"""Günlük anlık görüntü (DataDate) koruması: EDWDM view'ları takvimle joinlendiği için her kayıt her gün tekrarlanır;
gün seçilmeden SUM / COUNT, MAX / MIN / AVG, TOP / detay sorguları ve tarih eşlenmeden join reddedilir; doğru kalıplar geçer."""

import pytest

from app.data.validator import RolePolicy
from app.dictionary.repository import DDColumn, DDTable

POL = RolePolicy("standart", ["*"])


def _col(t, n, typ, role="attribute"):
    return DDColumn(t, n.lower(), n, "", typ, role, None, [], False, "", display_name=n)


@pytest.fixture()
def snap(services):
    dd = services.dictionary
    g = DDTable("clt.vguarantee", "Garantiler", "", "Kredi", "", 1000, display_name="CLT.vGuarantee")
    g.columns = [_col(g.name, "DataDate", "date"), _col(g.name, "CustomerId", "int"), _col(g.name, "Amount", "decimal", "measure"),
                 _col(g.name, "Branch", "nvarchar", "dimension")]
    c = DDTable("clt.vcollateral", "Teminatlar", "", "Kredi", "", 1000, display_name="CLT.vCollateral")
    c.columns = [_col(c.name, "DataDate", "date"), _col(c.name, "CustomerId", "int"), _col(c.name, "Value", "decimal", "measure")]
    dd.tables[g.name], dd.tables[c.name] = g, c
    assert dd.mark_snapshots() >= 2 and g.snapshot_date == "DataDate"
    yield services
    dd.tables.pop(g.name, None)
    dd.tables.pop(c.name, None)


RECENT = "DataDate >= DATEADD(day, -2, CAST(GETDATE() AS DATE))"      # veri ambarı T-1
LAST = f"(SELECT MAX(DataDate) FROM CLT.vGuarantee WHERE {RECENT})"


@pytest.mark.parametrize("sql", [
    f"SELECT SUM(g.Amount) FROM CLT.vGuarantee g WHERE g.DataDate = {LAST}",                     # son gün
    f"SELECT Branch, COUNT(*) FROM CLT.vGuarantee WHERE DataDate = {LAST} GROUP BY Branch",       # tablo adı yok, tek tablo
    "SELECT g.DataDate, SUM(g.Amount) FROM CLT.vGuarantee g GROUP BY g.DataDate",                 # günlük seri
    "SELECT CAST(g.DataDate AS DATE) d, SUM(g.Amount) FROM CLT.vGuarantee g GROUP BY CAST(g.DataDate AS DATE)",
    "SELECT YEAR(g.DataDate) y, MONTH(g.DataDate) m, SUM(g.Amount) FROM CLT.vGuarantee g "
    "WHERE g.DataDate = EOMONTH(g.DataDate) GROUP BY YEAR(g.DataDate), MONTH(g.DataDate)",       # ay sonu trendi
    "SELECT SUM(g.Amount) FROM CLT.vGuarantee g WHERE g.DataDate IN ('2026-09-30', '2026-08-31') GROUP BY g.DataDate",
    f"WITH son AS (SELECT * FROM CLT.vGuarantee WHERE DataDate = {LAST}) SELECT SUM(Amount) FROM son",
    f"SELECT SUM(g.Amount), SUM(c.Value) FROM CLT.vGuarantee g JOIN CLT.vCollateral c "
    f"ON c.CustomerId = g.CustomerId AND c.DataDate = g.DataDate WHERE g.DataDate = {LAST}",      # tarih eşli join
])
def test_correct_snapshot_queries_pass(snap, sql):
    r = snap.validator.validate(sql, POL)
    assert r.ok, r.errors


@pytest.mark.parametrize("sql,needle", [
    ("SELECT SUM(Amount) FROM CLT.vGuarantee", "gün sayısıyla çarpılmış"),
    ("SELECT Branch, COUNT(*) FROM CLT.vGuarantee GROUP BY Branch", "gün sayısıyla çarpılmış"),
    ("SELECT MONTH(g.DataDate), SUM(g.Amount) FROM CLT.vGuarantee g GROUP BY MONTH(g.DataDate)", "ayın TÜM günlerini"),
    ("SELECT SUM(g.Amount) FROM CLT.vGuarantee g WHERE g.DataDate BETWEEN '2026-01-01' AND '2026-09-30'", "tarih aralığı"),
    (f"SELECT SUM(g.Amount) FROM CLT.vGuarantee g JOIN CLT.vCollateral c ON c.CustomerId = g.CustomerId "
     f"WHERE g.DataDate = {LAST}", "gün × gün"),
])
def test_unsafe_snapshot_queries_rejected(snap, sql, needle):
    r = snap.validator.validate(sql, POL)
    assert not r.ok and any(needle in e for e in r.errors), r.errors
    assert any("DataDate" in e for e in r.errors)                         # düzeltme önerisi kolonla birlikte


