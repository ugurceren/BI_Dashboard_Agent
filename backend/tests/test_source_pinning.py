"""Raporlar oluşturuldukları veri kaynağına sabitlenir: bağlantı ayarı başka sunucu / veritabanına çevrilse de rapor
verisini kendi kaynağından (güncel olarak) okur; erişim her açılışta izleyicinin güncel veri rolüyle denetlenir."""

import json
import time

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app.data import connections as conns
from app.dictionary.repository import DataDictionary
from app.harness.session import SessionStore
from app.meta.store import MetaStore
from tests.conftest import FIXTURE, FakeConnector, _services
from tests.test_platform import ALI, VELI, H, ADMIN

KPI_SQL = "SELECT SUM(f.SalesAmount) AS amount FROM dbo.FactResellerSales f WHERE f.SalesAmount > 0"
PII_SQL = "SELECT c.BirthDate AS dogum FROM dbo.DimCustomer c WHERE c.CustomerKey > 0"


def _source(settings, sid: str, drop: tuple[str, ...] = ()):
    """Sahte veri kaynağı: kendi bağlantısı ve sözlüğü (drop: bu kaynakta olmayan tablolar)."""
    snap = json.loads(FIXTURE.read_text(encoding="utf-8"))
    snap["tables"] = [t for t in snap["tables"] if t["name"].lower() not in drop]
    con = FakeConnector()
    svc = _services(settings, DataDictionary.from_snapshot(settings, snap, con), con)
    svc.source_id, svc.source_label = sid, f"SRV · {sid}"
    return svc


@pytest.fixture()
def two(settings, monkeypatch, conn_file):
    """A: raporun yapıldığı kaynak; B: sonradan bağlantı ayarında seçilen (FactResellerSales / DimCustomer yok)."""
    a = _source(settings, "srcA")
    b = _source(settings, "srcB", drop=("dbo.factresellersales", "dbo.dimcustomer"))
    s = settings.model_copy(update={"platform_mode": "server", "platform_admins": ADMIN})
    monkeypatch.setattr(m, "get_settings", lambda: s)
    monkeypatch.setattr(m.state, "services", a, raising=False)
    monkeypatch.setattr(m.state, "store", SessionStore(settings.sessions_dir), raising=False)
    monkeypatch.setattr(m.state, "meta", MetaStore.sqlite(":memory:"), raising=False)
    m.state.meta.set_assignment("user", ALI, "builder", None, "test")
    m._forget_source()
    with m._cache_lock:
        m._cache.clear()
    m._options_cache.clear()

    def switch_to_b():
        """Bağlantı ayarı B'ye çevrildi: A arşivde (servisleri bu testte doğrudan önbelleğe konur)."""
        m.state.services = b
        m._sources["srcA"] = (time.time(), a)

    yield TestClient(m.app), a, b, switch_to_b
    m._forget_source()


def _build_report(c, pii: bool = False) -> str:
    sid = c.post("/api/sessions", headers=H(ALI)).json()["id"]
    if pii:   # kişisel veri kolonlu dataset'i yetkili (admin veri rolü) tasarımcı kaydeder
        m.state.meta.set_assignment("user", ALI, "builder", "admin", "test")
    qs = [{"id": "kpi", "title": "Satış", "sql": KPI_SQL}] + ([{"id": "cust", "title": "Müşteri", "sql": PII_SQL}] if pii else [])
    r = c.post(f"/api/sessions/{sid}/datasets/from-query", headers=H(ALI), json={"datasets": qs})
    assert r.status_code == 200, r.text
    visuals = [{"id": "k1", "type": "kpi", "title": "Tutar", "datasetId": "kpi", "encoding": {"value": "amount"}}]
    if pii:
        visuals.append({"id": "t1", "type": "table", "title": "Müşteri", "datasetId": "cust", "encoding": {"columns": ["dogum"]}})
    s = m.state.store.get(sid)
    spec = {"title": "Satış", "datasets": [d.model_dump() for d in s.datasets], "visuals": visuals}
    assert c.put(f"/api/sessions/{sid}/spec", headers=H(ALI), json=spec).status_code == 200
    m.state.meta.set_assignment("user", ALI, "builder", None, "test")    # veri rolü sonradan geri alındı
    return sid


