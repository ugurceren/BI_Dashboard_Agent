"""Nesne keşfi (discover_object): veritabanı kataloğundan teknik özellikler; yetki ve kişisel veri işaretleri."""

from app.data.connector import QueryResult
from app.dictionary import discovery
from app.harness.session import Session
from app.harness.tools import ToolContext, h_discover_object, tools_for


class CatalogConnector:
    """sys.* sorgularına hazır cevap veren sahte bağlantı; çalışan SQL'leri kaydeder."""
    dialect = "tsql"

    def __init__(self):
        self.sql: list[str] = []

    def execute(self, sql: str, max_rows: int) -> QueryResult:
        self.sql.append(sql)
        if "FROM sys.objects o" in sql:
            rows = [["USER_TABLE", "2025-01-01T10:00:00", "2025-02-01T10:00:00", "Bayi satışları", 60855]]
        elif "FROM sys.columns c LEFT JOIN" in sql:
            rows = [["SalesOrderNumber", "nvarchar", 40, 0, 0, False, False, False, None, None],
                    ["SalesAmount", "money", 8, 19, 4, True, False, False, "((0))", "Satış tutarı"],
                    ["BirthDate", "date", 3, 10, 0, True, False, False, None, None]]
        elif "FROM sys.indexes" in sql:
            rows = [["PK_X", "CLUSTERED", True, True, False, "SalesOrderNumber"]]
        elif "FROM sys.foreign_keys" in sql:
            rows = [["FK_X_DimDate", "dbo.FactResellerSales", "OrderDateKey", "dbo.DimDate", "DateKey"]]
        else:
            rows = []
        return QueryResult([], [], rows, False, 1)

    def ping(self) -> None:
        pass


def _ctx(services, role="standart"):
    services.connector = CatalogConnector()
    discovery._cache.clear()
    return ToolContext(Session(user_role=role, phase="data"), services)


def test_discover_object_reports_catalog_properties(services):
    ctx = _ctx(services)
    r = h_discover_object(ctx, {"objects": ["FactResellerSales"]})   # şemasız ad → dbo
    assert r.ok, r.content
    o = r.content["objects"][0]
    assert o["type"] == "table" and o["row_count"] == 60855 and o["description"] == "Bayi satışları"
    assert o["columns"][0] == "SalesOrderNumber nvarchar(20) NOT NULL [PK]"
    assert o["columns"][1] == "SalesAmount money NULL default ((0)) — Satış tutarı"
    assert o["indexes"][0]["primary_key"] and o["foreign_keys"][0]["to"] == "dbo.DimDate"
    assert all("OBJECT_ID(N'[dbo].[FactResellerSales]')" in q for q in services.connector.sql)


def test_pii_column_marked_by_role(services):
    """Sözlükte kişisel veri işaretli BirthDate: standart rolde 'sorgulanamaz', admin rolde yalnız PII işareti."""
    birth = lambda r: next(c for c in r.content["objects"][0]["columns"] if c.startswith("BirthDate"))   # noqa: E731
    std = h_discover_object(_ctx(services, "standart"), {"objects": ["dbo.DimCustomer"]})
    assert birth(std) == "BirthDate date NULL [PII-sorgulanamaz]"
    adm = h_discover_object(_ctx(services, "admin"), {"objects": ["dbo.DimCustomer"]})
    assert birth(adm) == "BirthDate date NULL [PII]"


def test_unknown_or_denied_object_rejected(services):
    ctx = _ctx(services)
    r = h_discover_object(ctx, {"objects": ["sys.objects", "dbo.YokBoyle"]})
    assert not r.ok and "yetkili nesneler arasında yok" in r.content["error"]
    assert services.connector.sql == []                                # katalog hiç sorgulanmadı


def test_tool_available_in_data_and_design_phases():
    for phase in ("data", "design"):
        assert "discover_object" in [t.name for t in tools_for(phase)]
    assert "discover_object" not in [t.name for t in tools_for("requirements")]
