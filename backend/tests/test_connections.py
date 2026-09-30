"""Bağlantı Ayarları: ODBC cümlesi, şifre koruması, uç noktalar, erişim ve veritabanı yokken açılış."""

import sys

import pytest

from app.data import connections as conns


@pytest.fixture()
def conn_file(tmp_path, monkeypatch):
    f = tmp_path / "connections.json"
    monkeypatch.setattr(conns, "CONNECTIONS_FILE", f)
    monkeypatch.setattr(conns, "installed_drivers", lambda: ["ODBC Driver 17 for SQL Server"])
    return f


def test_build_odbc_windows_and_sql_auth(conn_file):
    w = conns.build_odbc({"server": r"SRV01\BI", "database": "Satis DB", "auth": "windows"})
    assert w.startswith("DRIVER={ODBC Driver 17 for SQL Server};SERVER=SRV01\\BI;DATABASE={Satis DB};Trusted_Connection=yes;")
    s = conns.build_odbc({"server": "srv,1433", "database": "db", "auth": "sql", "username": "bi_ro", "password": "p;a}ss"})
    assert "UID=bi_ro;PWD={p;a}}ss};" in s and "Trusted_Connection" not in s
    assert conns.build_odbc({"server": "x", "database": "a"}, database="master").count("DATABASE=master") == 1


def test_parse_odbc_roundtrip_fields():
    f = conns.parse_odbc("DRIVER={ODBC Driver 18 for SQL Server};SERVER=srv\\inst;DATABASE=AW;UID=u;PWD={x;y};TrustServerCertificate=yes;")
    assert f == {"server": "srv\\inst", "database": "AW", "auth": "sql", "username": "u", "has_password": True,
                 "encrypt": True, "trust_server_certificate": True}


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI yalnız Windows")
def test_password_protected_with_dpapi():
    tok = conns.protect("Gizli!123")
    assert tok.startswith("dpapi:") and "Gizli" not in tok and conns.unprotect(tok) == "Gizli!123"


def test_dictionary_defaults_to_bi_meta_on_data_server(conn_file):
    cfg = {"data": {"server": "srv", "database": "AW", "auth": "windows"}, "dictionary": {"same_as_data": True}}
    assert conns.dictionary_conn_fields(cfg) == {"server": "srv", "database": "BI_Meta", "auth": "windows"}


