"""Model tabanlı filtre yayılımı (Power BI tarzı) ve alan kökeni — AdventureWorks sözlüğü."""

import pytest

from app.data.model_filters import ModelFilter, ModelFilterEngine
from app.data.validator import RolePolicy
from app.dictionary.repository import _group_relationships
from tests.test_join_guard import _DD, _t

STANDART = RolePolicy("standart", ["dbo"])
EUROPE = ModelFilter("dbo.dimsalesterritory", "salesterritorygroup", ["Europe"])


@pytest.fixture()
def eng(services):
    return ModelFilterEngine(services.dictionary, "tsql")


def test_filter_propagates_from_dimension_to_fact(eng):
    # dataset'te bölge yok; filtre DimSalesTerritory → FactInternetSales ilişkisiyle yayılmalı
    sql = """SELECT d.CalendarYear, SUM(f.SalesAmount) AS s FROM dbo.FactInternetSales f
             JOIN dbo.DimDate d ON d.DateKey = f.OrderDateKey GROUP BY d.CalendarYear"""
    out, applied = eng.apply(sql, [EUROPE])
    assert applied == ["dbo.dimsalesterritory.salesterritorygroup"]
    assert "EXISTS(SELECT 1 FROM dbo.DimSalesTerritory AS _mf0 WHERE _mf0.SalesTerritoryKey = f.SalesTerritoryKey" in out
    assert "_mf0.SalesTerritoryGroup IN (N'Europe')" in out


def test_filter_table_in_query_is_filtered_directly(eng):
    sql = """SELECT t.SalesTerritoryGroup, SUM(f.SalesAmount) FROM dbo.FactInternetSales f
             JOIN dbo.DimSalesTerritory t ON t.SalesTerritoryKey = f.SalesTerritoryKey GROUP BY t.SalesTerritoryGroup"""
    out, applied = eng.apply(sql, [ModelFilter("dbo.dimsalesterritory", "salesterritorygroup", ["Europe", "Pacific"])])
    assert "t.SalesTerritoryGroup IN (N'Europe', N'Pacific')" in out and "EXISTS" not in out and applied


def test_filter_applies_inside_ctes_and_union_branches(eng):
    sql = """WITH i AS (SELECT OrderDateKey k, SUM(SalesAmount) s FROM dbo.FactInternetSales GROUP BY OrderDateKey)
             SELECT N'İnternet' AS kanal, SUM(s) FROM i
             UNION ALL SELECT N'Bayi', SUM(SalesAmount) FROM dbo.FactResellerSales"""
    out, applied = eng.apply(sql, [EUROPE])
    assert out.count("EXISTS") == 2 and applied


def test_multi_hop_path(eng):
    # Kategori → Alt kategori → Ürün → Satış: iç içe 3 EXISTS
    out, applied = eng.apply("SELECT SUM(f.SalesAmount) FROM dbo.FactInternetSales f",
                             [ModelFilter("dbo.dimproductcategory", "englishproductcategoryname", ["Bikes"])])
    assert applied and out.count("EXISTS") == 3
    assert "_mf0.ProductKey = f.ProductKey" in out and "_mf2.EnglishProductCategoryName IN (N'Bikes')" in out


def test_unrelated_dataset_is_not_filtered(eng):
    # FactFinance'in satış bölgesiyle ilişkisi yok
    out, applied = eng.apply("SELECT SUM(Amount) FROM dbo.FactFinance", [EUROPE])
    assert applied == [] and "EXISTS" not in out


def test_fact_preferred_over_dimension_path(eng):
    # DimCustomer da (Coğrafya üzerinden) bölgeden ulaşılabilir; fact varken yalnız fact filtrelenmeli
    sql = """SELECT c.Gender, SUM(f.SalesAmount) FROM dbo.FactInternetSales f
             JOIN dbo.DimCustomer c ON c.CustomerKey = f.CustomerKey GROUP BY c.Gender"""
    out, _ = eng.apply(sql, [EUROPE])
    assert out.count("EXISTS") == 1 and "= f.SalesTerritoryKey" in out


