"""Vitrin platformu (sunucu modu): roller, tasarım oturumu yetkileri, yayınlama, paylaşım, izleyicinin veri rolü, denetim."""

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import authz
from app.harness.session import Session, SessionStore
from app.harness.tools import ToolContext, h_create_report_spec
from app.identity import Identity
from app.meta.store import MetaStore
from app.spec.models import Dataset, DatasetField

ADMIN, ALI, VELI, AYSE = "KURUM\\admin", "KURUM\\ali", "KURUM\\veli", "KURUM\\ayse"


def H(user: str, groups: str = "") -> dict[str, str]:
    return {"X-Remote-User": user, **({"X-Remote-Groups": groups} if groups else {})}


@pytest.fixture()
def srv(settings, services, monkeypatch):
    s = settings.model_copy(update={"platform_mode": "server", "platform_admins": ADMIN})
    monkeypatch.setattr(m, "get_settings", lambda: s)
    monkeypatch.setattr(m.state, "services", services, raising=False)
    monkeypatch.setattr(m.state, "store", SessionStore(settings.sessions_dir), raising=False)
    meta = MetaStore.sqlite(":memory:")
    monkeypatch.setattr(m.state, "meta", meta, raising=False)
    # ali tasarımcı (builder); veli ve ayşe varsayılan: izleyici (viewer)
    meta.set_assignment("user", ALI, "builder", None, "test")
    return TestClient(m.app)


def _report(owner: str, title: str = "Satış Özeti", pii: bool = False) -> Session:
    ds = [Dataset(id="kpi", sql="SELECT SUM(f.SalesAmount) AS amount FROM dbo.FactResellerSales f WHERE f.SalesAmount > 0",
                  fields=[DatasetField(name="amount", type="number")])]
    visuals = [{"id": "k1", "type": "kpi", "title": "Tutar", "datasetId": "kpi", "encoding": {"value": "amount"}}]
    if pii:
        ds.append(Dataset(id="cust", sql="SELECT c.BirthDate AS dogum FROM dbo.DimCustomer c WHERE c.CustomerKey > 0",
                          fields=[DatasetField(name="dogum")]))
        visuals.append({"id": "t1", "type": "table", "title": "Müşteri", "datasetId": "cust", "encoding": {"columns": ["dogum"]}})
    s = Session(phase="design", datasets=ds, owner=owner, owner_name=owner, user_role="admin", title=title)
    r = h_create_report_spec(ToolContext(s, m.state.services), {"spec": {"title": title, "visuals": visuals}})
    assert r.ok, r.content
    m.state.store.save(s)
    return s


def test_role_resolution(srv):
    s = m.get_settings()
    meta = m.state.meta
    meta.set_assignment("group", "BI_Yonetim", "admin", None, "test")
    meta.set_assignment("group", "Satis", None, "admin", "test")      # yalnız veri rolü
    r = lambda u, g=(): authz.resolve(s, meta, Identity(username=u, display_name=u, groups=list(g)))
    assert r(ADMIN).platform_role == "admin"                           # PLATFORM_ADMINS
    assert r(ALI).platform_role == "builder" and r("ali").platform_role == "viewer"   # tam ad eşleşir; kısa ad başka kişi olabilir
    assert r(VELI).platform_role == "viewer" and r(VELI).role == "standart"
    assert r(VELI, ["bi_yonetim"]).platform_role == "admin"           # grup (büyük/küçük harf duyarsız)
    assert r(VELI, ["Satis"]).role == "admin" and r(VELI, ["Satis"]).platform_role == "viewer"


def test_server_mode_requires_proxy_identity(srv):
    assert srv.get("/api/me").status_code == 401
    me = srv.get("/api/me", headers=H(VELI)).json()
    assert me["platform_role"] == "viewer" and me["capabilities"] == {"design": False, "admin": False, "vitrin": True}


def test_viewer_cannot_design_or_query(srv):
    for method, url in [("get", "/api/sessions"), ("post", "/api/sessions"), ("get", "/api/query/schema"),
                        ("get", "/api/me/access"), ("get", "/api/dictionary/model"), ("get", "/api/settings/llm"),
                        ("get", "/api/admin/overview")]:
        assert getattr(srv, method)(url, headers=H(VELI)).status_code == 403, url


