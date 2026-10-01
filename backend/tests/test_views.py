"""Dataset → onaylı view: script üretimi, sözlüğe ekleme, filtrelerin view'a yayılması."""

from app.data.model_filters import ModelFilter, ModelFilterEngine
from app.data.views import build_view_script, safe_view_name, select_from_view

REGION_SQL = """SELECT t.SalesTerritoryRegion AS region, CAST(SUM(f.SalesAmount) AS float) AS sales_amount
FROM dbo.FactResellerSales f JOIN dbo.DimSalesTerritory t ON t.SalesTerritoryKey = f.SalesTerritoryKey
GROUP BY t.SalesTerritoryRegion ORDER BY sales_amount DESC"""


def test_script_strips_order_by_and_names():
    vs = build_view_script(schema="rpt", name=safe_view_name("bölge_satış"), dataset_id="bölge_satış", description="d",
                           validated_sql=REGION_SQL, columns=["region", "sales_amount"], session_title="R")
    assert vs.name == "rpt.v_bolge_satis" and not vs.errors
    assert "CREATE OR ALTER VIEW [rpt].[v_bolge_satis] AS" in vs.script and "ORDER BY" not in vs.body_sql
    assert "ÇALIŞTIRILMADI" in vs.script


def test_script_rejects_unnamed_columns():
    vs = build_view_script(schema="rpt", name="v_x", dataset_id="x", description="", validated_sql="SELECT SUM(1) FROM dbo.DimDate",
                           columns=["SUM(1)"], session_title="R")
    assert vs.errors


def test_registered_view_is_queryable_and_filterable(services):
    lineage = ModelFilterEngine(services.dictionary, "tsql").lineage(REGION_SQL)
    assert lineage["region"] == "dbo.dimsalesterritory.salesterritoryregion"
    services.dictionary.add_view({"name": "rpt.v_region_sales", "business_name": "Bölge satış", "columns": [
        {"name": "region", "label": "Bölge", "type": "string", "lineage": lineage["region"]},
        {"name": "sales_amount", "label": "Satış", "type": "number", "lineage": None}]})
    sql = select_from_view("rpt", "v_region_sales", ["region", "sales_amount"])
    v = services.validator.validate(sql, services.policy("standart"))
    assert v.ok, v.errors
    eng = ModelFilterEngine(services.dictionary, "tsql")
    out, applied = eng.apply(sql, [ModelFilter("dbo.dimsalesterritory", "salesterritorygroup", ["Europe"])])
    assert applied and "EXISTS" in out and "SalesTerritoryRegion" in out
    assert eng.lineage(sql)["region"] == "dbo.dimsalesterritory.salesterritoryregion"   # tıklama filtresi kaynak kolona
