"""Model ilişkileri: agent sözlükte bulduğu tablolar arasında ilişki önerir, kullanıcı onaylayınca ortak modele kaydeder;
filtreler bu ilişkilerden yayılır, ilişkiler yeniden açılışta da yüklenir."""

import pytest

from app.data.model_filters import ModelFilterEngine
from app.dictionary import model_rels
from app.dictionary.repository import DDColumn, DDTable
from app.harness.session import Session, TranscriptItem
from app.harness.tools import ToolContext, h_propose_model, h_save_relationships


def _col(t, n, typ, role="attribute"):
    return DDColumn(t, n.lower(), n, "", typ, role, None, [], False, "", display_name=n)


@pytest.fixture()
def model(services, tmp_path, monkeypatch):
    s = services.settings.model_copy(update={"model_relationships": tmp_path / "model_relationships.json"})
    services.settings = s
    dd = services.dictionary
    dd.settings = s
    g = DDTable("clt.vguarantee", "Garantiler", "", "Kredi", "", 1000, display_name="CLT.vGuarantee")
    g.columns = [_col(g.name, "DataDate", "date"), _col(g.name, "CustomerPartyId", "int"), _col(g.name, "Amount", "decimal", "measure")]
    c = DDTable("cus.vcustomer", "Müşteriler", "", "Müşteri", "", 50, display_name="CUS.vCustomer")
    c.columns = [_col(c.name, "DataDate", "date"), _col(c.name, "CustomerPartyId", "int"), _col(c.name, "Segment", "nvarchar", "dimension")]
    dd.tables[g.name], dd.tables[c.name] = g, c
    dd.mark_snapshots()
    # tekillik: müşteri view'ında CustomerPartyId tekil, garantide değil (katalog / ölçüm yerine)
    monkeypatch.setattr(model_rels, "_side_unique", lambda dd_, con, key, col, info: (key == "cus.vcustomer", "test"))
    yield services
    dd.tables.pop(g.name, None)
    dd.tables.pop(c.name, None)
    dd.relationships = [r for r in dd.relationships if not r.id.startswith("model:")]


REL = {"from_table": "CLT.vGuarantee", "to_table": "CUS.vCustomer",
       "columns": [["CustomerPartyId", "CustomerPartyId"], ["DataDate", "DataDate"]], "cardinality": "N:1"}


def test_propose_name_match_with_snapshot_date_pair(model):
    ctx = ToolContext(Session(phase="data"), model)
    r = h_propose_model(ctx, {"tables": ["CLT.vGuarantee", "CUS.vCustomer"]})
    assert r.ok, r.content
    cand = next(c for c in r.content["candidates"] if c["columns"][0] == ["customerpartyid", "customerpartyid"])
    assert cand["from_table"] == "CLT.vGuarantee" and cand["to_table"] == "CUS.vCustomer" and cand["cardinality"] == "N:1"
    assert ["datadate", "datadate"] in cand["columns"]                   # iki anlık görüntü: tarih çifti
    assert {t["table"] for t in r.content["tables"]} == {"CLT.vGuarantee", "CUS.vCustomer"}


def test_save_requires_user_confirmation_after_proposal(model):
    s = Session(phase="data")
    ctx = ToolContext(s, model)
    assert "propose_model" in h_save_relationships(ctx, {"scope": "global", "relationships": [REL]}).content["error"]
    h_propose_model(ctx, {"tables": ["CLT.vGuarantee", "CUS.vCustomer"]})
    r = h_save_relationships(ctx, {"scope": "global", "relationships": [REL]})              # aynı turda, kullanıcı yanıt vermeden
    assert not r.ok and "onaylamadı" in r.content["error"]
    s.add(TranscriptItem(role="user", content="evet, bu ilişkilerle devam"))
    r = h_save_relationships(ctx, {"scope": "global", "relationships": [REL]})
    assert r.ok, r.content
    assert len(model_rels.RelationshipRegistry(model.settings.model_relationships).all()) == 1