def test_session_owner_matrix(srv):
    s = _report(ALI)
    other = _report(AYSE, "Ayşe'nin raporu")
    assert srv.get(f"/api/sessions/{s.id}", headers=H(ALI)).status_code == 200
    assert srv.get(f"/api/sessions/{s.id}", headers=H(ADMIN)).status_code == 200
    m.state.meta.set_assignment("user", VELI, "builder", None, "test")
    for method, url in [("get", f"/api/sessions/{s.id}"), ("delete", f"/api/sessions/{s.id}"),
                        ("get", f"/api/sessions/{s.id}/dashboard-data"), ("get", f"/api/sessions/{s.id}/filters"),
                        ("put", f"/api/sessions/{s.id}/status")]:
        kw = {"json": {"status": "test"}} if method == "put" else {}
        assert getattr(srv, method)(url, headers=H(VELI), **kw).status_code == 403, url
    ids = lambda u: [x["id"] for x in srv.get("/api/sessions", headers=H(u)).json()]   # noqa: E731
    assert ids(ALI) == [s.id] and set(ids(ADMIN)) == {s.id, other.id}


def test_same_title_does_not_delete_other_users_report(srv):
    a = _report(ALI, "Ortak Ad")
    b = _report(AYSE, "ortak  AD")
    assert {a.id, b.id} <= {x["id"] for x in m.state.store.list()}


def test_publish_share_and_view(srv):
    s = _report(ALI)
    assert srv.put(f"/api/sessions/{s.id}/status", json={"status": "live"}, headers=H(ALI)).status_code == 409   # Yayınla ile
    r = srv.post(f"/api/sessions/{s.id}/publish", headers=H(ALI), json={
        "description": "Bayi satışları", "notes": "ilk sürüm", "grants": [{"principal_type": "group", "principal": "Satis"}]})
    assert r.status_code == 200, r.text
    rid = r.json()["report"]["id"]
    assert m.state.store.get(s.id).status == "live"
    # izinsiz izleyici göremez, gruptaki görür
    assert srv.get("/api/vitrin", headers=H(VELI)).json() == []
    assert srv.get(f"/api/vitrin/{rid}", headers=H(VELI)).status_code == 404
    lst = srv.get("/api/vitrin", headers=H(VELI, "Satis")).json()
    assert [x["id"] for x in lst] == [rid] and lst[0]["can_manage"] is False and lst[0]["can_export"] is False
    assert lst[0]["session_id"] is None                                     # izleyici tasarım oturumunu bilmez
    assert srv.get("/api/vitrin", headers=H(ALI)).json()[0]["session_id"] == s.id   # sahibi "Tasarımda aç"
    got = srv.get(f"/api/vitrin/{rid}", headers=H(VELI, "Satis")).json()
    assert got["spec"]["datasets"][0]["sql"] == "" and "transcript" not in got   # SQL ve sohbet izleyiciye gitmez
    d = srv.post(f"/api/vitrin/{rid}/data", headers=H(VELI, "Satis"), json={"selections": []}).json()
    assert d["datasets"]["kpi"]["rows"]
    # dışa aktarma izni yok → 403; izin verilince açılır
    assert srv.get(f"/api/vitrin/{rid}/export/html", headers=H(VELI, "Satis")).status_code == 403
    # izleyici paylaşımı değiştiremez
    assert srv.put(f"/api/vitrin/{rid}/grants", headers=H(VELI, "Satis"), json=[]).status_code == 403


