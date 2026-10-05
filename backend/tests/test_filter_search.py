"""Dashboard hazırken 'filtre koy': önce dashboard'un kullandığı tablolarda aranır, bulunamazsa sözlükte;
her aday için filtrenin uygulanacağı / uygulanamayacağı görseller verilir."""

import pytest

from app.dictionary.repository import DDColumn, DDTable
from app.harness.session import Session
from app.harness.tools import ToolContext, h_create_report_spec, h_find_filter_column, tools_for
from app.spec.models import Dataset, DatasetField
from tests.test_harness import KPI_SQL, REGION_SQL


@pytest.fixture()
def ctx(services):
    ds = [Dataset(id="kpi", sql=KPI_SQL, fields=[DatasetField(name="sales_amount", type="number"),
                                                 DatasetField(name="order_count", type="number")]),
          Dataset(id="region", sql=REGION_SQL, fields=[DatasetField(name="region"), DatasetField(name="sales_amount", type="number")])]
    c = ToolContext(Session(phase="design", datasets=ds), services)
    assert h_create_report_spec(c, {"spec": {"title": "Satış", "visuals": [
        {"id": "k", "type": "kpi", "title": "Satış Tutarı", "datasetId": "kpi", "encoding": {"value": "sales_amount"}},
        {"id": "r", "type": "bar", "title": "Bölgeler", "datasetId": "region", "encoding": {"x": "region", "y": ["sales_amount"]}},
        {"id": "x", "type": "text", "title": "Not", "options": {"text": "açıklama"}}]}}).ok
    return c


def test_found_in_dashboard_tables_first(ctx):
    r = h_find_filter_column(ctx, {"query": "bölge"})
    assert r.ok and r.content["source"] == "dashboard"
    top = r.content["candidates"][0]
    assert top["table"] == "dbo.DimSalesTerritory"                        # dashboard'un okuduğu tablo
    assert set(top["applies_to_visuals"]) == {"Satış Tutarı", "Bölgeler"} and top["not_applied_visuals"] == []


def test_falls_back_to_dictionary_with_reach(ctx):
    r = h_find_filter_column(ctx, {"query": "ürün kategorisi"})
    assert r.ok and r.content["source"] == "sözlük" and "Dashboard'un tablolarında bulunamadı" in r.content["note"]
    top = r.content["candidates"][0]
    assert top["table"] == "dbo.DimProductCategory"
    assert set(top["applies_to_visuals"]) == {"Satış Tutarı", "Bölgeler"}  # ilişkiler: kategori → ürün → satış
    assert not any("key" in c["column"].lower() for c in r.content["candidates"])   # vekil anahtarlar dilimleyici değil


def test_view_columns_without_roles_are_candidates(ctx, services):
    """EDWDM gibi: sözlükte rol yok, view'ın metin kolonu yine aday; ilişkisi olmayan görsellere uygulanamadığı söylenir."""
    dd = services.dictionary
    v = DDTable("clt.vbranch", "Şube Görünümü", "", "Kredi", "", 10, display_name="CLT.vBranch")
    v.columns = [DDColumn(v.name, n.lower(), bn, "", typ, "attribute", None, [], pii, "", display_name=n)
                 for n, bn, typ, pii in [("BranchName", "Şube Adı", "nvarchar", False), ("Amount", "Tutar", "decimal", False),
                                         ("CustomerName", "Müşteri Adı", "nvarchar", True)]]
    dd.tables[v.name] = v
    try:
        r = h_find_filter_column(ctx, {"query": "şube"})
        top = r.content["candidates"][0]
        assert (top["table"], top["column"]) == ("CLT.vBranch", "BranchName") and r.content["source"] == "sözlük"
        assert set(top["not_applied_visuals"]) == {"Satış Tutarı", "Bölgeler"}  # ilişki yok: uygulanmaz (söylenir)
        assert all(c["column"] not in ("Amount", "CustomerName") for c in r.content["candidates"])   # tutar / PII değil
    finally:
        dd.tables.pop(v.name, None)


def test_nothing_found_and_tool_registered(ctx):
    r = h_find_filter_column(ctx, {"query": "zzqqxx"})
    assert r.ok and r.content["candidates"] == [] and "sor" in r.content["note"]
    assert "find_filter_column" in {t.name for t in tools_for("design")}