def test_saved_relationship_routes_filters_and_survives_reload(model):
    s = Session(phase="data")
    ctx = ToolContext(s, model)
    h_propose_model(ctx, {"tables": ["CLT.vGuarantee", "CUS.vCustomer"]})
    s.add(TranscriptItem(role="user", content="onaylıyorum"))
    assert h_save_relationships(ctx, {"scope": "global", "relationships": [REL]}).ok
    dd = model.dictionary
    eng = ModelFilterEngine(dd, "tsql")
    assert "clt.vguarantee" in eng.paths_from("cus.vcustomer")            # müşteri filtresi garantiye yayılır
    dd.relationships = [r for r in dd.relationships if not r.id.startswith("model:")]   # yeniden açılış
    assert dd.apply_model_relationships() == 1 and "clt.vguarantee" in eng.paths_from("cus.vcustomer")


@pytest.mark.parametrize("rel,needle", [
    ({**REL, "cardinality": "N:N"}, "filtre yaymaz"),
    ({**REL, "columns": [["Yok", "CustomerPartyId"]]}, "kolon"),
    ({**REL, "to_table": "sys.objects"}, "bulunamadı"),
])
def test_invalid_relationships_rejected(model, rel, needle):
    s = Session(phase="data")
    ctx = ToolContext(s, model)
    h_propose_model(ctx, {"tables": ["CLT.vGuarantee", "CUS.vCustomer"]})
    s.add(TranscriptItem(role="user", content="evet"))
    r = h_save_relationships(ctx, {"scope": "global", "relationships": [rel]})
    assert not r.ok and needle in r.content["error"]


