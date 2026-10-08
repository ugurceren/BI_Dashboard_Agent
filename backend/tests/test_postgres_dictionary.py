"""Veri sözlüğü kaynağı: PostgreSQL (pg8000). Sunucu olmadan sahte bağlantıyla: salt-okunur oturum, şema.tablo adları,
kolon listesi, tablo okuma (tırnaklı ad), varsayılanlar ve bağlantı formu (port 5432, kullanıcı adı / şifre)."""

import pytest

from app.dictionary import sources


class FakeCursor:
    def __init__(self, conn):
        self.conn, self.description, self._rows = conn, None, []

    def execute(self, sql, args=None):
        self.conn.sql.append((sql, args))
        if sql.startswith("SELECT version()"):
            self.description, self._rows = [("v",), ("d",), ("u",)], [["PostgreSQL 16.2, compiled by x", "bi_meta", "bi_okuyucu"]]
        elif "information_schema.columns WHERE table_schema = %s" in sql:
            self.description = [("column_name",)]
            self._rows = [["table_name"], ["business_name"]] if args == ("meta", "dd_tables") else []
        elif sql.startswith('SELECT * FROM "meta"."dd_tables"'):
            self.description, self._rows = [("table_name",), ("business_name",)], [["dbo.FactSales", "Satışlar"]]
        elif "FROM information_schema.columns" in sql:
            self.description, self._rows = [("s",), ("t",), ("c",)], [["meta", "dd_tables", "table_name"], ["public", "x", "a"]]
        elif "pg_database" in sql:
            self.description, self._rows = [("datname",)], [["bi_meta"], ["postgres"]]
        else:
            self.description, self._rows = None, []

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, **kw):
        self.kw, self.sql, self.autocommit = kw, [], False

    def cursor(self):
        return FakeCursor(self)


@pytest.fixture()
def pg(monkeypatch):
    import pg8000.dbapi
    made = []
    monkeypatch.setattr(pg8000.dbapi, "connect", lambda **kw: made.append(FakeConn(**kw)) or made[-1])
    r = sources.PostgresReader("pg.kurum.local", 5432, "bi_okuyucu", "gizli", "bi_meta")
    return r, made[0]


def test_postgres_reader_is_read_only_and_reads(pg):
    r, conn = pg
    assert conn.autocommit and conn.kw["port"] == 5432 and conn.kw["database"] == "bi_meta"
    assert conn.sql[0][0] == "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"
    assert r.probe() == {"server_name": "pg.kurum.local", "database": "bi_meta", "version": "PostgreSQL 16.2",
                         "login": "bi_okuyucu", "driver": "pg8000"}
    assert r.columns(["meta.dd_tables", "olmayan", "kötü ad; DROP"]) == {"meta.dd_tables": ["table_name", "business_name"]}
    assert r.read("meta.dd_tables") == [{"table_name": "dbo.FactSales", "business_name": "Satışlar"}]
    assert r.list_tables() == {"meta.dd_tables": ["table_name"], "public.x": ["a"]}
    assert r.databases() == ["bi_meta", "postgres"]
    assert r._split("dd_tables") == ("public", "dd_tables")               # şemasız ad: public


def test_postgres_defaults_and_kind():
    assert "postgres" in sources.KINDS
    assert sources.default_sources("postgres")["tables"] == ["public.dd_tables"]


def test_connection_form_uses_password_auth_and_default_port():
    from app.main import ConnIn, ConnTestIn, _resolved
    _, dic = _resolved(ConnTestIn(target="dictionary", data=ConnIn(database="EDWDM"),
                                  dictionary=ConnIn(kind="postgres", server="pg.kurum.local", database="bi_meta", auth="windows",
                                                    username="u", password="p", same_as_data=True)))
    assert dic["kind"] == "postgres" and dic["port"] == 5432 and dic["auth"] == "sql" and dic["same_as_data"] is False
