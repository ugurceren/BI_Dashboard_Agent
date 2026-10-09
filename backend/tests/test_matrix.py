"""Matris (pivot tablo) görseli: spec doğrulama, alan yerleştirme (x / y / columns_dim → rows / values / columnDim),
sütun boyutu kuralı ve varsayılan boyut."""

import pytest

from app.harness.session import Session
from app.harness.tools import ToolContext, h_add_visual, h_create_report_spec, h_update_visual
from app.spec.models import Dataset, DatasetField, ReportSpec, semantic_errors


def _prof(rows, **cols):
    return {"row_count": rows, "columns": cols}


FIELDS = [DatasetField(name="region"), DatasetField(name="branch"), DatasetField(name="year_month"),
          DatasetField(name="collateral_amount", type="number", format="currency"), DatasetField(name="loan_count", type="number")]


@pytest.fixture()
def ctx(services):
    ds = [Dataset(id="collateral", sql="SELECT 1", fields=FIELDS),
          Dataset(id="daily", sql="SELECT 1", fields=[DatasetField(name="region"), DatasetField(name="day"),
                                                      DatasetField(name="amount", type="number")])]
    s = Session(phase="design", datasets=ds)
    s.dataset_profiles = {
        "collateral": _prof(180, region={"type": "string", "distinct": 5, "top": ["Marmara", "Ege"]},
                            branch={"type": "string", "distinct": 30, "top": ["Kadıköy", "Bornova"]},
                            year_month={"type": "string", "distinct": 6, "top": ["2026-04", "2026-05"]},
                            collateral_amount={"type": "number", "min": 10, "max": 900},
                            loan_count={"type": "number", "min": 1, "max": 90}),
        "daily": _prof(400, region={"type": "string", "distinct": 5, "top": ["Marmara"]},
                       day={"type": "date", "distinct": 90, "top": ["2026-01-01", "2026-01-02"]},
                       amount={"type": "number", "min": 1, "max": 9}),
    }
    return ToolContext(s, services)


def _spec(ctx, *visuals):
    r = h_create_report_spec(ctx, {"spec": {"title": "T", "visuals": list(visuals)}})
    assert r.ok, r.content
    return {v.id: v for v in ctx.session.spec.visuals}, r


MATRIX = {"id": "m", "type": "matrix", "title": "Şube × Ay Teminat", "datasetId": "collateral",
          "encoding": {"rows": ["region", "branch"], "columnDim": "year_month", "values": ["collateral_amount"]},
          "options": {"rowTotals": True, "columnTotals": True, "subtotals": True, "aggregate": "sum"}}


def test_matrix_spec_is_valid_and_gets_default_size_and_currency(ctx):
    vs, r = _spec(ctx, MATRIX)
    m = vs["m"]
    assert m.type == "matrix"
    assert m.encoding.rows == ["region", "branch"] and m.encoding.columnDim == "year_month"
    assert m.encoding.values == ["collateral_amount"]
    assert m.options.rowTotals and m.options.columnTotals and m.options.subtotals
    assert (m.position.w, m.position.h) == (12, 5)
    assert m.options.currency     # tutar ölçüsü: kurum varsayılan para birimi
    assert "KURAL" not in str(r.content)


def test_matrix_semantic_errors():
    base = {"title": "T", "datasets": [{"id": "d", "sql": "SELECT 1", "fields": [f.model_dump() for f in FIELDS]}]}

    def errs(enc, **opts):
        spec = ReportSpec.model_validate({**base, "visuals": [
            {"id": "m", "type": "matrix", "datasetId": "d", "encoding": enc, "options": opts, "position": {"x": 0, "y": 0, "w": 12, "h": 5}}]})
        return " | ".join(semantic_errors(spec))

    assert errs({"rows": ["region"], "values": ["collateral_amount"]}) == ""          # sütun boyutu isteğe bağlı
    assert "encoding.rows gerekli" in errs({"values": ["collateral_amount"]})
    assert "encoding.values gerekli" in errs({"rows": ["region"]})
    assert "olmayan alan" in errs({"rows": ["region"], "columnDim": "olmayan", "values": ["collateral_amount"]})
    assert "en çok 2 satır" in errs({"rows": ["region", "branch", "year_month"], "values": ["collateral_amount"]})
    assert "hem satır" in errs({"rows": ["region", "year_month"], "columnDim": "year_month", "values": ["collateral_amount"]})
    # count toplulaştırması geçerli
    assert errs({"rows": ["region"], "values": ["loan_count"]}, aggregate="count") == ""


def test_matrix_fields_are_moved_from_xy_and_columns_dim_alias(ctx):
    # küçük modeller x / y / series ya da columns_dim yazıyor: alanlar matrisin beklediği yere taşınır, "desteklenmeyen" sayılmaz
    vs, r = _spec(ctx, {"id": "a", "type": "matrix", "title": "A", "datasetId": "collateral",
                        "encoding": {"x": "region", "y": ["collateral_amount", "loan_count"], "series": "year_month"}},
                  {"id": "b", "type": "matrix", "title": "B", "datasetId": "collateral",
                   "encoding": {"rows": "region", "columns_dim": "year_month", "values": "collateral_amount"}})
    a, b = vs["a"].encoding, vs["b"].encoding
    assert a.rows == ["region"] and a.values == ["collateral_amount", "loan_count"] and a.columnDim == "year_month"
    assert a.x is None and a.y is None and a.series is None
    assert b.rows == ["region"] and b.columnDim == "year_month" and b.values == ["collateral_amount"]
    assert "UYGULANMADI" not in str(r.content)


def test_matrix_too_many_columns_rule(ctx):
    vs, r = _spec(ctx, {"id": "m", "type": "matrix", "title": "Günlük", "datasetId": "daily",
                        "encoding": {"rows": ["region"], "columnDim": "day", "values": ["amount"]}})
    assert vs["m"].options.maxColumns == 12
    assert "KURAL 'm'" in str(r.content) and "son 12 dönem" in str(r.content)
    # açıkça verilen sınıra dokunulmaz
    vs, _ = _spec(ctx, {"id": "m", "type": "matrix", "title": "Günlük", "datasetId": "daily",
                        "encoding": {"rows": ["region"], "columnDim": "day", "values": ["amount"]}, "options": {"maxColumns": 6}})
    assert vs["m"].options.maxColumns == 6


def test_matrix_add_and_change_from_table(ctx):
    _spec(ctx, {"id": "t", "type": "table", "title": "Tablo", "datasetId": "collateral"})
    r = h_add_visual(ctx, {"visual": MATRIX})
    assert r.ok, r.content
    r = h_update_visual(ctx, {"id": "t", "changes": {"type": "matrix", "encoding": {
        "rows": ["branch"], "columnDim": "year_month", "values": ["loan_count"]}, "options": {"aggregate": "count"}}})
    assert r.ok, r.content
    t = next(v for v in ctx.session.spec.visuals if v.id == "t")
    assert t.type == "matrix" and t.encoding.rows == ["branch"] and t.options.aggregate == "count"