def test_report_is_stamped_and_reads_its_own_source_after_switch(two):
    c, a, b, switch_to_b = two
    sid = _build_report(c)
    s = m.state.store.get(sid)
    assert s.data_source == "srcA" and s.data_source_label == "SRV · srcA"
    assert c.get(f"/api/sessions/{sid}", headers=H(ALI)).json()["data_source"] == "srcA"
    switch_to_b()
    a.connector.executed.clear()
    d = c.get(f"/api/sessions/{sid}/dashboard-data", headers=H(ALI)).json()["datasets"]["kpi"]
    assert d["rows"] and not d.get("denied") and not d.get("error")
    assert a.connector.executed and not b.connector.executed          # veri A'dan; B'de bu tablo yok bile


def test_published_report_reads_pinned_source_and_viewer_role_applies(two):
    c, a, b, switch_to_b = two
    sid = _build_report(c, pii=True)
    rid = c.post(f"/api/sessions/{sid}/publish", headers=H(ALI),
                 json={"grants": [{"principal_type": "user", "principal": VELI}]}).json()["report"]["id"]
    assert m.state.meta.version(rid)["data_source"] == {"id": "srcA", "label": "SRV · srcA"}
    switch_to_b()
    d = c.post(f"/api/vitrin/{rid}/data", headers=H(VELI), json={"selections": []}).json()["datasets"]
    assert d["kpi"]["rows"] and not b.connector.executed
    assert d["cust"].get("denied") is True                           # A'nın kişisel veri kuralı, izleyicinin rolüyle
    m.state.meta.set_assignment("user", VELI, None, "admin", "test")   # veri rolü yükseltildi → görür
    d = c.post(f"/api/vitrin/{rid}/data", headers=H(VELI), json={"selections": []}).json()["datasets"]
    assert d["cust"]["rows"] and not d["cust"].get("denied")
    assert c.get(f"/api/vitrin/{rid}", headers=H(VELI)).status_code == 200
    # paylaşım kaldırılırsa (rapor erişimi) kaynak fark etmeksizin görülmez
    assert c.put(f"/api/vitrin/{rid}/grants", headers=H(ALI), json=[]).status_code == 200
    assert c.post(f"/api/vitrin/{rid}/data", headers=H(VELI), json={"selections": []}).status_code == 404


def test_session_data_uses_callers_current_role(two):
    c, a, b, switch_to_b = two
    sid = _build_report(c, pii=True)       # admin veri rolüyle kaydedildi
    s = m.state.store.get(sid)
    assert s.user_role == "admin"
    d = c.get(f"/api/sessions/{sid}/dashboard-data", headers=H(ALI)).json()["datasets"]
    assert d["cust"].get("denied") is True  # sahibin bugünkü rolü standart: kayıttaki eski rol kullanılmaz


def test_missing_source_is_an_error_not_denied(two):
    c, a, b, switch_to_b = two
    sid = _build_report(c)
    m.state.services = b                    # A arşivde de yok (silinmiş)
    d = c.get(f"/api/sessions/{sid}/dashboard-data", headers=H(ALI)).json()["datasets"]["kpi"]
    assert not d.get("denied") and "kayıtlı değil" in d["error"] and not b.connector.executed


def test_cache_does_not_leak_between_sources(two):
    c, a, b, switch_to_b = two
    sql = "SELECT SUM(f.SalesAmount) AS amount FROM dbo.FactInternetSales f"
    m._dataset_payload(sql, "standart", a)
    m._dataset_payload(sql, "standart", b)
    assert len(a.connector.executed) == 1 and len(b.connector.executed) == 1
    m._dataset_payload(sql, "standart", a)
    assert len(a.connector.executed) == 1                              # aynı kaynakta önbellekten


