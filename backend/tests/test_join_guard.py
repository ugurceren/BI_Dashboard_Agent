"""JOIN doğrulayıcı: kardinalite, bileşik anahtar, fan-out ve chasm trap (AdventureWorks sözlüğü)."""

import pytest
import sqlglot

from app.data.join_guard import JoinGuard
from app.data.validator import RolePolicy
from app.dictionary.repository import DDColumn, DDTable, _group_relationships

ANALYST = RolePolicy("analyst", ["dbo"])


@pytest.mark.parametrize("sql", [
    # fact → boyut → alt boyut zinciri (N:1)
    """SELECT pc.EnglishProductCategoryName, SUM(f.SalesAmount) FROM dbo.FactInternetSales f
       JOIN dbo.DimProduct p ON p.ProductKey = f.ProductKey
       JOIN dbo.DimProductSubcategory ps ON ps.ProductSubcategoryKey = p.ProductSubcategoryKey
       JOIN dbo.DimProductCategory pc ON pc.ProductCategoryKey = ps.ProductCategoryKey
       GROUP BY pc.EnglishProductCategoryName""",
    # iki fact, önce CTE'lerde toplanmış
    """WITH i AS (SELECT OrderDateKey k, SUM(SalesAmount) s FROM dbo.FactInternetSales GROUP BY OrderDateKey),
            r AS (SELECT OrderDateKey k, SUM(SalesAmount) s FROM dbo.FactResellerSales GROUP BY OrderDateKey)
       SELECT d.CalendarYear, SUM(i.s), SUM(r.s) FROM dbo.DimDate d
       LEFT JOIN i ON i.k = d.DateKey LEFT JOIN r ON r.k = d.DateKey GROUP BY d.CalendarYear""",
    # çoğalan tarafta yalnız COUNT(DISTINCT) / MAX
    """SELECT sr.SalesReasonName, COUNT(DISTINCT f.SalesOrderNumber), MAX(f.SalesAmount) FROM dbo.FactInternetSales f
       JOIN dbo.FactInternetSalesReason r ON r.SalesOrderNumber = f.SalesOrderNumber AND r.SalesOrderLineNumber = f.SalesOrderLineNumber
       JOIN dbo.DimSalesReason sr ON sr.SalesReasonKey = r.SalesReasonKey GROUP BY sr.SalesReasonName""",
    # WHERE ile eski usul birleştirme
    """SELECT t.SalesTerritoryGroup, SUM(f.SalesAmount) FROM dbo.FactInternetSales f, dbo.DimSalesTerritory t
       WHERE t.SalesTerritoryKey = f.SalesTerritoryKey GROUP BY t.SalesTerritoryGroup""",
    # rol yapan tarih: sevk tarihi
    "SELECT d.CalendarYear, SUM(f.SalesAmount) FROM dbo.FactInternetSales f JOIN dbo.DimDate d ON d.DateKey = f.ShipDateKey GROUP BY d.CalendarYear",
])
def test_safe_joins(services, sql):
    r = services.validator.validate(sql, ANALYST)
    assert r.ok, r.errors
    assert not r.warnings, r.warnings


@pytest.mark.parametrize("sql", [
    # köprü tablo: çok nedenli siparişlerin tutarı birden çok sayılır
    """SELECT sr.SalesReasonName, SUM(f.SalesAmount) FROM dbo.FactInternetSales f
       JOIN dbo.FactInternetSalesReason r ON r.SalesOrderNumber = f.SalesOrderNumber AND r.SalesOrderLineNumber = f.SalesOrderLineNumber
       JOIN dbo.DimSalesReason sr ON sr.SalesReasonKey = r.SalesReasonKey GROUP BY sr.SalesReasonName""",
    # chasm trap: iki fact ortak tarih boyutu üzerinden
    """SELECT d.CalendarYear, SUM(f.SalesAmount), SUM(r.SalesAmount) FROM dbo.FactInternetSales f
       JOIN dbo.DimDate d ON d.DateKey = f.OrderDateKey JOIN dbo.FactResellerSales r ON r.OrderDateKey = d.DateKey GROUP BY d.CalendarYear""",
    # boyut kolonunu fact satırları üzerinden toplamak
    "SELECT SUM(p.ListPrice) FROM dbo.DimProduct p JOIN dbo.FactInternetSales f ON f.ProductKey = p.ProductKey",
    # bayi satışı + kota, çalışan üzerinden
    """SELECT e.LastName, SUM(r.SalesAmount), SUM(q.SalesAmountQuota) FROM dbo.DimEmployee e
       JOIN dbo.FactResellerSales r ON r.EmployeeKey = e.EmployeeKey JOIN dbo.FactSalesQuota q ON q.EmployeeKey = e.EmployeeKey
       GROUP BY e.LastName""",
])
def test_fanout_rejected(services, sql):
    r = services.validator.validate(sql, ANALYST)
    assert not r.ok
    assert any("Satır çoğalması" in e for e in r.errors), r.errors


def test_partial_composite_key_rejected(services):
    r = services.validator.validate(
        """SELECT COUNT(DISTINCT r.SalesReasonKey) FROM dbo.FactInternetSales f
           JOIN dbo.FactInternetSalesReason r ON r.SalesOrderNumber = f.SalesOrderNumber""", ANALYST)
    assert not r.ok and any("Bileşik anahtar eksik" in e and "salesorderlinenumber" in e for e in r.errors), r.errors