@pytest.mark.parametrize("sql", [
    "SELECT AVG(Amount) FROM CLT.vGuarantee",                             # tüm günler üzerinden ortalama
    "SELECT COUNT(DISTINCT CustomerId) FROM CLT.vGuarantee",
    "SELECT TOP 100 * FROM CLT.vGuarantee",                               # örnek satır bile: her gün tekrarlanır
    "SELECT TOP 100 MAX(Amount) FROM CLT.vGuarantee",                     # LLM'in attığı kalıp
    "SELECT TOP 100 Branch, MAX(Amount) FROM CLT.vGuarantee GROUP BY Branch",
    "SELECT DISTINCT Branch FROM CLT.vGuarantee",                         # değer listesi de tüm günleri tarar
    "SELECT Branch FROM CLT.vGuarantee GROUP BY Branch",
    "SELECT MAX(g.Amount) FROM CLT.vGuarantee g WHERE g.DataDate BETWEEN '2026-01-01' AND '2026-09-30'",
    "SELECT TOP 10 Branch, MAX(DataDate) FROM CLT.vGuarantee GROUP BY Branch",
])
def test_unpinned_non_additive_and_detail_queries_rejected(snap, sql):
    r = snap.validator.validate(sql, POL)
    assert not r.ok and any("günlük anlık görüntü" in e and "DataDate" in e for e in r.errors), r.errors


@pytest.mark.parametrize("sql", [
    f"SELECT MAX(DataDate) FROM CLT.vGuarantee WHERE {RECENT}",            # son günü bulmak: alt sınırlı tarih sorgusu
    f"SELECT MIN(g.DataDate), MAX(g.DataDate) FROM CLT.vGuarantee g WHERE g.{RECENT}",
    "SELECT MAX(DataDate) FROM CLT.vGuarantee WHERE DataDate >= DATEADD(day, -2, GETDATE())",   # kullanıcının yazdığı biçim
    "SELECT MAX(DataDate) FROM CLT.vGuarantee WHERE DataDate BETWEEN '2026-09-01' AND '2026-09-30'",
    f"SELECT DISTINCT TOP 30 DataDate FROM CLT.vGuarantee WHERE {RECENT} ORDER BY DataDate DESC",
    f"SELECT TOP 100 * FROM CLT.vGuarantee WHERE DataDate = {LAST}",      # örnek satır: tek gün
    f"SELECT TOP 100 Branch, MAX(Amount) FROM CLT.vGuarantee WHERE DataDate = {LAST} GROUP BY Branch",
    "SELECT g.DataDate, MAX(g.Amount) FROM CLT.vGuarantee g GROUP BY g.DataDate",
])
def test_pinned_or_date_only_queries_pass(snap, sql):
    r = snap.validator.validate(sql, POL)
    assert r.ok, r.errors


@pytest.mark.parametrize("sql", [
    "SELECT MAX(DataDate) FROM CLT.vGuarantee",                           # alt sınırsız: view'ın tamamı taranır
    "SELECT MIN(g.DataDate), MAX(g.DataDate) FROM CLT.vGuarantee g",
    "SELECT MAX(DataDate) FROM CLT.vGuarantee WHERE DataDate <= '2026-09-30'",   # yalnız üst sınır yetmez
    "SELECT SUM(Amount) FROM CLT.vGuarantee WHERE DataDate = (SELECT MAX(DataDate) FROM CLT.vGuarantee)",  # alt sorgu sınırsız
])
def test_unbounded_date_only_query_rejected(snap, sql):
    """Son günü bulan MIN / MAX da alt sınır ister (T-1 veri ambarı: DataDate >= GETDATE() - 2)."""
    r = snap.validator.validate(sql, POL)
    assert not r.ok and any("alt sınır" in e and "DATEADD(day, -2" in e for e in r.errors), r.errors


def test_period_average_only_warns(snap):
    """Dönem ortalaması (ör. aylık ortalama bakiye) anlamlı bir metrik: engellenmez, uyarılır."""
    r = snap.validator.validate("SELECT MONTH(g.DataDate) m, AVG(g.Amount) FROM CLT.vGuarantee g GROUP BY MONTH(g.DataDate)", POL)
    assert r.ok and any("dönem ortalaması" in w for w in r.warnings), r.warnings


def test_query_console_still_runs_but_warns(snap):
    r = snap.validator.validate("SELECT TOP 100 MAX(Amount) FROM CLT.vGuarantee", POL, strict_joins=False)
    assert r.ok and any("günlük anlık görüntü" in w for w in r.warnings)


def test_query_console_only_warns(snap):
    """Kullanıcının kendi sorgu ekranında (strict_joins=False) engellemez, uyarır."""
    r = snap.validator.validate("SELECT SUM(Amount) FROM CLT.vGuarantee", POL, strict_joins=False)
    assert r.ok and any("gün sayısıyla" in w for w in r.warnings)


