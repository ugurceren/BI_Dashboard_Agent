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
    monkeypatch.setattr(srcmod, "collect", lambda reader, src, *a: srcmod.Collected())
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
    monkeypatch.setattr(srcmod, "collect", lambda reader, src, *a: srcmod.Collected(errors=["Kolonlar: zorunlu kolon yok"]))
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
    monkeypatch.setattr(srcmod, "collect", lambda reader, src, *a: srcmod.Collected())
    c = TestClient(m.app)
    data = {"server": "srv", "database": "AW"}
    x = {"kind": "excel", "excel_path": str(tmp_path / "s.xlsx"), "same_as_data": True}
    assert c.put("/api/settings/connections", json={"data": data, "dictionary": x}).status_code == 200
    d = conns.load_connections()["dictionary"]
    assert d["kind"] == "excel" and not d["same_as_data"] and d["sources"]["columns"] == ["Kolonlar"]
    my = {"kind": "mysql", "server": "mysqlhost", "database": "meta", "username": "ro", "password": "pw"}
    assert c.put("/api/settings/connections", json={"data": data, "dictionary": my}).status_code == 200
    d = conns.load_connections()["dictionary"]
    # şifre açık metin saklanmaz (şifreli değerde "pw" harfleri tesadüfen geçebilir: alanları kontrol et)
    assert d["kind"] == "mysql" and d["port"] == 3306 and d["auth"] == "sql" and "password" not in d
    assert conns.unprotect(d["password_enc"]) == "pw" and d["password_enc"] != "pw"
    assert conns.saved_dictionary()[0]["server"] == "mysqlhost" and conns.dictionary_odbc(settings) is None


def test_no_stored_procedures_anywhere(settings, services, monkeypatch):
    """Projede stored procedure kullanılmaz: Sorgu Çalıştır şemasında da, Veri Erişimim'de de SP yok;
    veritabanına SP sorgusu (sys.procedures) hiç gönderilmez."""
    from fastapi.testclient import TestClient

    import app.main as m

    sent = []

    class Con:
        dialect = "tsql"

        def execute(self, sql, max_rows):
            sent.append(sql)
            from app.data.connector import QueryResult
            return QueryResult([], [], [])

    m.state.services = services
    monkeypatch.setattr(services, "connector", Con())
    monkeypatch.setattr(m.state, "store", type("S", (), {"list": lambda self: []})(), raising=False)
    c = TestClient(m.app)
    q = c.get("/api/query/schema").json()
    a = c.get("/api/me/access").json()
    assert "procedures" not in q and not any(o.get("type") == "procedure" for o in a["objects"])
    assert not any("sys.procedures" in x for x in sent)


def test_my_access_returns_typed_objects_with_domains(settings, services, monkeypatch):
    """Veri Erişimim: tek liste — tablo / view / dataset, her biri domain ve erişim durumuyla."""
    from fastapi.testclient import TestClient

    import app.main as m

    m.state.services = services
    monkeypatch.setattr(m.state, "store", type("S", (), {"list": lambda self: []})(), raising=False)
    d = TestClient(m.app).get("/api/me/access").json()
    types = {o["type"] for o in d["objects"]}
    assert "table" in types and "procedure" not in types and "tables" not in d
    t = next(o for o in d["objects"] if o["id"] == "dbo.factinternetsales")
    assert t["type"] == "table" and t["subject_area"] and t["accessible"] and "column_count" in t


