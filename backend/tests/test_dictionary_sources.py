"""Sözlük kaynakları: rol başına birden çok tablo / sayfa, eksik kolonlar, normalize, Excel ve MySQL okuyucuları."""

import openpyxl
import pytest

from app.dictionary.sources import DEFAULTS, ExcelReader, MySQLReader, SqlServerReader, collect, default_sources, suggest_role


class FakeReader:
    """Tablo adı → satırlar (kolon adları küçük harf)."""

    kind = "sqlserver"
    valid = staticmethod(SqlServerReader.valid)

    def __init__(self, tables: dict[str, list[dict]]):
        self.tables = {k.lower(): v for k, v in tables.items()}
        self.cols = {k.lower(): set(v[0].keys()) if v else set() for k, v in tables.items()}

    def columns(self, names):
        return {n.lower(): self.cols[n.lower()] for n in names if n.lower() in self.cols}

    def read(self, name):
        return self.tables[name.lower()]

    def list_tables(self):
        return {k: sorted(v) for k, v in self.cols.items()}


def test_multiple_tables_merged_and_optional_columns_blank():
    r = FakeReader({
        "meta.dd_tables": [{"table_name": "dbo.FactSales", "business_name": "Satış", "row_count": "1.234"}],
        "satis.kolon": [{"table_name": "dbo.FactSales", "column_name": "Amount", "is_pii": "Hayır"}],
        "finans.kolon": [{"table_name": "dbo.FactSales", "column_name": "Email", "is_pii": "Evet"},
                         {"table_name": "", "column_name": "boş"}],
    })
    got = collect(r, {"tables": ["meta.dd_tables"], "columns": ["satis.kolon", "finans.kolon"], "relationships": [], "metrics": []})
    assert not got.errors
    assert got.counts == {"tables": 1, "columns": 2}
    t = got.rows["tables"][0]
    assert t["row_count"] == 1234 and t["description"] is None and t["table_type"] is None
    assert [c["is_pii"] for c in got.rows["columns"]] == [False, True]
    assert any("boş sayıldı" in w for w in got.warnings) and any("1 satır atlandı" in w for w in got.warnings)


def test_required_column_missing_and_unknown_table():
    r = FakeReader({"meta.x": [{"table_name": "a"}]})
    got = collect(r, {"tables": ["meta.x"], "columns": ["meta.x", "meta.yok"], "relationships": [], "metrics": []})
    assert any("'meta.x' tablosunda zorunlu kolon(lar) yok: column_name" in e for e in got.errors)
    assert any("'meta.yok' tablosu bulunamadı" in e for e in got.errors)
    assert any("Kolonlar için en az bir geçerli tablo" in e for e in got.errors)


def test_invalid_sql_names_rejected():
    r = FakeReader({})
    got = collect(r, {"tables": ["meta.t; DROP TABLE x"], "columns": [], "relationships": [], "metrics": []})
    assert any("Geçersiz ad" in e for e in got.errors)
    assert MySQLReader.valid("dd_tables") and MySQLReader.valid("meta.dd_tables") and not MySQLReader.valid("x`; DROP")