def test_agent_is_told_about_snapshots(snap):
    d = snap.dictionary.table_details("clt.vguarantee", False)
    assert d["snapshot"]["date_column"] == "DataDate" and "MAX(DataDate)" in d["snapshot"]["rule"]
    hit = next(h for h in snap.dictionary.search("garantiler") if h["table"] == "clt.vguarantee")
    assert hit["snapshot_date"] == "DataDate"
    assert next(c for c in snap.dictionary.tables["clt.vguarantee"].columns if c.name == "datadate").role == "date"


def test_suggestions_for_snapshot_views(snap):
    """Anlık görüntü view'ları için öneri: son gün itibarıyla + ay sonu trendi (günleri toplayan 'aylık trend' değil)."""
    from app import suggestions as sugg
    items = [i for i in sugg.rule_suggestions(snap.dictionary, lambda t: t.name.startswith("clt."), limit=10)
             if i["table"] == "CLT.vGuarantee"]
    assert items and items[0]["snapshot"] and "son gün itibarıyla" in items[0]["text"] and "ay sonu" in items[0]["text"]
    assert "DataDate" not in items[0]["dims"]


# ------------------------------------------------------------------ veri tarihi ('itibarıyla') seçimi
KPI = (f"SELECT SUM(g.Amount) AS amount FROM CLT.vGuarantee g WHERE g.DataDate = {LAST}")
TREND = ("SELECT EOMONTH(g.DataDate) AS ay, SUM(g.Amount) AS amount FROM CLT.vGuarantee g "
         "WHERE g.DataDate = EOMONTH(g.DataDate) GROUP BY EOMONTH(g.DataDate)")


def test_as_of_rewrites_every_snapshot_block(snap):
    from app.data.model_filters import ModelFilterEngine
    eng = ModelFilterEngine(snap.dictionary, "tsql")
    assert eng.snapshot_tables(KPI) == ["clt.vguarantee"]
    sql, done = eng.as_of(KPI, "2026-06-30")
    assert done and sql.count("<= CAST('2026-06-30' AS DATE)") == 2     # dış sorgu + MAX alt sorgusu
    assert "GETDATE" not in sql and "DATEADD(day, -7, CAST('2026-06-30' AS DATE))" in sql   # bugüne göreli sınır → seçilen gün
    assert snap.validator.validate(sql, POL).ok                         # günlük anlık görüntü kontrolünden geçer
    t2, _ = eng.as_of(TREND, "2026-06-30")
    assert "<= CAST('2026-06-30' AS DATE)" in t2 and snap.validator.validate(t2, POL).ok
    plain, done = eng.as_of("SELECT COUNT(*) FROM dbo.DimProduct", "2026-06-30")
    assert not done and plain == "SELECT COUNT(*) FROM dbo.DimProduct"  # anlık görüntü olmayan dataset değişmez


def test_dashboard_data_and_filters_with_as_of(snap, monkeypatch):
    from types import SimpleNamespace

    import app.main as m
    from app.spec.models import Dataset

    m.state.services = snap
    sess = SimpleNamespace(datasets=[Dataset(id="kpi", sql=KPI), Dataset(id="urun", sql="SELECT EnglishProductName FROM dbo.DimProduct")],
                           spec=None, user_role="standart")
    seen = []
    monkeypatch.setattr(m, "_dataset_payload", lambda sql, role: seen.append(sql) or {"columns": ["max_d"], "rows": [["2026-10-04"]]})
    m._options_cache.clear()
    info = m._data_date_info(sess, "standart")
    assert info["column"] == "DataDate" and info["min"] is None and info["max"] == "2026-10-04"
    assert "DATEADD(day, -2" in seen[0] and "MIN(" not in seen[0]     # yalnız son günler, tüm geçmiş taranmaz
    seen.clear()
    r = m._dashboard_data(sess, [{"key": m.AS_OF_KEY, "values": ["2026-06-30"]}])
    assert r["as_of"] == "2026-06-30" and r["applied"]["kpi"] == [m.AS_OF_KEY] and "urun" not in r["applied"]
    assert "2026-06-30" in seen[0] and "2026-06-30" not in seen[1]
    import pytest as _pt
    from fastapi import HTTPException
    with _pt.raises(HTTPException):
        m._dashboard_data(sess, [{"key": m.AS_OF_KEY, "values": ["30.06.2026'; DROP"]}])


def test_filter_options_on_snapshot_read_latest_day(snap, monkeypatch):
    """Dilimleyici seçenekleri anlık görüntü view'ında son günden okunur (koruma kuralından geçer)."""
    import app.main as m
    monkeypatch.setattr(m.state, "services", snap, raising=False)
    m._options_cache.clear()
    seen = []
    monkeypatch.setattr(m, "_dataset_payload", lambda sql, role: seen.append(sql) or {"rows": [["Ankara"]]})
    assert m._filter_options("clt.vguarantee.branch", "standart") == ["Ankara"]
    assert "MAX(DataDate)" in seen[0] and snap.validator.validate(seen[0], POL).ok