def test_legacy_sessions_stamped_on_startup(two):
    c, a, b, switch_to_b = two
    sid = _build_report(c)
    s = m.state.store.get(sid)
    s.data_source = s.data_source_label = None
    m.state.store.save(s)
    empty = m.state.store.create("standart")
    assert m._stamp_legacy_sessions() == 1
    assert m.state.store.get(sid).data_source == "srcA" and m.state.store.get(empty.id).data_source is None


def test_agent_turn_uses_report_source(two, settings):
    from app.harness.agent import Agent
    from tests.conftest import FakeLLM, call
    from app.llm.gateway import AssistantTurn
    c, a, b, switch_to_b = two
    sid = _build_report(c)
    switch_to_b()
    llm = FakeLLM([AssistantTurn("", [call("run_sql", sql=KPI_SQL, purpose="kontrol")]), AssistantTurn("tamam")], settings)
    agent = Agent(llm, m.state.services, m.state.store, m.services_for)
    list(agent.run_turn(sid, "toplamı kontrol et"))
    assert any("FactResellerSales" in q for q in a.connector.executed) and not b.connector.executed


# ------------------------------------------------------------------ arşiv (connections.json → sources)
def test_archive_on_switch_and_override(settings, conn_file, monkeypatch):
    monkeypatch.setattr(conns, "get_settings", lambda: settings)
    conns.save_connections({"data": {"server": "ESKI", "database": "DW", "auth": "sql", "username": "ro",
                                     "password_enc": conns.protect("pw1")}})
    old = conns.active_source_id(settings)
    conns.save_connections({"data": {"server": "YENI", "database": "DW"}})
    assert conns.active_source_id(settings) != old
    arch = conns.archived_source(old)
    assert arch["label"] == "ESKI · DW" and "pw1" not in conn_file.read_text(encoding="utf-8")
    cfg = conns.source_config(old)
    with conns.override_connections(cfg):
        assert "SERVER=ESKI" in conns.data_odbc(settings) and "PWD={pw1}" in conns.data_odbc(settings)
    assert "SERVER=YENI" in conns.data_odbc(settings)                 # bağlam dışında etkin ayar
    # aynı yere dönülünce kimlik aynı; kimlik bilgisi güncellenebilir
    assert conns.update_source_credentials(old, "ro2", "pw2")
    with conns.override_connections(conns.source_config(old)):
        assert "UID=ro2" in conns.data_odbc(settings) and "PWD={pw2}" in conns.data_odbc(settings)
    assert conns.update_source_credentials(old, "", None)            # kullanıcı adı boş → Windows oturumu
    with conns.override_connections(conns.source_config(old)):
        assert "UID=" not in conns.data_odbc(settings) and "Trusted_Connection=yes" in conns.data_odbc(settings)
    listed = {x["id"]: x for x in conns.list_sources(settings)}
    assert listed[old]["active"] is False and listed[conns.active_source_id(settings)]["active"] is True
    assert conns.delete_source(old) and conns.archived_source(old) is None


def test_env_source_is_archived_encrypted(settings, conn_file, monkeypatch):
    env = "DRIVER={ODBC Driver 17 for SQL Server};SERVER=PASIFIK;DATABASE=EDWDM;UID=u;PWD=gizli;"
    st = settings.model_copy(update={"sqlserver_odbc": env})
    monkeypatch.setattr(conns, "get_settings", lambda: st)
    env_id = conns.active_source_id(st)
    conns.save_connections({"data": {"server": "YENI", "database": "DW"}})
    assert "gizli" not in conn_file.read_text(encoding="utf-8")
    with conns.override_connections(conns.source_config(env_id)):
        assert conns.data_odbc(st) == env and conns.extra_databases(st) == []
        f = conns.dictionary_conn_fields(conns.load_connections(), st)
        assert "SERVER=PASIFIK" in f["odbc"] and "DATABASE=BI_Meta" in f["odbc"]
    assert conns.source_id_of(conns.archived_source(env_id)["data"], st) == env_id