def test_settings_endpoints_save_and_mask_password(settings, services, conn_file, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as m

    m.state.services = services
    m.state.agent = type("A", (), {"services": services})()
    monkeypatch.setattr(m, "_probe", lambda fields, database=None: {"server_name": fields["server"], "database": fields["database"]})
    monkeypatch.setattr(m, "build_services", lambda: services)
    from app.dictionary import sources as srcmod
    monkeypatch.setattr(conns, "open_dictionary_reader", lambda fields: (None, {}))
    monkeypatch.setattr(srcmod, "collect", lambda reader, src: srcmod.Collected())
    c = TestClient(m.app)
    body = {"data": {"server": r"SRV\BI", "database": "AW", "auth": "sql", "username": "ro", "password": "s3cret"},
            "dictionary": {"same_as_data": True, "database": ""}}
    t = c.post("/api/settings/connections/test", json={"target": "data", **body}).json()
    assert t["ok"] and t["server_name"] == r"SRV\BI"
    assert c.put("/api/settings/connections", json=body).status_code == 200
    raw = conn_file.read_text(encoding="utf-8")
    assert "s3cret" not in raw and "password_enc" in raw
    g = c.get("/api/settings/connections").json()
    assert g["source"] == "ui" and g["data"]["has_password"] and "password" not in g["data"]
    assert g["dictionary"]["database"] == "BI_Meta" and g["dictionary"]["same_as_data"]
    # şifre boş gönderilirse kayıtlı şifre korunur
    assert "PWD={s3cret}" in conns.build_odbc(conns.load_connections()["data"])
    c.put("/api/settings/connections", json={**body, "data": {**body["data"], "password": None}})
    assert "PWD={s3cret}" in conns.build_odbc(conns.load_connections()["data"])
    assert c.delete("/api/settings/connections").status_code == 200 and not conn_file.exists()


def test_settings_forbidden_for_remote_non_admin(settings, services, conn_file):
    from fastapi.testclient import TestClient

    import app.main as m

    m.state.services = services
    c = TestClient(m.app)
    r = c.get("/api/settings/connections", headers={"X-Forwarded-For": "10.1.2.3"})
    assert r.status_code == 403


def test_app_starts_without_database(settings, monkeypatch, conn_file):
    """Veritabanına ulaşılamasa da servisler kurulur; hata startup_error'da tutulur."""
    import app.main as m
    from app.data import connector as cmod

    def boom(self, sql, max_rows):
        raise cmod.QueryError("[08001] [Microsoft][ODBC Driver 17 for SQL Server]Named Pipes Provider: Could not open a connection")
    monkeypatch.setattr(cmod.SqlServerConnector, "execute", boom)
    svc = m.build_services()
    assert svc.dictionary.tables == {} and "Veri sözlüğü" in (m.state.startup_error or "")


def test_friendly_connection_errors():
    from app.data.odbc import friendly_error

    e = ("('08001', '[08001] [Microsoft][ODBC Driver 18 for SQL Server]SQL Server Network Interfaces: Error Locating "
         "Server/Instance Specified [xFFFFFFFF].  (-1) (SQLDriverConnect)')")
    assert friendly_error(e).startswith("Sunucu / instance bulunamadı") and "Error Locating" in friendly_error(e)
    assert friendly_error("[28000] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]Login failed for user 'x'. (18456)").startswith("Oturum açılamadı")


def test_save_rejected_when_dictionary_tables_invalid(settings, services, conn_file, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as m
    from app.dictionary import sources as srcmod

    m.state.services = services
    monkeypatch.setattr(conns, "open_dictionary_reader", lambda fields: (None, {}))
    monkeypatch.setattr(srcmod, "collect", lambda reader, src: srcmod.Collected(errors=["Kolonlar: zorunlu kolon yok"]))
    body = {"data": {"server": "srv", "database": "AW"},
            "dictionary": {"same_as_data": True, "sources": {"tables": ["meta.x"], "columns": ["meta.x"]}}}
    r = TestClient(m.app).put("/api/settings/connections", json=body)
    assert r.status_code == 422 and "Kaydedilmedi" in r.text and not conn_file.exists()


def test_excel_and_mysql_dictionary_kinds_resolved(settings, services, conn_file, monkeypatch, tmp_path):
    """Excel / MySQL sözlüğü: 'aynı sunucu' uygulanmaz, kendi alanları ve varsayılan sayfa / tablo adları saklanır."""
    from fastapi.testclient import TestClient

    import app.main as m
    from app.dictionary import sources as srcmod

    m.state.services = services
    m.state.agent = type("A", (), {"services": services})()
    monkeypatch.setattr(m, "build_services", lambda: services)
    monkeypatch.setattr(conns, "open_dictionary_reader", lambda fields: (None, {}))
    monkeypatch.setattr(srcmod, "collect", lambda reader, src: srcmod.Collected())
    c = TestClient(m.app)
    data = {"server": "srv", "database": "AW"}
    x = {"kind": "excel", "excel_path": str(tmp_path / "s.xlsx"), "same_as_data": True}
    assert c.put("/api/settings/connections", json={"data": data, "dictionary": x}).status_code == 200
    d = conns.load_connections()["dictionary"]
    assert d["kind"] == "excel" and not d["same_as_data"] and d["sources"]["columns"] == ["Kolonlar"]
    my = {"kind": "mysql", "server": "mysqlhost", "database": "meta", "username": "ro", "password": "pw"}
    assert c.put("/api/settings/connections", json={"data": data, "dictionary": my}).status_code == 200
    d = conns.load_connections()["dictionary"]
    assert d["kind"] == "mysql" and d["port"] == 3306 and d["auth"] == "sql" and "pw" not in str(d)
    assert conns.saved_dictionary()[0]["server"] == "mysqlhost" and conns.dictionary_odbc(settings) is None


def test_query_schema_lists_procedures_with_domain(settings, services, monkeypatch):
    """Stored procedure'ler: yalnız yetkili şemalar, parametreler ve kullandığı tablolardan domain."""
    from fastapi.testclient import TestClient

    import app.main as m
    from app.data.connector import QueryResult

    class Con:
        dialect = "tsql"

        def execute(self, sql, max_rows):
            if "FROM sys.procedures p LEFT JOIN" in sql:
                return QueryResult([], [], [[1, "dbo", "uspSatisOzeti", "Aylık satış özeti"], [2, "secret", "uspGizli", None]])
            if "sys.parameters" in sql:
                return QueryResult([], [], [[1, "@Yil", "int"], [1, "@Bolge", "nvarchar"]])
            if "sql_expression_dependencies" in sql:
                return QueryResult([], [], [[1, "dbo", "FactInternetSales"], [1, "dbo", "DimProduct"]])
            return QueryResult([], [], [])

    m.state.services = services
    monkeypatch.setattr(services, "connector", Con())
    monkeypatch.setattr(m.state, "store", type("S", (), {"list": lambda self: []})(), raising=False)
    d = TestClient(m.app).get("/api/query/schema").json()
    assert [p["name"] for p in d["procedures"]] == ["dbo.uspSatisOzeti"]  # 'secret' şemasına yetki yok
    sp = d["procedures"][0]
    assert sp["parameters"] == ["@Yil int", "@Bolge nvarchar"] and sp["description"] == "Aylık satış özeti"
    assert sp["subject_area"] == services.dictionary.tables["dbo.factinternetsales"].subject_area
