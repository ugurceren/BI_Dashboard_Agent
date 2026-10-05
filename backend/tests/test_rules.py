"""Veritabanına özgü kurum kuralları (config/data_rules/<DB>.md): yerel LLM'in talimatına aşamaya göre eklenir."""

import pytest

from app.dictionary.repository import DDColumn, DDTable
from app.harness.phases import system_prompt
from app.harness.rules import RULES_DIR, access_levels, parse_sections, phase_rules, rules_file
from app.harness.session import Session


def test_edwdm_rules_file_has_all_phase_sections():
    f = rules_file("edwdm")                                     # büyük / küçük harf duyarsız
    assert f is not None and f.parent == RULES_DIR
    sec = parse_sections(f.read_text(encoding="utf-8"))
    assert set(sec) == {"all", "requirements", "data", "design"}
    assert "Masked" in sec["all"][0] and "DataDate" in sec["all"][0]
    assert "itibarıyla" in sec["requirements"][0] and "EOMONTH" in sec["data"][0]
    assert not any("<!--" in x for v in sec.values() for x in v)  # açıklama yorumu talimata girmez


def test_parse_sections_maps_titles_to_phases():
    sec = parse_sections("# Başlık\nönsöz atılır\n## Genel\nA\n## İhtiyaç\nB\n## Veri keşfi\nC\n## Tasarım\nD\n## Bilinmeyen\nE")
    assert {k: [x.split("\n", 1)[1] for x in v] for k, v in sec.items()} == {
        "all": ["A"], "requirements": ["B"], "data": ["C"], "design": ["D"]}


@pytest.fixture()
def edwdm(services):
    dd = services.dictionary
    base = DDTable("clt.vguarantee", "Garanti", "", "Kredi", "", 10, display_name="CLT.vGuarantee", can_select=False)
    masked = DDTable("clt.vguaranteemasked", "Garanti (maskeli)", "", "Kredi", "", 10, display_name="CLT.vGuaranteeMasked",
                     variant_of=base.name, can_select=True)
    pm = DDTable("clt.vguaranteepersonnelmasked", "Garanti (personel maskeli)", "", "Kredi", "", 10,
                 display_name="CLT.vGuaranteePersonnelMasked", variant_of=base.name, can_select=False)
    for t in (base, masked, pm):
        t.columns = [DDColumn(t.name, "amount", "Tutar", "", "decimal", "measure", "sum", [], False, "")]
        dd.tables[t.name] = t
    old = dd.database
    dd.database = "EDWDM"
    yield services
    dd.database = old
    for t in (base, masked, pm):
        dd.tables.pop(t.name, None)


def test_access_levels_reflect_user_permissions(edwdm):
    dd = edwdm.dictionary
    assert access_levels(dd, dd.usable) == [("Masked", 1)]     # ana view ve PersonnelMasked'e SELECT yok


def test_phase_rules_by_phase(edwdm):
    req = phase_rules(edwdm, Session(phase="requirements"))
    assert "Kurum veri kuralları — EDWDM" in req and "hangi tarih itibarıyla" in req
    assert "Veri hazırlığı" not in req and "- Masked: 1 view" in req
    data = phase_rules(edwdm, Session(phase="data"))
    assert "### Veri hazırlığı" in data and "a.DataDate = b.DataDate" in data and "- Masked: 1 view" in data
    assert "İhtiyaç analizi" not in data
    design = phase_rules(edwdm, Session(phase="design"))
    assert "### Tasarım" in design and "Erişilebilir yetki seviyeleri" not in design
    sp = system_prompt(Session(phase="data"), "tsql", rules=data)
    assert sp.index("VERİ KEŞFİ") < sp.index("Kurum veri kuralları")  # faz talimatından sonra, durumdan önce


@pytest.mark.parametrize("db", ["AdventureWorksDW2025", None])
def test_no_rules_outside_edwdm(services, db):
    dd = services.dictionary
    old, dd.database = dd.database, db
    try:
        assert phase_rules(services, Session(phase="data")) == ""
        assert "DataDate" not in system_prompt(Session(phase="data"), "tsql")
    finally:
        dd.database = old


def test_agent_sends_rules_to_llm(edwdm, settings):
    """Yerel LLM'e giden sistem mesajında EDWDM kuralları var."""
    from app.harness.agent import Agent
    from app.harness.session import SessionStore
    from app.llm.gateway import AssistantTurn
    from tests.conftest import FakeLLM

    llm = FakeLLM([AssistantTurn("Hangi tarih itibarıyla görmek istersiniz?")], settings)
    store = SessionStore(settings.sessions_dir)
    s = store.create("standart")
    agent = Agent(llm, edwdm, store)
    list(agent.run_turn(s.id, "Teminat tutarlarını görmek istiyorum", None))
    sys_msg = llm.calls[0]["messages"][0]["content"]
    assert "Kurum veri kuralları — EDWDM" in sys_msg and "Masked" in sys_msg