def test_excel_reader_with_turkish_sheets(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tablolar"
    ws.append(["Table_Name", "Business Name", "Row_Count"])
    ws.append(["dbo.DimProduct", "Ürün", 606])
    ws2 = wb.create_sheet("Kolonlar")
    ws2.append([None])  # başlıktan önce boş satır
    ws2.append(["table_name", "column_name", "is_pii", "synonyms"])
    ws2.append(["dbo.DimProduct", "EnglishProductName", "", "ürün adı, isim"])
    ws2.append([None, None, None, None])
    ws3 = wb.create_sheet("İlişkiler")
    ws3.append(["from_table", "from_column", "to_table", "to_column", "is_active"])
    ws3.append(["dbo.FactSales", "ProductKey", "dbo.DimProduct", "ProductKey", "Hayır"])
    path = tmp_path / "sozluk.xlsx"
    wb.save(path)

    r = ExcelReader(path)
    assert set(r.list_tables()) == {"Tablolar", "Kolonlar", "İlişkiler"}
    got = collect(r, {**default_sources("excel"), "metrics": []})
    assert not got.errors, got.errors
    assert got.rows["tables"][0]["business_name"] == "Ürün" and got.rows["tables"][0]["row_count"] == 606
    assert got.rows["columns"] == [{**{k: None for k in got.rows["columns"][0]}, "table_name": "dbo.DimProduct",
                                    "column_name": "EnglishProductName", "is_pii": False, "synonyms": "ürün adı, isim"}]
    assert got.rows["relationships"][0]["is_active"] is False
    # sayfa adı büyük/küçük harf duyarsız
    assert not collect(r, {"tables": ["tablolar"], "columns": ["KOLONLAR"], "relationships": [], "metrics": []}).errors
    assert "sayfası bulunamadı" in " ".join(collect(r, {"tables": ["Yok"], "columns": ["Kolonlar"]}).errors)


def test_excel_missing_file():
    with pytest.raises(FileNotFoundError):
        ExcelReader("D:/yok/sozluk.xlsx")


def test_suggest_role_and_defaults():
    assert suggest_role({"table_name", "column_name", "data_type"}) == "columns"
    assert suggest_role({"table_name", "business_name", "subject_area"}) == "tables"
    assert suggest_role({"from_table", "from_column", "to_table", "to_column"}) == "relationships"
    assert suggest_role({"foo"}) is None
    assert DEFAULTS["sqlserver"]["columns"] == ["meta.dd_columns"] and default_sources("mysql")["tables"] == ["dd_tables"]


def test_template_roundtrip(settings, services, tmp_path):
    """Sözlük şablonu indirilip Excel kaynağı olarak geri okunabilmeli (aynı tablo / kolon sayıları)."""
    from fastapi.testclient import TestClient

    import app.main as m

    m.state.services = services
    r = TestClient(m.app).get("/api/settings/dictionary/template.xlsx")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    path = tmp_path / "t.xlsx"
    path.write_bytes(r.content)
    got = collect(ExcelReader(path), default_sources("excel"))
    assert not got.errors, got.errors
    dd = services.dictionary
    assert got.counts["tables"] == len([t for t in dd.tables.values() if t.table_type != "view"])
    assert got.counts["columns"] == sum(len(t.columns) for t in dd.tables.values() if t.table_type != "view")


def test_single_flat_table_with_turkish_headers(tmp_path):
    """Tek sayfa / tek tablo: tablolar kolon satırlarından çıkar, Türkçe başlıklar tanınır."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sözlük"
    ws.append(["Tablo Adı", "Tablo Açıklaması", "Konu Alanı", "Kolon Adı", "İş Adı", "Açıklama", "Veri Tipi", "Kişisel Veri"])
    ws.append(["dbo.FactSales", "Satış işlemleri", "Satış", "SalesAmount", "Satış tutarı", "KDV hariç", "money", "Hayır"])
    ws.append(["dbo.FactSales", "Satış işlemleri", "Satış", "CustomerEmail", "E-posta", "", "nvarchar", "Evet"])
    ws.append(["dbo.DimProduct", None, "Ürün", "ProductKey", "Ürün anahtarı", "", "int", ""])
    path = tmp_path / "tek.xlsx"
    wb.save(path)
    got = collect(ExcelReader(path), {"tables": [], "columns": ["Sözlük"], "relationships": [], "metrics": []})
    assert not got.errors, got.errors
    tables = {t["table_name"]: t for t in got.rows["tables"]}
    assert set(tables) == {"dbo.FactSales", "dbo.DimProduct"} and got.derived_tables == 2
    assert tables["dbo.FactSales"]["description"] == "Satış işlemleri" and tables["dbo.FactSales"]["subject_area"] == "Satış"
    assert tables["dbo.DimProduct"]["description"] is None and tables["dbo.DimProduct"]["subject_area"] == "Ürün"
    cols = got.rows["columns"]
    assert cols[0]["business_name"] == "Satış tutarı" and cols[0]["description"] == "KDV hariç"  # kolon açıklaması ≠ tablo açıklaması
    assert [c["is_pii"] for c in cols] == [False, True, False]
    assert any("kolon sözlüğünden çıkarıldı" in w for w in got.warnings)


def test_mixed_tables_plus_extra_columns():
    """Tablolar ayrı verilmiş ama bir tablo yalnız kolon sözlüğünde: o da eklenir, açık tablo bilgisi korunur."""
    r = FakeReader({
        "meta.t": [{"table_name": "dbo.A", "business_name": "A tablosu"}],
        "meta.c": [{"table_name": "dbo.A", "column_name": "x", "table_description": "yok sayılır"},
                   {"table_name": "dbo.B", "column_name": "y", "table_description": "B açıklaması"}],
    })
    got = collect(r, {"tables": ["meta.t"], "columns": ["meta.c"], "relationships": [], "metrics": []})
    t = {x["table_name"]: x for x in got.rows["tables"]}
    assert t["dbo.A"]["business_name"] == "A tablosu" and t["dbo.B"]["description"] == "B açıklaması"
    assert any("Yalnız kolon sözlüğünde olan 1 tablo" in w for w in got.warnings)


def _dd_with(tables: dict[str, list[str]], connector=None):
    from app.config import Settings
    from app.dictionary.repository import DataDictionary, DDColumn, DDTable

    dd = DataDictionary(Settings(), connector)
    tabs = {}
    for name, cols in tables.items():
        t = DDTable(name.lower(), name, "", "", "", None, display_name=name)
        t.columns = [DDColumn(name.lower(), c.lower(), c, "", "int", "key", None, [], False, "", display_name=c) for c in cols]
        tabs[name.lower()] = t
    return dd, tabs


def test_relationships_from_key_column_names():
    dd, tabs = _dd_with({"dbo.FactInternetSales": ["SalesOrderNumber", "ProductKey", "CustomerKey", "OrderDateKey"],
                         "dbo.DimProduct": ["ProductKey", "ProductSubcategoryKey"],
                         "dbo.DimProductSubcategory": ["ProductSubcategoryKey"],
                         "dbo.DimCustomer": ["CustomerKey"],
                         "dbo.Other": ["CustomerKey"]})
    rows, src = dd._auto_relationship_rows(tabs)
    pairs = {(r["from_table"], r["to_table"], r["from_column"]) for r in rows}
    assert src == "name_match"
    assert ("dbo.FactInternetSales", "dbo.DimProduct", "productkey") in pairs
    assert ("dbo.DimProduct", "dbo.DimProductSubcategory", "productsubcategorykey") in pairs
    assert ("dbo.Other", "dbo.DimCustomer", "customerkey") in pairs
    assert not any(r["from_column"] == "orderdatekey" for r in rows)  # hedef tablo yok → atlanır


def test_relationships_prefer_foreign_keys():
    from app.data.connector import QueryResult

    class FK:
        dialect = "tsql"

        def execute(self, sql, max_rows):
            assert "sys.foreign_keys" in sql
            return QueryResult([], [], [["FK_Sales_Product", "dbo.FactSales", "ProductKey", "dbo.DimProduct", "ProductKey"],
                                        ["FK_X", "dbo.NotInDict", "a", "dbo.DimProduct", "ProductKey"]])

    dd, tabs = _dd_with({"dbo.FactSales": ["ProductKey"], "dbo.DimProduct": ["ProductKey"]}, FK())
    rows, src = dd._auto_relationship_rows(tabs)
    assert src == "foreign_keys" and len(rows) == 1 and rows[0]["relationship_id"] == "fk:FK_Sales_Product"
