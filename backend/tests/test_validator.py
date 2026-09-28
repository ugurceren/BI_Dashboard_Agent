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