def test_services_for_builds_archived_source_under_override(two, monkeypatch):
    c, a, b, switch_to_b = two
    conns.save_connections({"data": {"server": "ESKI", "database": "DW"}})
    old = conns.active_source_id()
    conns.save_connections({"data": {"server": "YENI", "database": "DW"}})
    m.state.services = b
    seen = []

    def fake_build(archived=False):
        seen.append((archived, conns.load_connections()["data"]["server"]))
        return a
    monkeypatch.setattr(m, "build_services", fake_build)
    assert m.services_for(old) is a and seen == [(True, "ESKI")]
    assert m.services_for(old) is a and len(seen) == 1                 # önbellekten
    assert m.services_for(None) is b and m.services_for("srcB") is b


def test_sources_endpoints(two):
    c, a, b, switch_to_b = two
    sid = _build_report(c)
    conns.save_connections({"data": {"server": "ESKI", "database": "DW"}})
    old = conns.active_source_id()
    conns.save_connections({"data": {"server": "YENI", "database": "DW"}})
    m.state.services = b
    b.source_id = conns.active_source_id()
    s = m.state.store.get(sid)
    s.data_source = old
    m.state.store.save(s)
    lst = {x["id"]: x for x in c.get("/api/settings/sources", headers=H(ADMIN)).json()}
    assert lst[old]["reports"] == 1 and lst[old]["label"] == "ESKI · DW" and not lst[old]["active"]
    assert c.get("/api/settings/sources", headers=H(VELI)).status_code == 403
    assert c.delete(f"/api/settings/sources/{old}", headers=H(ADMIN)).status_code == 409    # rapor bağlı
    m.state.store.delete(sid)
    assert c.delete(f"/api/settings/sources/{old}", headers=H(ADMIN)).status_code == 200
    assert old not in {x["id"] for x in c.get("/api/settings/sources", headers=H(ADMIN)).json()}


# ------------------------------------------------------------------ yayın (Vitrin) hesabı
def _publish(c, sid: str) -> str:
    return c.post(f"/api/sessions/{sid}/publish", headers=H(ALI),
                  json={"grants": [{"principal_type": "user", "principal": VELI}]}).json()["report"]["id"]


def test_vitrin_reads_with_publish_account_design_with_normal(two):
    c, a, b, switch_to_b = two
    sid = _build_report(c, pii=True)
    rid = _publish(c, sid)
    pub = FakeConnector()
    a.publish_connector = pub
    a.connector.executed.clear()
    d = c.post(f"/api/vitrin/{rid}/data", headers=H(VELI), json={"selections": []}).json()["datasets"]
    assert d["kpi"]["rows"] and pub.executed and not a.connector.executed      # Vitrin: yayın hesabı
    assert d["cust"].get("denied") is True                                      # izleyicinin rolü yine geçerli
    pub.executed.clear()
    c.get(f"/api/sessions/{sid}/dashboard-data", headers=H(ALI))
    assert a.connector.executed and not pub.executed                           # tasarım: normal bağlantı
    switch_to_b()                                                               # kaynak değişse de A'nın yayın hesabı
    with m._cache_lock:
        m._cache.clear()
    c.post(f"/api/vitrin/{rid}/data", headers=H(VELI), json={"selections": []})
    assert pub.executed and not b.connector.executed


def test_publish_account_bypasses_normal_accounts_object_permission_only(two):
    c, a, b, switch_to_b = two
    sid = _build_report(c)
    rid = _publish(c, sid)
    a.dictionary.tables["dbo.factresellersales"].can_select = False   # normal hesabın yetkisi geri alındı
    with m._cache_lock:
        m._cache.clear()
    assert c.get(f"/api/sessions/{sid}/dashboard-data", headers=H(ALI)).json()["datasets"]["kpi"].get("denied")
    assert c.post(f"/api/vitrin/{rid}/data", headers=H(VELI), json={"selections": []}).json()["datasets"]["kpi"].get("denied")
    a.publish_connector = FakeConnector()                              # yayın hesabı tanımlandı → Vitrin okur
    d = c.post(f"/api/vitrin/{rid}/data", headers=H(VELI), json={"selections": []}).json()["datasets"]["kpi"]
    assert d["rows"] and not d.get("denied")