# ------------------------------------------------------------------ ilişki yoksa yedek yollar (EDWDM view'ları)
@pytest.fixture()
def views(services):
    dd = services.dictionary

    def mk(name, cols, snap=False):
        t = DDTable(name.lower(), name, "", "Kredi", "", 10, display_name=name)
        t.columns = [DDColumn(t.name, c.lower(), c, "", typ, "attribute", None, [], False, "", display_name=c) for c, typ in cols]
        t.snapshot_date = "DataDate" if snap else ""
        dd.tables[t.name] = t
        return t
    made = [mk("CLT.vBranch", [("BranchName", "nvarchar"), ("Name", "nvarchar"), ("CustomerPartyId", "int"), ("DataDate", "date")], True),
            mk("CLT.vLoan", [("CustomerPartyId", "int"), ("Amount", "decimal"), ("Name", "nvarchar"), ("DataDate", "date")], True),
            mk("CLT.vCard", [("CustomerPartyId", "int"), ("BranchName", "nvarchar"), ("Limit", "decimal")]),
            mk("CLT.vOther", [("OtherId", "int"), ("Amount", "decimal")])]
    yield services
    for t in made:
        dd.tables.pop(t.name, None)


def _apply(svc, sql, table, col):
    from app.data.model_filters import ModelFilter, ModelFilterEngine
    return ModelFilterEngine(svc.dictionary, "tsql").apply(sql, [ModelFilter(table, col, ["Merkez"])])


def test_same_named_column_applies_directly(views):
    sql, applied = _apply(views, "SELECT SUM(c.Limit) AS l FROM CLT.vCard c WHERE c.CustomerPartyId > 0", "clt.vbranch", "branchname")
    assert applied == ["clt.vbranch.branchname"] and "c.BranchName IN (N'Merkez')" in sql and "EXISTS" not in sql


def test_shared_key_bridge_with_same_day(views):
    sql, applied = _apply(views, "SELECT SUM(l.Amount) AS a FROM CLT.vLoan l WHERE l.DataDate = '2026-09-30'", "clt.vbranch", "branchname")
    assert applied == ["clt.vbranch.branchname"]
    assert "EXISTS(SELECT 1 FROM CLT.vBranch AS _mfb WHERE _mfb.CustomerPartyId = l.CustomerPartyId" in sql
    assert "_mfb.BranchName IN (N'Merkez')" in sql and "_mfb.DataDate = l.DataDate" in sql   # iki görüntü: aynı gün
    assert views.validator.validate(sql, views.policy("standart")).ok


def test_generic_name_not_matched_and_unreachable_not_applied(views):
    sql, applied = _apply(views, "SELECT SUM(l.Amount) AS a FROM CLT.vLoan l WHERE l.DataDate = '2026-09-30'", "clt.vbranch", "name")
    assert "l.Name IN" not in sql and "EXISTS" in sql                      # 'Name' genel ad: köprüyle uygulanır
    _, applied = _apply(views, "SELECT SUM(o.Amount) AS a FROM CLT.vOther o WHERE o.OtherId > 0", "clt.vbranch", "branchname")
    assert applied == []                                                    # ortak kolon / anahtar yok: Filtre dışı


def test_ignore_filters_only_when_set(views, monkeypatch):
    from types import SimpleNamespace

    import app.main as m
    from app.spec.models import ReportSpec
    m.state.services = views
    sent = []
    monkeypatch.setattr(m, "_dataset_payload", lambda sql, role: sent.append(sql) or {"columns": [], "rows": []})
    ds = [{"id": "a", "sql": "SELECT SUM(c.Limit) AS l FROM CLT.vCard c WHERE c.CustomerPartyId > 0", "fields": [{"name": "l", "type": "number"}]},
          {"id": "b", "sql": "SELECT SUM(c.Limit) AS l2 FROM CLT.vCard c WHERE c.CustomerPartyId > 0", "fields": [{"name": "l2", "type": "number"}]}]
    spec = ReportSpec.model_validate({"title": "T", "datasets": ds, "visuals": [
        {"id": "va", "type": "kpi", "title": "A", "datasetId": "a", "encoding": {"value": "l"}, "position": {"x": 0, "y": 0, "w": 3, "h": 2}},
        {"id": "vb", "type": "kpi", "title": "B", "datasetId": "b", "encoding": {"value": "l2"}, "options": {"ignoreFilters": True},
         "position": {"x": 3, "y": 0, "w": 3, "h": 2}}]})
    sess = SimpleNamespace(datasets=[], spec=spec, user_role="standart")
    r = m._dashboard_data(sess, [{"key": "clt.vbranch.branchname", "values": ["Merkez"]}])
    assert r["applied"].get("a") == ["clt.vbranch.branchname"] and not r["applied"].get("b")
    assert "Merkez" in sent[0] and "Merkez" not in sent[1]
