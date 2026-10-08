"""Dashboard sayfaları (Power BI gibi): LLM sayfa ekler / siler / görselleri taşır; her sayfanın kendi ızgarası var;
eski tek sayfalı raporlar değişmez."""

import pytest

from app.harness.session import Session
from app.harness.tools import (ToolContext, h_add_page, h_add_visual, h_create_report_spec, h_remove_page, h_update_report,
                               h_update_visual, tools_for)
from app.spec.models import Dataset, DatasetField, ReportSpec, semantic_errors


@pytest.fixture()
def ctx(services):
    ds = [Dataset(id="kpi", sql="SELECT 1 AS amount", fields=[DatasetField(name="amount", type="number")]),
          Dataset(id="region", sql="SELECT 'a' AS region, 1 AS amount",
                  fields=[DatasetField(name="region"), DatasetField(name="amount", type="number")])]
    c = ToolContext(Session(phase="design", datasets=ds), services)
    r = h_create_report_spec(c, {"spec": {"title": "Satış", "visuals": [
        {"id": "k1", "type": "kpi", "title": "Tutar", "datasetId": "kpi", "encoding": {"value": "amount"}},
        {"id": "r1", "type": "bar", "title": "Bölge", "datasetId": "region", "encoding": {"x": "region", "y": ["amount"]}}]}})
    assert r.ok, r.content
    return c


def test_single_page_report_unchanged(ctx):
    assert ctx.session.spec.pages == [] and all(v.page is None for v in ctx.session.spec.visuals)


def test_add_page_keeps_existing_on_first_page(ctx):
    r = h_add_page(ctx, {"id": "detay", "title": "Bölge Detayı", "visuals": [
        {"id": "r2", "type": "table", "title": "Liste", "datasetId": "region", "encoding": {"columns": ["region", "amount"]},
         "position": {"x": 0, "y": 0, "w": 12, "h": 4}}]})
    assert r.ok, r.content
    spec = ctx.session.spec
    assert [p.id for p in spec.pages] == ["genel", "detay"] and spec.pages[0].title == "Genel Bakış"
    assert {v.id: spec.page_of(v) for v in spec.visuals} == {"k1": "genel", "r1": "genel", "r2": "detay"}
    assert next(v for v in spec.visuals if v.id == "r2").position.y == 0      # kendi sayfasında y=0 (çakışma yok)


def test_move_rename_and_remove_pages(ctx):
    assert h_add_page(ctx, {"id": "detay", "title": "Detay"}).ok
    assert h_update_visual(ctx, {"id": "r1", "changes": {"page": "detay"}}).ok         # görsel taşındı
    spec = ctx.session.spec
    assert spec.page_of(next(v for v in spec.visuals if v.id == "r1")) == "detay"
    r = h_update_report(ctx, {"pages": [{"id": "detay", "title": "Bölgeler"}, {"id": "genel", "title": "Özet"}]})
    assert r.ok and [p.title for p in ctx.session.spec.pages] == ["Bölgeler", "Özet"]   # yeniden adlandırma + sıralama
    assert not h_add_page(ctx, {"id": "detay", "title": "x"}).ok                       # tekrarlanan id
    r = h_remove_page(ctx, {"id": "detay", "move_to": "genel"})
    assert r.ok and ctx.session.spec.pages == []                                       # tek sayfa kaldı: sekmeler kalkar
    assert {v.id for v in ctx.session.spec.visuals} == {"k1", "r1"} and all(v.page is None for v in ctx.session.spec.visuals)


def test_remove_page_without_move_deletes_its_visuals(ctx):
    assert h_add_page(ctx, {"id": "detay", "title": "Detay"}).ok
    assert h_add_page(ctx, {"id": "ek", "title": "Ek"}).ok
    assert h_add_visual(ctx, {"visual": {"id": "x1", "type": "kpi", "title": "X", "datasetId": "kpi",
                                         "encoding": {"value": "amount"}, "page": "ek"}}).ok
    assert h_remove_page(ctx, {"id": "ek"}).ok
    assert "x1" not in {v.id for v in ctx.session.spec.visuals} and [p.id for p in ctx.session.spec.pages] == ["genel", "detay"]


def test_unknown_page_and_overlap_per_page():
    base = {"title": "T", "datasets": [{"id": "d", "sql": "SELECT 1 AS a", "fields": [{"name": "a", "type": "number"}]}]}
    v = lambda i, pg: {"id": i, "type": "kpi", "title": i, "datasetId": "d", "encoding": {"value": "a"}, "page": pg,  # noqa: E731
                       "position": {"x": 0, "y": 0, "w": 3, "h": 2}}
    ok = ReportSpec.model_validate({**base, "pages": [{"id": "p1", "title": "1"}, {"id": "p2", "title": "2"}],
                                    "visuals": [v("a", "p1"), v("b", "p2")]})
    assert semantic_errors(ok) == []                                  # aynı konum, farklı sayfa: çakışma değil
    bad = ReportSpec.model_validate({**base, "pages": [{"id": "p1", "title": "1"}], "visuals": [v("a", "yok")]})
    assert any("sayfa 'yok' yok" in e for e in semantic_errors(bad))