def test_export_connections_without_passwords(settings, services, conn_file, monkeypatch):
    """Dışa aktarılan ayar dosyasında şifre (ne düz ne şifreli) olmamalı; alanlar içe aktarmaya yetmeli."""
    import json as _json

    from fastapi.testclient import TestClient

    import app.main as m
    from app.dictionary import sources as srcmod

    m.state.services = services
    m.state.agent = type("A", (), {"services": services})()
    monkeypatch.setattr(m, "build_services", lambda: services)
    monkeypatch.setattr(conns, "open_dictionary_reader", lambda fields: (None, {}))
    monkeypatch.setattr(srcmod, "collect", lambda reader, src, *a: srcmod.Collected())
    c = TestClient(m.app)
    body = {"data": {"server": r"SRV\BI", "database": "AW", "auth": "sql", "username": "ro", "password": "s3cret"},
            "dictionary": {"kind": "sqlserver", "same_as_data": True, "database": "BI_Meta",
                           "sources": {"tables": [], "columns": ["meta.sozluk"], "relationships": [], "metrics": []}}}
    assert c.put("/api/settings/connections", json=body).status_code == 200
    r = c.get("/api/settings/connections/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert "s3cret" not in r.text and "password" not in r.text
    d = _json.loads(r.text)
    assert d["type"] == "connection-settings" and d["data"]["server"] == r"SRV\BI" and d["data"]["auth"] == "sql"
    assert d["dictionary"]["sources"]["columns"] == ["meta.sozluk"] and d["dictionary"]["database"] == "BI_Meta"



def test_access_follows_database_permissions(settings, services, monkeypatch):
    """Erişim veritabanı yetkisinden gelir: SELECT yetkisi olan her şema görünür, olmayan gizlenir / reddedilir."""
    import dataclasses

    from fastapi.testclient import TestClient

    import app.main as m
    from app.dictionary.repository import DDColumn, DDTable

    dd = services.dictionary
    m.state.services = services
    monkeypatch.setattr(m.state, "store", type("S", (), {"list": lambda self: []})(), raising=False)
    pol = dataclasses.replace(services.policy("standart"), allowed_schemas=["*"])
    monkeypatch.setattr(services, "policies", {**services.policies, "standart": pol})
    extra = DDTable("satis.siparis", "satis.Siparis", "Sipariş başlıkları", "Sözlükte tanımsız", "", 10, display_name="satis.Siparis",
                    documented=False, in_db=True, can_select=True)
    extra.columns = [DDColumn("satis.siparis", "tutar", "Tutar", "", "money", "attribute", None, [], False, "", display_name="Tutar")]
    monkeypatch.setitem(dd.tables, "satis.siparis", extra)
    monkeypatch.setattr(dd.tables["dbo.dimdate"], "can_select", False)
    c = TestClient(m.app)
    acc = {o["id"]: o for o in c.get("/api/me/access").json()["objects"]}
    assert acc["satis.siparis"]["accessible"] and acc["satis.siparis"]["documented"] is False   # dbo dışı şema da görünür
    assert not acc["dbo.dimdate"]["accessible"] and "SELECT yetkiniz yok" in acc["dbo.dimdate"]["reason"]
    names = {o["id"] for o in c.get("/api/query/schema").json()["objects"]}
    assert "satis.siparis" in names and "dbo.dimdate" not in names
    v = services.validator
    assert v.validate("SELECT Tutar FROM satis.Siparis", pol).ok
    r = v.validate("SELECT DateKey FROM dbo.DimDate", pol)
    assert not r.ok and "SELECT yetkiniz yok" in r.errors[0]
    assert not v.validate("SELECT name FROM sys.tables", pol).ok   # sistem şeması her zaman kapalı


def test_llm_settings_saved_encrypted_and_sections_independent(settings, services, conn_file, monkeypatch):
    """LLM bağlantısı: anahtar şifreli; görsel model ayrı / aynı; veritabanı kaydı ve sıfırlaması LLM bölümünü silmez."""
    import json as _json

    from fastapi.testclient import TestClient

    import app.main as m
    from app.dictionary import sources as srcmod
    from app.llm.gateway import LLMGateway

    m.state.services = services
    m.state.agent = type("A", (), {"services": services, "llm": None})()
    m.state.gateway = LLMGateway(settings)
    monkeypatch.setattr(m, "build_services", lambda: services)
    monkeypatch.setattr(LLMGateway, "health", lambda self: {"reachable": True, "model": self.s.llm_model})
    monkeypatch.setattr(conns, "open_dictionary_reader", lambda fields: (None, {}))
    monkeypatch.setattr(srcmod, "collect", lambda reader, src, *a: srcmod.Collected())
    c = TestClient(m.app)
    llm = {"base_url": "https://llm.kurum/v1", "api_key": "sk-gizli", "model": "qwen-32b", "tool_mode": "native",
           "vision": {"enabled": True, "same_as_main": False, "base_url": "https://vl.kurum/v1", "api_key": "vk-gizli", "model": "qwen-vl"}}
    r = c.put("/api/settings/llm", json=llm)
    assert r.status_code == 200 and r.json()["model"] == "qwen-32b"
    raw = conn_file.read_text(encoding="utf-8")
    assert "sk-gizli" not in raw and "vk-gizli" not in raw
    g = m.state.gateway.s   # yeniden başlatmadan uygulandı
    assert (g.llm_base_url, g.llm_api_key, g.llm_model, g.llm_tool_mode) == ("https://llm.kurum/v1", "sk-gizli", "qwen-32b", "native")
    assert (g.vision_base_url, g.vision_api_key, g.vision_model) == ("https://vl.kurum/v1", "vk-gizli", "qwen-vl")
    assert m.state.agent.llm is m.state.gateway
    e = c.get("/api/settings/llm").json()
    assert e["source"] == "ui" and e["has_api_key"] and e["vision"]["has_api_key"] and "sk-gizli" not in _json.dumps(e)
    # anahtar boş bırakılırsa (None) kayıtlı anahtar korunur; görsel "aynı bağlantı"
    c.put("/api/settings/llm", json={**llm, "api_key": None, "vision": {"enabled": True, "same_as_main": True, "model": "qwen-vl"}})
    g = m.state.gateway.s
    assert g.llm_api_key == "sk-gizli" and g.vision_base_url is None and g.vision_model == "qwen-vl"
    # veritabanı ayarlarını kaydetmek / sıfırlamak LLM bölümünü korur
    db = {"data": {"server": "srv", "database": "AW"}, "dictionary": {"same_as_data": True}}
    assert c.put("/api/settings/connections", json=db).status_code == 200
    assert "llm" in conns.load_connections() and "data" in conns.load_connections()
    c.delete("/api/settings/connections")
    assert set(conns.load_connections()) == {"llm"}
    exp = c.get("/api/settings/connections/export").text
    assert "sk-gizli" not in exp and '"qwen-32b"' in exp
    c.delete("/api/settings/llm")
    from app.config import get_settings
    assert not conn_file.exists() and m.state.gateway.s.llm_model == get_settings().llm_model   # .env ayarına döndü


def test_catalog_loaded_even_if_dictionary_fails(settings, monkeypatch, conn_file):
    """Sözlük yüklenemese de (ör. dictionary.toml localhost/BI_Meta'yı gösteriyor) yetkili tablolar katalogdan gelir."""
    import app.main as m
    from app.dictionary.repository import DataDictionary, DDTable

    def boom(self):
        raise RuntimeError("[08001] [Microsoft][ODBC Driver 17 for SQL Server]Cannot open database \"BI_Meta\" (4060)")

    def cat(self):
        self.tables["dbo.satis"] = DDTable("dbo.satis", "dbo.Satis", "", "Sözlükte tanımsız", "", None, display_name="dbo.Satis",
                                           documented=False, in_db=True, can_select=True)
    monkeypatch.setattr(DataDictionary, "load", boom)
    monkeypatch.setattr(DataDictionary, "_merge_catalog", cat)
    svc = m.build_services()
    assert "dbo.satis" in svc.dictionary.tables and "Veri sözlüğü" in (m.state.startup_error or "")


def test_no_dictionary_kind_uses_catalog_only(settings, services, conn_file, monkeypatch):
    """'Sözlük yok': sözlük kaynağı okunmaz (hata yok), tablolar yalnız veritabanı kataloğundan."""
    from fastapi.testclient import TestClient

    import app.main as m
    from app.dictionary.repository import DataDictionary

    m.state.services = services
    m.state.agent = type("A", (), {"services": services})()
    monkeypatch.setattr(m, "build_services", lambda: services)
    c = TestClient(m.app)
    body = {"data": {"server": "PASIFIK", "database": "EDWDM"}, "dictionary": {"kind": "none"}}
    t = c.post("/api/settings/connections/test", json={"target": "dictionary", **body}).json()
    assert t["ok"] and t["database"] == "Sözlük kullanılmıyor"
    assert c.put("/api/settings/connections", json=body).status_code == 200
    assert conns.load_connections()["dictionary"]["kind"] == "none" and conns.dictionary_odbc(settings) is None
    called = {"catalog": False}
    monkeypatch.setattr(DataDictionary, "_merge_catalog", lambda self, catalog=None: called.__setitem__("catalog", True))
    dd = DataDictionary(settings, services.connector).load()   # sözlük okunmadan yüklenir
    # sözlük tablosu okunmadı; yalnız uygulamanın onaylı view kayıtları (views.json) eklenir
    assert all(t.table_type == "view" for t in dd.tables.values()) and called["catalog"]


def test_sections_saved_and_reset_independently(settings, services, conn_file, monkeypatch):
    """Veri kaynağı ve sözlük ayrı kaydedilir; "aynı sunucu" sözlüğü veri kaynağının GEÇERLİ ayarını izler (.env ya da arayüz)."""
    from fastapi.testclient import TestClient

    import app.main as m
    from app.dictionary import sources as srcmod

    m.state.services = services
    m.state.agent = type("A", (), {"services": services})()
    monkeypatch.setattr(m, "build_services", lambda: services)
    monkeypatch.setattr(conns, "open_dictionary_reader", lambda fields: (None, {}))
    monkeypatch.setattr(srcmod, "collect", lambda reader, src, *a: srcmod.Collected())
    env = "DRIVER={ODBC Driver 17 for SQL Server};SERVER=PASIFIK;DATABASE=EDWDM;Trusted_Connection=yes;"
    monkeypatch.setattr(m, "get_settings", lambda: settings.model_copy(update={"sqlserver_odbc": env}))
    monkeypatch.setattr(conns, "get_settings", lambda: settings.model_copy(update={"sqlserver_odbc": env}))
    c = TestClient(m.app)
    # 1) yalnız sözlük: veri kaynağı .env'den gelir, sözlük aynı sunucuda BI_Meta
    r = c.put("/api/settings/connections/dictionary", json={"dictionary": {"kind": "sqlserver", "same_as_data": True, "database": "BI_Meta"}})
    assert r.status_code == 200
    cfg = conns.load_connections()
    assert set(cfg) == {"dictionary"} and "server" not in cfg["dictionary"]          # sunucu kopyalanmaz
    odbc = conns.dictionary_odbc(settings.model_copy(update={"sqlserver_odbc": env}))
    assert "SERVER=PASIFIK" in odbc and "DATABASE=BI_Meta" in odbc
    e = c.get("/api/settings/connections").json()
    assert e["data_source"] == "env" and e["dictionary_source"] == "ui" and e["dictionary"]["server"] == "PASIFIK"
    # 2) yalnız veri kaynağı: sözlük artık yeni sunucuyu izler
    assert c.put("/api/settings/connections/data", json={"data": {"server": r"YENI\BI", "database": "DW"}}).status_code == 200
    assert r"SERVER=YENI\BI" in conns.dictionary_odbc(settings) and "DATABASE=BI_Meta" in conns.dictionary_odbc(settings)
    # 3) bölüm sıfırlama diğerine dokunmaz
    assert c.delete("/api/settings/connections/data").status_code == 200
    assert set(conns.load_connections()) == {"dictionary"}
    assert c.delete("/api/settings/connections/dictionary").status_code == 200
    assert not conn_file.exists()