def test_published_version_is_immutable(srv):
    s = _report(ALI)
    rid = srv.post(f"/api/sessions/{s.id}/publish", headers=H(ALI),
                   json={"grants": [{"principal_type": "user", "principal": VELI}]}).json()["report"]["id"]
    s = m.state.store.get(s.id)
    s.spec.title = "Değişti"
    s.spec_version += 1
    m.state.store.save(s)
    assert srv.get(f"/api/vitrin/{rid}", headers=H(VELI)).json()["spec"]["title"] == "Satış Özeti"
    pub = srv.get(f"/api/sessions/{s.id}/publication", headers=H(ALI)).json()
    assert pub["unpublished_changes"] is True and pub["report"]["version"] == 1
    srv.post(f"/api/sessions/{s.id}/publish", headers=H(ALI), json={})              # paylaşım korunur
    assert srv.get(f"/api/vitrin/{rid}", headers=H(VELI)).json()["spec"]["title"] == "Değişti"
    assert srv.get(f"/api/sessions/{s.id}/publication", headers=H(ALI)).json()["report"]["version"] == 2


def test_viewer_data_role_applies(srv):
    """Kişisel veri kolonu kullanan görsel 'standart' rolde erişim dışı; diğer görseller çalışır."""
    s = _report(ALI, pii=True)
    rid = srv.post(f"/api/sessions/{s.id}/publish", headers=H(ALI),
                   json={"grants": [{"principal_type": "user", "principal": VELI}]}).json()["report"]["id"]
    d = srv.post(f"/api/vitrin/{rid}/data", headers=H(VELI), json={"selections": []}).json()["datasets"]
    assert d["kpi"]["rows"] and d["cust"].get("denied") is True
    m.state.meta.set_assignment("user", VELI, None, "admin", "test")              # veri rolü yükseltildi
    d = srv.post(f"/api/vitrin/{rid}/data", headers=H(VELI), json={"selections": []}).json()["datasets"]
    assert not d["cust"].get("denied")


def test_retire_and_delete_rules(srv):
    s = _report(ALI)
    rid = srv.post(f"/api/sessions/{s.id}/publish", headers=H(ALI),
                   json={"grants": [{"principal_type": "user", "principal": VELI, "can_export": True}]}).json()["report"]["id"]
    assert srv.delete(f"/api/sessions/{s.id}", headers=H(ALI)).status_code == 409   # yayındaki rapor silinmez
    assert srv.post(f"/api/vitrin/{rid}/retire", headers=H(ALI)).status_code == 200
    assert srv.get("/api/vitrin", headers=H(VELI)).json() == []
    assert m.state.store.get(s.id).status == "test"
    assert srv.delete(f"/api/sessions/{s.id}", headers=H(ALI)).status_code == 200


def test_admin_assignments_and_audit(srv):
    r = srv.put("/api/admin/assignments", headers=H(ADMIN),
                json={"principal_type": "group", "principal": "BI_Tasarim", "platform_role": "builder"})
    assert r.status_code == 200
    assert srv.post("/api/sessions", headers=H(AYSE, "BI_Tasarim")).status_code == 200
    assert srv.put("/api/admin/assignments", headers=H(ADMIN),
                   json={"principal_type": "user", "principal": AYSE, "platform_role": "kral"}).status_code == 400
    s = _report(ALI)
    srv.post(f"/api/sessions/{s.id}/publish", headers=H(ALI), json={})
    events = [e["event"] for e in srv.get("/api/admin/audit", headers=H(ADMIN)).json()]
    assert "role_assign" in events and "report_publish" in events
    assert srv.get("/api/admin/audit", headers=H(ALI)).status_code == 403


def test_desktop_mode_unchanged(settings, services, monkeypatch):
    """Masaüstü modu: başlık gerekmez, tek kullanıcı admin; mevcut akış aynen çalışır."""
    monkeypatch.setattr(m, "get_settings", lambda: settings)
    monkeypatch.setattr(m.state, "services", services, raising=False)
    monkeypatch.setattr(m.state, "store", SessionStore(settings.sessions_dir), raising=False)
    monkeypatch.setattr(m.state, "meta", MetaStore.sqlite(":memory:"), raising=False)
    c = TestClient(m.app)
    me = c.get("/api/me").json()
    assert me["platform_role"] == "admin" and me["capabilities"]["design"]
    assert c.post("/api/sessions").status_code == 200 and len(c.get("/api/sessions").json()) == 1
