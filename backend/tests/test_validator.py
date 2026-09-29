import pytest

from app.data.validator import RolePolicy

ANALYST = RolePolicy("analyst", ["dbo"])
ADMIN = RolePolicy("admin", ["dbo"], allow_pii=True)


@pytest.mark.parametrize("sql", [
    "SELECT t.SalesTerritoryRegion, SUM(f.SalesAmount) FROM dbo.FactInternetSales f "
    "JOIN dbo.DimSalesTerritory t ON t.SalesTerritoryKey = f.SalesTerritoryKey GROUP BY t.SalesTerritoryRegion",
    "SELECT Gender, COUNT(*) FROM dbo.DimCustomer GROUP BY Gender",
    "WITH x AS (SELECT * FROM dbo.DimSalesTerritory) SELECT SalesTerritoryGroup, COUNT(*) FROM x GROUP BY SalesTerritoryGroup",
    "SELECT 1 AS a UNION ALL SELECT 2",
    "SELECT COUNT(DISTINCT CustomerKey) FROM dbo.FactInternetSales;",
    "SELECT TOP 10 EnglishProductName FROM dbo.DimProduct ORDER BY ListPrice DESC",
    "SELECT * FROM [dbo].[DimSalesTerritory]",
])
def test_allowed(services, sql):
    r = services.validator.validate(sql, ANALYST)
    assert r.ok, r.errors


@pytest.mark.parametrize("sql,needle", [
    ("DELETE FROM dbo.DimDate", "Yalnızca SELECT"),
    ("DROP TABLE dbo.DimDate", "Yalnızca SELECT"),
    ("SELECT 1; DROP TABLE dbo.DimDate", "Tek bir SQL"),
    ("SELECT * INTO dbo.x FROM dbo.DimDate", "INTO"),
    ("SELECT TOP 5 * INTO #t FROM dbo.DimDate", "INTO"),
    ("EXEC xp_cmdshell 'dir'", "Yalnızca SELECT"),
    ("SELECT * FROM meta.dd_tables", "şemasına erişim"),
    ("SELECT name FROM sys.databases", "şemasına erişim"),
    ("SELECT * FROM OPENROWSET('SQLNCLI', 'x', 'select 1')", "izin yok"),
    ("SELECT SUSER_SNAME()", "izin yok"),
    ("SELECT SYSTEM_USER", "izin yok"),
    ("SELECT HOST_NAME()", "izin yok"),
    ("SELECT @@SERVERNAME", "sistem değişkeni"),
    ("SELECT SalesAmount FROM dbo.FactInternetSales WHERE SalesOrderNumber = @x", "sistem değişkeni"),
    ("SELECT * FROM DimDate", "şemasıyla"),
    ("SELECT * FROM dbo.NotInDictionary", "sözlüğünde yok"),
    ("SELECT * FROM OtherDb.dbo.DimDate", "Veritabanı/sunucu adı"),
    ("SELECT * FROM dbo.DimCustomer", "SELECT *"),
    ("SELECT c.* FROM dbo.DimCustomer c", "SELECT *"),
    ("SELECT EmailAddress FROM dbo.DimCustomer", "PII"),
    ("SELECT c.Phone FROM dbo.FactInternetSales f JOIN dbo.DimCustomer c ON c.CustomerKey = f.CustomerKey", "PII"),
    ("SELECT e.FirstName, e.BaseRate FROM dbo.DimEmployee e", "PII"),
    ("SELECT (1 FROM", "ayrıştırılamadı"),
])
def test_rejected(services, sql, needle):
    r = services.validator.validate(sql, ANALYST)
    assert not r.ok
    assert any(needle in e for e in r.errors), r.errors


def test_pii_allowed_for_admin(services):
    assert services.validator.validate("SELECT EmailAddress FROM dbo.DimCustomer", ADMIN).ok


def test_dictionary_search_turkish(services):
    hits = services.dictionary.search("internet satışları ülke bazında")
    assert hits[0]["table"] == "dbo.factinternetsales"
    assert any(c["name"] == "salesamount" for c in hits[0]["columns"])
    assert services.dictionary.search("BAYİ SATIŞ TEMSİLCİSİ")[0]["table"] == "dbo.factresellersales"
    assert services.dictionary.search("çağrı merkezi servis seviyesi")[0]["table"] == "dbo.factcallcenter"


def test_live_sql_server_validator(sql_services):
    """Gerçek SQL Server: sözlük yüklenir, doğrulanan sorgu çalışır."""
    v = sql_services.validator.validate("SELECT TOP 3 SalesTerritoryGroup FROM dbo.DimSalesTerritory", ANALYST)
    assert v.ok
    assert len(sql_services.connector.execute(v.sql, 10).rows) == 3


def test_tsql_integer_division_becomes_float(services):
    v = services.validator.validate(
        "SELECT COUNT(DISTINCT SalesOrderNumber) * 1.0 / 7 AS a, (COUNT(*) - 3) / NULLIF(COUNT(*), 0) AS b FROM dbo.FactInternetSales", ANALYST)
    assert v.ok
    assert "CAST((COUNT(*) - 3) AS FLOAT)" in v.sql, v.sql       # tamsayı payı ondalığa çevrildi
    assert "COUNT(DISTINCT SalesOrderNumber) * 1.0 / 7" in v.sql  # zaten ondalık olan dokunulmadı


def test_live_growth_not_truncated(sql_services):
    v = sql_services.validator.validate(
        "SELECT (COUNT(DISTINCT CASE WHEN YEAR(OrderDate) = 2013 THEN SalesOrderNumber END) - COUNT(DISTINCT CASE WHEN YEAR(OrderDate) = 2012 THEN SalesOrderNumber END))"
        " / NULLIF(COUNT(DISTINCT CASE WHEN YEAR(OrderDate) = 2012 THEN SalesOrderNumber END), 0) AS g FROM dbo.FactResellerSales", ANALYST)
    assert v.ok
    assert abs(sql_services.connector.execute(v.sql, 1).rows[0][0] - 0.3375) < 0.001


def test_count_of_order_number_becomes_distinct(services):
    v = services.validator.validate(
        "SELECT COUNT(CASE WHEN YEAR(OrderDate) = 2013 THEN SalesOrderNumber END) AS orders, COUNT(*) AS n FROM dbo.FactResellerSales", ANALYST)
    assert v.ok and "COUNT(DISTINCT CASE WHEN" in v.sql and "COUNT(*)" in v.sql
    assert any("tekil sayım" in w for w in v.warnings)


def test_console_mode_warns_on_fanout_and_keeps_sql(services):
    """Sorgu ekranı: JOIN çoğalması uyarıya düşer, SQL otomatik düzeltilmez; güvenlik kuralları aynen geçerli."""
    fan = "SELECT SUM(p.ListPrice) FROM dbo.DimProduct p JOIN dbo.FactInternetSales f ON f.ProductKey = p.ProductKey"
    assert not services.validator.validate(fan, ANALYST).ok
    r = services.validator.validate(fan, ANALYST, strict_joins=False, autofix=False)
    assert r.ok and any("Satır çoğalması" in w for w in r.warnings)
    div = "SELECT SUM(SalesAmount) / COUNT(SalesOrderNumber) FROM dbo.FactInternetSales"
    assert services.validator.validate(div, ANALYST, strict_joins=False, autofix=False).sql == div
    for bad in ("DELETE FROM dbo.DimDate", "SELECT name FROM sys.databases", "SELECT @@SERVERNAME"):
        assert not services.validator.validate(bad, ANALYST, strict_joins=False, autofix=False).ok
