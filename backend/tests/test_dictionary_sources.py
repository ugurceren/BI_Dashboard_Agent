"""Sözlük tabloları: rol başına birden çok tablo, eksik kolonlar, rol önerisi."""

from app.data.connector import QueryResult
from app.dictionary.sources import DEFAULT_SOURCES, build_queries, suggest_role


class InfoSchema:
    """INFORMATION_SCHEMA.COLUMNS sorgusuna sahte cevap."""

    def __init__(self, tables: dict[str, list[str]]):
        self.tables = tables
        self.sql: list[str] = []

    def execute(self, sql, max_rows):
        self.sql.append(sql)
        rows = [[t.split(".")[0], t.split(".")[1], c] for t, cols in self.tables.items() for c in cols]
        return QueryResult(["s", "t", "c"], ["string"] * 3, rows)


def test_multiple_tables_union_and_missing_optional_columns():
    con = InfoSchema({
        "meta.dd_tables": ["table_name", "business_name", "description", "subject_area", "grain", "row_count"],
        "satis.kolon_sozlugu": ["table_name", "column_name", "business_name", "description", "data_type"],
        "finans.kolon_sozlugu": ["table_name", "column_name", "business_name", "is_pii"],
    })
    b = build_queries(con, {"tables": ["meta.dd_tables"], "columns": ["satis.kolon_sozlugu", "finans.kolon_sozlugu"],
                            "relationships": [], "metrics": []})
    assert not b.errors
    q = b.queries["columns"]
    assert q.count("UNION ALL") == 1 and "FROM [satis].[kolon_sozlugu]" in q and "FROM [finans].[kolon_sozlugu]" in q
    assert "NULL AS [is_pii]" in q.split("UNION ALL")[0] and "[is_pii]" in q.split("UNION ALL")[1]
    assert "NULL AS [table_type]" in b.queries["tables"]
    assert "relationships" not in b.queries and any("boş sayıldı" in w for w in b.warnings)


def test_required_column_missing_and_unknown_table():
    con = InfoSchema({"meta.x": ["table_name"]})
    b = build_queries(con, {"tables": ["meta.x"], "columns": ["meta.x", "meta.yok"], "relationships": [], "metrics": []})
    assert any("zorunlu kolon" in e and "column_name" in e for e in b.errors)
    assert any("'meta.yok' tablosu bulunamadı" in e for e in b.errors)
    assert any("Kolonlar için en az bir" in e for e in b.errors)


def test_invalid_names_rejected_without_sql_injection():
    con = InfoSchema({})
    b = build_queries(con, {"tables": ["meta.t; DROP TABLE x"], "columns": [], "relationships": [], "metrics": []})
    assert any("Geçersiz tablo adı" in e for e in b.errors)
    assert all("DROP" not in s for s in con.sql)


def test_suggest_role_and_defaults():
    assert suggest_role({"table_name", "column_name", "data_type"}) == "columns"
    assert suggest_role({"table_name", "business_name", "subject_area"}) == "tables"
    assert suggest_role({"from_table", "from_column", "to_table", "to_column"}) == "relationships"
    assert suggest_role({"foo"}) is None
    assert DEFAULT_SOURCES["columns"] == ["meta.dd_columns"]