def test_only_active_date_relationship_propagates(eng):
    # sipariş tarihi aktif, sevk/vade pasif
    out, _ = eng.apply("SELECT SUM(f.SalesAmount) FROM dbo.FactInternetSales f", [ModelFilter("dbo.dimdate", "calendaryear", [2013])])
    assert "= f.OrderDateKey" in out and "ShipDateKey" not in out and "DueDateKey" not in out


def test_lineage_through_cte(eng):
    sql = """WITH x AS (SELECT t.SalesTerritoryCountry AS c, SUM(f.SalesAmount) AS s FROM dbo.FactInternetSales f
             JOIN dbo.DimSalesTerritory t ON t.SalesTerritoryKey = f.SalesTerritoryKey GROUP BY t.SalesTerritoryCountry)
             SELECT c AS ulke, s FROM x"""
    assert eng.lineage(sql) == {"ulke": "dbo.dimsalesterritory.salesterritorycountry"}


# --------------------------------------------------------------------------- elle kurulmuş model
@pytest.fixture()
def toy():
    tables = [_t("s.ord", ["no", "line", "amt"]), _t("s.ordx", ["no", "line", "tag"])]
    rels = _group_relationships([
        {"relationship_id": "x", "from_table": "s.ordx", "from_column": "no", "to_table": "s.ord", "to_column": "no", "cardinality": "N:1"},
        {"relationship_id": "x", "from_table": "s.ordx", "from_column": "line", "to_table": "s.ord", "to_column": "line", "cardinality": "N:1"},
    ])
    dd = _DD(tables, rels)
    dd._table_kinds = lambda: {"s.ordx": "bridge"}
    return ModelFilterEngine(dd, "tsql")


def test_composite_key_path(toy):
    out, _ = toy.apply("SELECT COUNT(*) FROM s.ordx x", [ModelFilter("s.ord", "amt", [5])])
    assert "_mf0.no = x.no" in out and "_mf0.line = x.line" in out


# --------------------------------------------------------------------------- gerçek SQL Server
def _rows(svc, sql):
    v = svc.validator.validate(sql, STANDART)
    assert v.ok, v.errors
    return svc.connector.execute(v.sql, 5000).rows


def test_live_filtered_totals_match_manual_join(sql_services):
    eng = ModelFilterEngine(sql_services.dictionary, "tsql")
    sql = """SELECT d.CalendarYear, CAST(SUM(f.SalesAmount) AS float) FROM dbo.FactInternetSales f
             JOIN dbo.DimDate d ON d.DateKey = f.OrderDateKey GROUP BY d.CalendarYear ORDER BY d.CalendarYear"""
    got = _rows(sql_services, eng.apply(sql, [EUROPE])[0])
    want = _rows(sql_services, """SELECT d.CalendarYear, CAST(SUM(f.SalesAmount) AS float) FROM dbo.FactInternetSales f
             JOIN dbo.DimDate d ON d.DateKey = f.OrderDateKey JOIN dbo.DimSalesTerritory t ON t.SalesTerritoryKey = f.SalesTerritoryKey
             WHERE t.SalesTerritoryGroup = 'Europe' GROUP BY d.CalendarYear ORDER BY d.CalendarYear""")
    assert [(r[0], round(r[1], 2)) for r in got] == [(r[0], round(r[1], 2)) for r in want]


def test_live_multi_hop_totals(sql_services):
    eng = ModelFilterEngine(sql_services.dictionary, "tsql")
    sql = "SELECT CAST(SUM(f.SalesAmount) AS float) FROM dbo.FactInternetSales f"
    got = _rows(sql_services, eng.apply(sql, [ModelFilter("dbo.dimproductcategory", "englishproductcategoryname", ["Bikes"])])[0])
    want = _rows(sql_services, """SELECT CAST(SUM(f.SalesAmount) AS float) FROM dbo.FactInternetSales f
             JOIN dbo.DimProduct p ON p.ProductKey = f.ProductKey
             JOIN dbo.DimProductSubcategory ps ON ps.ProductSubcategoryKey = p.ProductSubcategoryKey
             JOIN dbo.DimProductCategory pc ON pc.ProductCategoryKey = ps.ProductCategoryKey
             WHERE pc.EnglishProductCategoryName = 'Bikes'""")
    assert round(got[0][0], 2) == round(want[0][0], 2)