def test_undefined_join_is_warning(services):
    r = services.validator.validate(
        """SELECT c.Gender, SUM(f.SalesAmount) FROM dbo.FactInternetSales f
           JOIN dbo.DimCustomer c ON c.GeographyKey = f.SalesTerritoryKey GROUP BY c.Gender""", ANALYST)
    assert r.ok and any("sözlükte tanımlı değil" in w for w in r.warnings)


def test_table_details_show_cardinality_and_role(services):
    joins = services.dictionary.table_details("dbo.FactInternetSales", False)["joins"]
    assert any("[N:1" in j and "rol: Sevk tarihi" in j and "pasif" in j for j in joins)
    assert any("rol: Sipariş tarihi" in j and "pasif" not in j for j in joins)


def test_model_for_ui(services):
    m = services.dictionary.model()
    kinds = {t["id"]: t["kind"] for t in m["tables"]}
    assert kinds["dbo.factinternetsales"] == "fact" and kinds["dbo.factinternetsalesreason"] == "bridge"
    assert kinds["dbo.dimdate"] == "dimension" and kinds["dbo.dimcustomer"] == "dimension"
    fis = next(t for t in m["tables"] if t["id"] == "dbo.factinternetsales")
    assert fis["name"] == "dbo.FactInternetSales"
    assert any(c["id"] == "productkey" and c["is_key"] for c in fis["columns"])
    ship = next(r for r in m["relationships"] if r["from_table"] == "dbo.factinternetsales" and r["role"] == "Sevk tarihi")
    assert ship["cardinality"] == "N:1" and ship["active"] is False


# --------------------------------------------------------------------------- elle kurulmuş sözlük: bileşik anahtar, 1:1, N:N
class _DD:
    def __init__(self, tables, rels):
        self.tables = {t.name: t for t in tables}
        self.relationships = rels

    def has_table(self, name):
        return name in self.tables


def _t(name, cols):
    return DDTable(name, name, "", "", "", 100, [DDColumn(name, c, c, "", "", "attribute", None, [], False, "") for c in cols])


@pytest.fixture()
def guard():
    tables = [_t("s.orders", ["order_no", "line_no", "amount"]), _t("s.order_tags", ["order_no", "line_no", "tag", "w"]),
              _t("s.person", ["id", "x"]), _t("s.passport", ["person_id", "y"]),
              _t("s.student", ["id", "fee"]), _t("s.course", ["id", "credits"])]
    rels = _group_relationships([
        {"relationship_id": "fk_tags", "from_table": "s.order_tags", "from_column": "order_no", "to_table": "s.orders", "to_column": "order_no", "cardinality": "N:1"},
        {"relationship_id": "fk_tags", "from_table": "s.order_tags", "from_column": "line_no", "to_table": "s.orders", "to_column": "line_no", "cardinality": "N:1"},
        {"relationship_id": "pp", "from_table": "s.person", "from_column": "id", "to_table": "s.passport", "to_column": "person_id", "cardinality": "1:1"},
        {"relationship_id": "enr", "from_table": "s.student", "from_column": "id", "to_table": "s.course", "to_column": "id", "cardinality": "N:N"},
    ])
    return JoinGuard(_DD(tables, rels))


def _check(guard, sql):
    return guard.check(sqlglot.parse_one(sql, read="tsql"))


def test_composite_key_grouped_and_partial_rejected(guard):
    rel = next(r for r in guard.dd.relationships if r.id == "fk_tags")
    assert rel.pairs == [("order_no", "order_no"), ("line_no", "line_no")]
    rep = _check(guard, "SELECT COUNT(DISTINCT t.tag) FROM s.orders o JOIN s.order_tags t ON t.order_no = o.order_no")
    assert any("Bileşik anahtar eksik" in e and "line_no" in e for e in rep.errors), rep.errors
    full = _check(guard, "SELECT t.tag, SUM(t.w) FROM s.orders o JOIN s.order_tags t ON t.order_no = o.order_no AND t.line_no = o.line_no GROUP BY t.tag")
    assert not full.errors, full.errors  # ölçü 'çok' taraftan: güvenli
    bad = _check(guard, "SELECT t.tag, SUM(o.amount) FROM s.orders o JOIN s.order_tags t ON t.order_no = o.order_no AND t.line_no = o.line_no GROUP BY t.tag")
    assert any("Satır çoğalması" in e for e in bad.errors)


def test_one_to_one_is_safe(guard):
    rep = _check(guard, "SELECT SUM(p.x), SUM(q.y) FROM s.person p JOIN s.passport q ON q.person_id = p.id")
    assert not rep.errors and not rep.warnings


def test_many_to_many_rejected(guard):
    rep = _check(guard, "SELECT SUM(s.fee) FROM s.student s JOIN s.course c ON c.id = s.id")
    assert any("çoktan çoka" in e for e in rep.errors), rep.errors


def test_one_to_many_is_normalized():
    [r] = _group_relationships([{"from_table": "d.dim", "from_column": "k", "to_table": "d.fact", "to_column": "k", "cardinality": "1:N"}])
    assert (r.from_table, r.to_table, r.cardinality) == ("d.fact", "d.dim", "N:1")


def test_live_cardinality_inference(sql_services):
    """Gerçek SQL Server: kardinalite unique index metadatasından çıkarılır."""
    rel = next(r for r in sql_services.dictionary.relationships
               if r.from_table == "dbo.factinternetsalesreason" and r.to_table == "dbo.factinternetsales")
    assert rel.cardinality == "N:1" and len(rel.pairs) == 2