def test_design_tools_include_pages():
    names = {t.name for t in tools_for("design")}
    assert {"add_page", "remove_page"} <= names


def test_moved_or_removed_visual_leaves_no_gap(ctx):
    """Başka sayfaya taşınan görsel o sayfanın en üstüne yerleşir; kalan sayfada boş satır kalmaz."""
    from app.harness.tools import h_remove_visual
    assert h_add_page(ctx, {"id": "detay", "title": "Detay"}).ok
    assert h_update_visual(ctx, {"id": "r1", "changes": {"page": "detay"}}).ok
    spec = ctx.session.spec
    assert next(v for v in spec.visuals if v.id == "r1").position.y == 0          # yeni sayfanın en üstü
    assert h_add_visual(ctx, {"visual": {"id": "k2", "type": "kpi", "title": "İkinci", "datasetId": "kpi",
                                         "encoding": {"value": "amount"}, "position": {"x": 0, "y": 6, "w": 3, "h": 2}}}).ok
    assert h_remove_visual(ctx, {"ids": ["k1"]}).ok
    k2 = next(v for v in ctx.session.spec.visuals if v.id == "k2")
    assert k2.position.y == 0                                                        # üstteki boşluk kapandı


def test_add_page_moves_existing_visuals(ctx):
    r = h_add_page(ctx, {"id": "detay", "title": "Detay", "move_visuals": ["r1"]})
    assert r.ok, r.content
    spec = ctx.session.spec
    assert spec.page_of(next(v for v in spec.visuals if v.id == "r1")) == "detay"
    assert next(v for v in spec.visuals if v.id == "r1").position.y == 0
    assert not h_add_page(ctx, {"id": "x", "title": "X", "move_visuals": ["yok"]}).ok


def test_rename_pages_with_unknown_ids_matches_by_order(ctx):
    """Model ilk sayfanın kimliğini bilmeden 'page1' yazdı: sıraya göre eşleştirilir, görseller sahipsiz kalmaz."""
    assert h_add_page(ctx, {"id": "detay", "title": "Detay", "move_visuals": ["r1"]}).ok
    r = h_update_report(ctx, {"pages": [{"id": "page1", "title": "Genel Özet"}, {"id": "detay", "title": "Detay"}]})
    assert r.ok, r.content
    assert [(p.id, p.title) for p in ctx.session.spec.pages] == [("genel", "Genel Özet"), ("detay", "Detay")]


def test_renaming_one_page_keeps_the_others(ctx):
    """Model yalnız ilk sayfayı yazdı: diğer sayfa silinmemeli."""
    assert h_add_page(ctx, {"id": "detay", "title": "Detay", "visuals": ["r1"]}).ok      # id metni = taşı
    assert ctx.session.spec.page_of(next(v for v in ctx.session.spec.visuals if v.id == "r1")) == "detay"
    r = h_update_report(ctx, {"pages": [{"id": "genel", "title": "Genel Özet"}]})
    assert r.ok, r.content
    assert [(p.id, p.title) for p in ctx.session.spec.pages] == [("genel", "Genel Özet"), ("detay", "Detay")]


def test_update_visual_many_ids_and_move_reminder(ctx):
    assert h_add_visual(ctx, {"visual": {"id": "k2", "type": "kpi", "title": "T2", "datasetId": "kpi", "encoding": {"value": "amount"}}}).ok
    r = h_update_visual(ctx, {"ids": ["k1", "k2"], "changes": {"options": {"color": "mavi"}}})
    assert r.ok, r.content
    assert {v.id: v.options.color for v in ctx.session.spec.visuals if v.type == "kpi"} == {"k1": "#2563eb", "k2": "#2563eb"}
    r = h_add_page(ctx, {"id": "detay", "title": "Detay"})
    assert r.ok and "taşınmadı" in str(r.content)


def test_add_page_with_existing_visual_definitions_moves_them(ctx):
    """Model taşınacak görselin tam tanımını visuals'a yazdı ve first_title'a yeni sayfanın adını verdi."""
    r1 = next(v for v in ctx.session.spec.model_dump(exclude_none=True)["visuals"] if v["id"] == "r1")
    r = h_add_page(ctx, {"id": "urun", "title": "Ürün Detayı", "first_title": "Ürün Detayı", "visuals": [r1]})
    assert r.ok, r.content
    spec = ctx.session.spec
    assert [p.title for p in spec.pages] == ["Genel Bakış", "Ürün Detayı"]
    assert spec.page_of(next(v for v in spec.visuals if v.id == "r1")) == "urun" and len(spec.visuals) == 2