def test_publish_account_storage(settings, conn_file, monkeypatch):
    monkeypatch.setattr(conns, "get_settings", lambda: settings)
    conns.save_connections({"data": {"server": "SRV", "database": "DW", "auth": "sql", "username": "ro",
                                     "password_enc": conns.protect("dar")}})
    sid = conns.active_source_id(settings)
    assert conns.publish_odbc(sid, settings) is None
    conns.save_publish_account(sid, {"auth": "sql", "username": "bi_vitrin", "password": "Güçlü;1}"}, "KURUM\admin")
    odbc = conns.publish_odbc(sid, settings)
    assert "UID=bi_vitrin" in odbc and "PWD={Güçlü;1}}}" in odbc and "UID=ro" not in odbc and "SERVER=SRV" in odbc
    assert "Güçlü" not in conn_file.read_text(encoding="utf-8")
    conns.save_publish_account(sid, {"auth": "sql", "username": "bi_vitrin"})          # şifre boş → korunur
    assert "PWD={Güçlü;1}}}" in conns.publish_odbc(sid, settings)
    conns.save_connections({"data": {"server": "SRV", "database": "DW", "auth": "sql", "username": "ro2"}})
    assert conns.publish_account(sid)                                                   # veri kartı kaydı silmez
    conns.save_publish_account(sid, {"auth": "windows"})
    o = conns.publish_odbc(sid, settings)
    assert "Trusted_Connection=yes" in o and "UID=" not in o and "PWD=" not in o
    pub = {x["id"]: x for x in conns.list_sources(settings)}[sid]["publish"]
    assert pub["auth"] == "windows" and "password_enc" not in pub
    conns.save_publish_account(sid, None)
    assert conns.publish_account(sid) is None


class _FakeSql:
    def __init__(self, odbc, timeout):
        self.odbc = odbc

    def execute(self, sql, n):
        from app.data.connector import QueryResult
        owner = 1 if "UID=sahip" in self.odbc else 0
        return QueryResult([], [], [["KURUM\bi_vitrin", "AW", 0, owner, 0, 0, 40, 42]], False, 1)


def test_publish_account_endpoints(two, monkeypatch):
    c, a, b, switch_to_b = two
    monkeypatch.setattr(m, "SqlServerConnector", _FakeSql)
    r = c.post("/api/settings/sources/srcA/publish-account/test", headers=H(ADMIN),
               json={"auth": "sql", "username": "sahip", "password": "x"}).json()
    assert r["ok"] and r["login"] == "KURUM\bi_vitrin" and r["databases"][0]["readable"] == 40
    assert any("yazma" in w for w in r["warnings"]) and any("2 tablo" in w for w in r["warnings"])
    assert c.post("/api/settings/sources/srcA/publish-account/test", headers=H(ADMIN),
                  json={"auth": "sql", "username": "u"}).status_code == 400                 # şifre gerekli
    assert c.put("/api/settings/sources/srcA/publish-account", headers=H(VELI), json={"auth": "windows"}).status_code == 403
    r = c.put("/api/settings/sources/srcA/publish-account", headers=H(ADMIN), json={"auth": "windows"}).json()
    assert r["publish"]["auth"] == "windows" and isinstance(a.publish_connector, _FakeSql)
    c.put("/api/settings/sources/srcA/publish-account", headers=H(ADMIN), json={"auth": "none"})
    assert a.publish_connector is None
    assert c.put("/api/settings/sources/yok/publish-account", headers=H(ADMIN), json={"auth": "windows"}).status_code == 404
