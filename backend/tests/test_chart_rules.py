"""Grafik türü kuralları: veri profiline göre uygulanır (pasta ≤8 dilim, çok kategoride yatay / ilk 15 çubuk,
çizgi zaman ekseni ister, gösterge hedef ister …)."""

import pytest

from app.harness.session import Session
from app.harness.tools import ToolContext, h_create_report_spec, h_update_visual
from app.spec.models import Dataset, DatasetField


def _prof(rows, **cols):
    return {"row_count": rows, "columns": cols}


@pytest.fixture()
def ctx(services):
    ds = [Dataset(id="country", sql="SELECT 1", fields=[DatasetField(name="country"), DatasetField(name="amount", type="number")]),
          Dataset(id="cat", sql="SELECT 1", fields=[DatasetField(name="category"), DatasetField(name="amount", type="number")]),
          Dataset(id="month", sql="SELECT 1", fields=[DatasetField(name="year_month"), DatasetField(name="amount", type="number")]),
          Dataset(id="profit", sql="SELECT 1", fields=[DatasetField(name="region"), DatasetField(name="profit", type="number")])]
    s = Session(phase="design", datasets=ds)
    s.dataset_profiles = {
        "country": _prof(30, country={"type": "string", "distinct": 30, "top": ["United States", "Germany", "France"]},
                         amount={"type": "number", "min": 10, "max": 900}),
        "cat": _prof(4, category={"type": "string", "distinct": 4, "top": ["Bikes", "Accessories"]},
                     amount={"type": "number", "min": 5, "max": 90}),
        "month": _prof(36, year_month={"type": "string", "distinct": 36, "top": ["2013-01", "2013-02"]},
                       amount={"type": "number", "min": 1, "max": 9}),
        "profit": _prof(5, region={"type": "string", "distinct": 5, "top": ["A", "B"]},
                        profit={"type": "number", "min": -40, "max": 90}),
    }
    return ToolContext(s, services)


def _spec(ctx, *visuals):
    r = h_create_report_spec(ctx, {"spec": {"title": "T", "visuals": list(visuals)}})
    assert r.ok, r.content
    return {v.id: v for v in ctx.session.spec.visuals}, r


def test_pie_with_many_slices_becomes_sorted_horizontal_bar(ctx):
    vs, r = _spec(ctx, {"id": "p", "type": "pie", "title": "Ülke", "datasetId": "country",
                        "encoding": {"category": "country", "value": "amount"}})
    p = vs["p"]
    assert p.type == "bar" and p.encoding.x == "country" and p.encoding.y == ["amount"]
    assert p.options.horizontal is True and p.options.sort == "desc"
    assert "KURAL" in str(r.content)


def test_small_pie_is_kept_but_negative_values_are_not(ctx):
    vs, _ = _spec(ctx, {"id": "d", "type": "donut", "title": "Kategori", "datasetId": "cat",
                        "encoding": {"category": "category", "value": "amount"}},
                  {"id": "n", "type": "pie", "title": "Kâr", "datasetId": "profit", "encoding": {"category": "region", "value": "profit"}})
    assert vs["d"].type == "donut" and vs["n"].type == "bar"


def test_bar_with_many_categories_is_horizontal_and_limited(ctx):
    vs, _ = _spec(ctx, {"id": "b", "type": "bar", "title": "Ülke", "datasetId": "country", "encoding": {"x": "country", "y": ["amount"]}},
                  {"id": "c", "type": "bar", "title": "Kategori", "datasetId": "cat", "encoding": {"x": "category", "y": ["amount"]}})
    b, c = vs["b"].options, vs["c"].options
    assert (b.horizontal, b.limit, b.sort) == (True, 15, "desc")
    assert c.horizontal is None and c.limit is None                      # 4 kısa kategori: dikey kalır


def test_time_axis_bar_is_not_limited_and_line_needs_time(ctx):
    vs, r = _spec(ctx, {"id": "m", "type": "bar", "title": "Ay", "datasetId": "month", "encoding": {"x": "year_month", "y": ["amount"]}},
                  {"id": "l", "type": "line", "title": "Kategori", "datasetId": "cat", "encoding": {"x": "category", "y": ["amount"]}})
    assert vs["m"].options.limit is None and vs["m"].options.horizontal is None
    assert "'l': x ekseni (category) zaman değil" in str(r.content)


def test_gauge_without_target_is_reported(ctx):
    _, r = _spec(ctx, {"id": "g", "type": "gauge", "title": "G", "datasetId": "cat", "encoding": {"value": "amount"}})
    assert "hedef değer ister" in str(r.content)