def test_admin_can_delete_model_relationship(model, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as m
    monkeypatch.setattr(m.state, "services", model, raising=False)
    monkeypatch.setattr(m, "get_settings", lambda: model.settings)
    s = Session(phase="data")
    ctx = ToolContext(s, model)
    h_propose_model(ctx, {"tables": ["CLT.vGuarantee", "CUS.vCustomer"]})
    s.add(TranscriptItem(role="user", content="evet"))
    h_save_relationships(ctx, {"scope": "global", "relationships": [REL]})
    rid = next(r.id for r in model.dictionary.relationships if r.id.startswith("model:"))
    c = TestClient(m.app)
    assert any(r.get("source") == "model" for r in c.get("/api/dictionary/model").json()["relationships"])
    assert c.delete(f"/api/dictionary/relationships/{rid}").json() == {"ok": True}
    assert not any(r.id == rid for r in model.dictionary.relationships)
    assert model_rels.RelationshipRegistry(model.settings.model_relationships).all() == []


def _confirmed(model):
    s = Session(phase="data")
    ctx = ToolContext(s, model)
    h_propose_model(ctx, {"tables": ["CLT.vGuarantee", "CUS.vCustomer"]})
    s.add(TranscriptItem(role="user", content="evet"))
    return s, ctx


def test_scope_must_be_asked(model):
    _s, ctx = _confirmed(model)
    r = h_save_relationships(ctx, {"relationships": [REL]})
    assert not r.ok and "ORTAK" in r.content["error"] and "YALNIZ BU RAPORA" in r.content["error"]


def test_report_scope_stays_in_report(model):
    from app.dictionary.model_rels import report_relationships
    s, ctx = _confirmed(model)
    r = h_save_relationships(ctx, {"scope": "report", "relationships": [REL]})
    assert r.ok and r.content["scope"] == "report"
    dd = model.dictionary
    assert not any(x.id.startswith(("model:", "report:")) for x in dd.relationships)          # ortak modele girmedi
    assert model_rels.RelationshipRegistry(model.settings.model_relationships).all() == []
    assert len(s.model_relationships) == 1
    assert "clt.vguarantee" not in ModelFilterEngine(dd, "tsql").paths_from("cus.vcustomer")   # başka rapor görmez
    eng = ModelFilterEngine(dd, "tsql", report_relationships(dd, s.model_relationships))
    assert "clt.vguarantee" in eng.paths_from("cus.vcustomer")                               # bu raporun filtreleri yayılır
    # aynı ilişki tekrar önerilirken "zaten var" görünür
    again = h_propose_model(ctx, {"tables": ["CLT.vGuarantee", "CUS.vCustomer"]}).content["candidates"]
    assert any(c.get("already_in_model", "").startswith("report:") for c in again)


def test_report_relationships_travel_with_published_version(model, monkeypatch):
    """Yayınlanan sürüm rapora özel ilişkileri de taşır: Vitrin'deki filtreler çalışır."""
    import app.main as m
    from app.meta.store import MetaStore
    from app.spec.models import ReportSpec
    meta = MetaStore.sqlite(":memory:")
    rel = {"id": "report:x", "from_table": "clt.vguarantee", "to_table": "cus.vcustomer",
           "pairs": [["customerpartyid", "customerpartyid"]], "cardinality": "N:1"}
    rep = meta.publish(session_id="s1", owner="u", owner_name="u", title="T", description=None, domains=[],
                       spec={"title": "T"}, datasets=[], notes=None, by="u", model_relationships=[rel])
    ver = meta.version(rep["report_id"])
    assert ver["datasets"] == [] and ver["model_relationships"] == [rel]
    monkeypatch.setattr(m.state, "services", model, raising=False)
    snap = m._snapshot(ver, "standart")
    assert "clt.vguarantee" in m._engine(snap).paths_from("cus.vcustomer")
    old = meta.publish(session_id="s2", owner="u", owner_name="u", title="T2", description=None, domains=[],
                       spec={"title": "T2"}, datasets=[{"id": "a", "sql": "SELECT 1"}], notes=None, by="u")
    assert meta.version(old["report_id"])["datasets"][0]["id"] == "a"                         # eski biçim bozulmadı


def test_search_limit_redirects_to_propose_model(model):
    """Yerel model aramayı bırakmazsa: 6 aramadan sonra sistem bulunan tablolarla model önerisini kendisi hazırlar."""
    from app.harness.tools import SEARCH_LIMIT_BEFORE_MODEL, h_search_dictionary
    s = Session(phase="data")
    ctx = ToolContext(s, model)
    for i in range(SEARCH_LIMIT_BEFORE_MODEL):
        assert h_search_dictionary(ctx, {"query": f"satış {i}"}).ok
    assert s.model_proposal_at is None
    r = h_search_dictionary(ctx, {"query": "bir arama daha"})
    assert r.ok and "ARAMA SINIRI" in r.content["note"] and r.content["tables"] and "candidates" in r.content
    assert s.model_proposal_at is not None                      # sistem öneriyi kendisi hazırladı → onay sorusuna geçilir
    r = h_search_dictionary(ctx, {"query": "kullanıcı yanıtlamadan"})
    assert not r.ok and "kullanıcı henüz yanıtlamadı" in r.content["error"]
    s.add(TranscriptItem(role="user", content="evet, şu tabloyu da ekle"))
    assert h_search_dictionary(ctx, {"query": "yanıttan sonra serbest"}).ok


def test_agent_stops_and_asks_when_model_keeps_calling_tools(model, settings):
    """Öneri hazırken model sormadan araç çağırmayı sürdürürse tur sistemce biter ve öneri onay sorusuyla gösterilir."""
    from app.harness.agent import Agent
    from app.harness.session import SessionStore
    from app.llm.gateway import AssistantTurn
    from tests.conftest import FakeLLM, call

    store = SessionStore(model.settings.sessions_dir)
    s = store.create("standart")
    s.set_phase("data")
    store.save(s)
    script = [AssistantTurn("", [call("propose_model", tables=["CLT.vGuarantee", "CUS.vCustomer"])])] + \
             [AssistantTurn("", [call("search_dictionary", query=f"yine arıyor {i}")]) for i in range(6)]
    llm = FakeLLM(script, settings)
    agent = Agent(llm, model, store)
    list(agent.run_turn(s.id, "veriyi hazırla"))
    s = store.get(s.id)
    last = s.transcript[-1]
    assert last.role == "assistant" and "CLT.vGuarantee" in last.content and "devam edeyim mi" in last.content
    assert len(llm.script) > 0                                     # model senaryosu bitmeden tur durduruldu