def test_sql_standards_go_to_data_phase_only(edwdm):
    """Veri Ambarı Kullanım Kılavuzu bölüm 8: veri hazırlığı aşamasında LLM'e verilir."""
    data = phase_rules(edwdm, Session(phase="data"))
    for needle in ("WHERE koşulu zorunludur", "PartyId", "FECId = '1'", "WITH (NOLOCK)", "SELECT * kullanma",
                   "COUNT(1)", "UNION ALL", "önce bütün INNER JOIN", "JOIN kolonlarında fonksiyon", "COR.vCalendar"):
        assert needle in data, needle
    assert "SQL kullanım standartları" not in phase_rules(edwdm, Session(phase="requirements"))


@pytest.mark.parametrize("db,dialect,expected", [("EDWDM", "tsql", True), ("AdventureWorksDW2025", "tsql", False),
                                                 (None, "tsql", False), ("EDWDM", "duckdb", False)])
def test_generated_sql_gets_nolock_only_in_edwdm(services, monkeypatch, db, dialect, expected):
    """Uygulamanın ürettiği filtre alt sorgularına WITH (NOLOCK) yalnız EDWDM'de (SQL Server) eklenir."""
    from app.data.model_filters import ModelFilter, ModelFilterEngine
    dd = services.dictionary
    monkeypatch.setattr(dd, "database", db)
    monkeypatch.setattr(dd._data_connector, "dialect", dialect, raising=False)
    eng = ModelFilterEngine(dd, "tsql")
    sql, _ = eng.apply("SELECT SUM(f.SalesAmount) FROM dbo.FactInternetSales f WITH (NOLOCK) WHERE f.OrderDateKey > 1",
                       [ModelFilter("dbo.dimsalesterritory", "salesterritoryregion", ["Europe"])])
    sub = sql[sql.index("EXISTS"):]
    assert ("WITH (NOLOCK)" in sub) is expected
    assert services.validator.validate(sql, services.policy("standart")).ok


@pytest.fixture()
def edwdm_db(services, monkeypatch):
    monkeypatch.setattr(services.dictionary, "database", "EDWDM")
    return services


def test_validator_adds_nolock_everywhere(edwdm_db):
    """Kullanıcı / LLM yazmasa da her tablo ve view referansına WITH (NOLOCK) eklenir: JOIN, alt sorgu, CTE içi;
    CTE adına eklenmez, zaten yazılmışsa tekrarlanmaz."""
    pol = edwdm_db.policy("standart")
    sql = ("WITH s AS (SELECT f.SalesTerritoryKey, f.SalesAmount FROM dbo.FactInternetSales f WHERE f.OrderDateKey > 1) "
           "SELECT t.SalesTerritoryRegion, SUM(s.SalesAmount) AS amt FROM s "
           "JOIN dbo.DimSalesTerritory t WITH (NOLOCK) ON t.SalesTerritoryKey = s.SalesTerritoryKey "
           "WHERE t.SalesTerritoryKey IN (SELECT SalesTerritoryKey FROM dbo.DimSalesTerritory WHERE SalesTerritoryGroup = 'Europe') "
           "GROUP BY t.SalesTerritoryRegion")
    r = edwdm_db.validator.validate(sql, pol)
    assert r.ok, r.errors
    out = r.sql.upper()
    assert out.count("WITH (NOLOCK)") == 3                       # CTE içi + JOIN (tekrar yok) + alt sorgu
    assert "FROM S WITH" not in out                               # CTE adına eklenmez
    assert edwdm_db.validator.validate(r.sql, pol).sql.upper().count("WITH (NOLOCK)") == 3   # ikinci geçişte değişmez


def test_nolock_in_query_console_and_other_hints(edwdm_db):
    pol = edwdm_db.policy("standart")
    r = edwdm_db.validator.validate("SELECT TOP 5 EnglishProductName FROM dbo.DimProduct WHERE ProductKey > 0", pol,
                                    strict_joins=False, autofix=False)      # Sorgu Çalıştır ekranı
    assert r.ok and "WITH (NOLOCK)" in r.sql.upper()
    r2 = edwdm_db.validator.validate("SELECT ProductKey FROM dbo.DimProduct WITH (INDEX(1)) WHERE ProductKey > 0", pol)
    assert r2.ok and "NOLOCK" in r2.sql.upper() and "INDEX" in r2.sql.upper()


def test_no_nolock_outside_edwdm(services):
    r = services.validator.validate("SELECT TOP 5 EnglishProductName FROM dbo.DimProduct WHERE ProductKey > 0",
                                    services.policy("standart"))
    assert r.ok and "NOLOCK" not in r.sql.upper()                  # veritabanı adı bilinmiyor / EDWDM değil


def test_query_endpoint_executes_with_nolock(edwdm_db, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as m
    m.state.services = edwdm_db
    c = TestClient(m.app)
    r = c.post("/api/query", json={"sql": "SELECT TOP 5 EnglishProductName FROM dbo.DimProduct WHERE ProductKey > 0"})
    assert r.status_code == 200, r.text
    assert "WITH (NOLOCK)" in edwdm_db.connector.executed[-1].upper()