def test_rules_apply_when_llm_changes_type(ctx):
    _spec(ctx, {"id": "b", "type": "bar", "title": "Ülke", "datasetId": "country", "encoding": {"x": "country", "y": ["amount"]}})
    r = h_update_visual(ctx, {"id": "b", "changes": {"type": "donut", "encoding": {"category": "country", "value": "amount", "x": None, "y": None}}})
    assert r.ok, r.content
    assert ctx.session.spec.visuals[0].type == "bar" and "dilim okunmaz" in str(r.content)


def test_mixed_unit_measures_are_not_stacked(ctx):
    ctx.session.datasets.append(Dataset(id="m2", sql="SELECT 1", fields=[
        DatasetField(name="year_month"), DatasetField(name="amount", type="number", format="currency"),
        DatasetField(name="orders", type="number", format="number")]))
    ctx.session.dataset_profiles["m2"] = _prof(12, year_month={"type": "string", "distinct": 12, "top": ["2013-01"]},
                                               amount={"type": "number", "min": 1}, orders={"type": "number", "min": 1})
    vs, r = _spec(ctx, {"id": "a", "type": "area", "title": "Trend", "datasetId": "m2",
                        "encoding": {"x": "year_month", "y": ["amount", "orders"]}, "options": {"stacked": True}})
    assert vs["a"].options.stacked is None and "yığılmaz" in str(r.content)


def test_xy_encodings_are_moved_for_part_and_heatmap_types(ctx):
    """Model huni / ısı haritasına x / y yazdı: alanlar category / value'ya taşınır, araç reddetmez (döngü olmaz)."""
    ctx.session.datasets.append(Dataset(id="cm", sql="SELECT 1", fields=[
        DatasetField(name="year_month"), DatasetField(name="channel"), DatasetField(name="amount", type="number")]))
    vs, r = _spec(ctx,
                  {"id": "f", "type": "funnel", "title": "Huni", "datasetId": "cat", "encoding": {"x": "category", "y": "amount"}},
                  {"id": "h", "type": "heatmap", "title": "Isı", "datasetId": "cm",
                   "encoding": {"x": "year_month", "y": "channel", "value": "amount"}})
    assert (vs["f"].encoding.category, vs["f"].encoding.value) == ("category", "amount")
    assert (vs["h"].encoding.x, vs["h"].encoding.category, vs["h"].encoding.value) == ("year_month", "channel", "amount")


def test_time_axis_is_not_sorted_by_value(ctx):
    vs, r = _spec(ctx, {"id": "m", "type": "bar", "title": "Ay", "datasetId": "month",
                        "encoding": {"x": "year_month", "y": ["amount"]}, "options": {"sort": "desc"}})
    assert vs["m"].options.sort is None and "sıralanmaz" in str(r.content)


def test_dataset_and_visual_ids_are_not_searched_as_tables(ctx):
    """Model kayıtlı dataset / görsel kimliklerini tablo sanıp arıyordu: doğru araca yönlendirilir."""
    from app.harness.tools import h_get_table_details, h_run_sql, h_search_dictionary
    _spec(ctx, {"id": "kpi_sales", "type": "kpi", "title": "Tutar", "datasetId": "cat", "encoding": {"value": "amount"}})
    r = h_get_table_details(ctx, {"tables": ["dbo.country"]})
    assert not r.ok and "DATASET" in r.content["error"] and "datasetId='country'" in r.content["error"]
    r = h_search_dictionary(ctx, {"query": "kpi sales"})
    assert not r.ok and "GÖRSEL" in r.content["error"]
    r = h_run_sql(ctx, {"sql": "SELECT TOP 5 * FROM dbo.month"})
    assert not r.ok and "DATASET" in r.content["hint"]


def test_top_n_title_sets_limit_and_sort(ctx):
    vs, r = _spec(ctx, {"id": "t", "type": "table", "title": "En Çok Satan 10 Ülke", "datasetId": "country",
                        "encoding": {"columns": ["country", "amount"]}},
                  {"id": "b", "type": "bar", "title": "İlk 5 kategori", "datasetId": "cat", "encoding": {"x": "category", "y": ["amount"]}},
                  {"id": "x", "type": "text", "title": "Not", "datasetId": "", "options": {"text": "Açıklama"}})
    assert (vs["t"].options.limit, vs["t"].options.sort) == (10, "desc")
    assert vs["b"].options.limit == 5 and vs["x"].datasetId is None
