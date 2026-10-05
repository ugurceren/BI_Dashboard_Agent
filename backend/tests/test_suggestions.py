"""Yeni rapor önerileri: kural katmanı (yetkili veriden) ve LLM katmanı (iş diline çevirme, önbellek)."""

import json

from app import suggestions as sugg
from app.dictionary.repository import DataDictionary, DDColumn, DDRelationship, DDTable
from app.llm.gateway import AssistantTurn

from tests.conftest import FakeLLM


def col(table, name, typ="", role="attribute", bn="", pii=False):
    return DDColumn(table, name.lower(), bn or name, "", typ, role, None, [], pii, "", display_name=name)


def _dd(settings):
    dd = DataDictionary(settings)
    fact = DDTable("clt.vrepurchaseguarantee", "Geri Alım Garantileri", "Geri alım garantisi bilgileri", "Kredi", "", 1200,
                   display_name="CLT.vRepurchaseGuarantee", table_type="")
    fact.columns = [col(fact.name, "GuaranteeAmount", "decimal", bn="Teminat Tutarı"),
                    col(fact.name, "CustomerPartyId", "int"),                      # anahtar: ölçü değil
                    col(fact.name, "CollateralName", "nvarchar", bn="Teminat Adı"),
                    col(fact.name, "CustomerName", "nvarchar", pii=True),         # PII: kırılım değil
                    col(fact.name, "Gender", "nvarchar"),                         # kişisel: kırılım değil
                    col(fact.name, "GuaranteeDate", "date"),
                    col(fact.name, "BranchKey", "int")]
    branch = DDTable("dbo.dimbranch", "Şube", "", "Kredi", "", 50, display_name="dbo.DimBranch")
    branch.columns = [col(branch.name, "BranchKey", "int"), col(branch.name, "BranchName", "nvarchar", bn="Şube Adı")]
    empty = DDTable("dbo.bos", "Boş Tablo", "", "Kredi", "", 0, display_name="dbo.Bos")
    empty.columns = [col(empty.name, "Tutar", "money"), col(empty.name, "Tip", "nvarchar")]
    secret = DDTable("gizli.maas", "Maaşlar", "", "İK", "", 10, display_name="gizli.Maas")
    secret.columns = [col(secret.name, "Tutar", "money"), col(secret.name, "Birim", "nvarchar")]
    dd.tables = {t.name: t for t in (fact, branch, empty, secret)}
    dd.relationships = [DDRelationship("r1", fact.name, branch.name, [("branchkey", "branchkey")], "N:1")]
    return dd


def test_rule_suggestions_use_accessible_data_only(settings):
    dd = _dd(settings)
    items = sugg.rule_suggestions(dd, lambda t: not t.name.startswith("gizli."),
                                  reports=[{"id": "r9", "title": "Teminat Raporu", "tables": ["clt.vrepurchaseguarantee"]}])
    assert [i["table"] for i in items] == ["CLT.vRepurchaseGuarantee"]   # boyut (şube), boş tablo, yetkisiz tablo yok
    s = items[0]
    assert s["measures"] == ["Teminat Tutarı"] and s["time"] is True
    assert s["dims"][0] == "Şube"                                         # ilişkili boyut: kendi iş adıyla
    assert "Teminat Adı" in s["dims"] and not {"CustomerName", "Gender"} & set(s["dims"])
    assert s["text"].startswith("Geri Alım Garantileri: Teminat Tutarı aylık trendi")
    assert s["similar_report"] == {"id": "r9", "title": "Teminat Raporu"}


def test_human_names_and_offset_rotation(settings):
    assert sugg.human("vRepurchaseGuarantee") == "Repurchase Guarantee"
    assert sugg.human("vw_satis_ozeti") == "Satis ozeti" and sugg.human("dbo.SalesAmount") == "Sales Amount"
    dd = _dd(settings)
    a = sugg.rule_suggestions(dd, lambda t: True, offset=0, limit=1)
    b = sugg.rule_suggestions(dd, lambda t: True, offset=1, limit=1)
    assert a and b and a[0]["table"] != b[0]["table"]                     # 'Yenile' başka öneri getirir


def test_polish_uses_llm_then_cache_and_falls_back(settings, tmp_path):
    dd = _dd(settings)
    items = sugg.rule_suggestions(dd, lambda t: True)
    good = AssistantTurn(json.dumps([f"Öneri {n} istiyorum" for n in range(len(items))], ensure_ascii=False))
    llm = FakeLLM([good], settings)
    assert sugg.polish(llm, items, tmp_path) == [f"Öneri {n} istiyorum" for n in range(len(items))]
    assert "UYDURMA" in llm.calls[0]["messages"][0]["content"]           # uydurma metrik yasağı istemde
    again = FakeLLM([], settings)                                        # ikinci kez: önbellekten, LLM çağrılmaz
    assert sugg.polish(again, items, tmp_path) is not None and not again.calls
    bad = FakeLLM([AssistantTurn("üzgünüm, yapamam")], settings)
    other = [dict(i, text=i["text"] + " x") for i in items]
    assert sugg.polish(bad, other, tmp_path) is None                     # biçim bozuksa kural metni kalır


def test_suggestions_endpoints(settings, services, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import app.main as m

    m.state.services = services
    monkeypatch.setattr(m.state, "store", type("S", (), {"list": lambda self: []})(), raising=False)
    monkeypatch.setattr(m.state, "gateway", FakeLLM([], settings), raising=False)
    monkeypatch.setattr(settings, "cache_dir", tmp_path)   # gerçek backend/cache klasörüne yazılmasın
    monkeypatch.setattr(m, "get_settings", lambda: settings)
    c = TestClient(m.app)
    r = c.get("/api/suggestions").json()
    assert r["items"] and not r["polished"]
    n = len(r["items"])
    m.state.gateway.script = [AssistantTurn(json.dumps([f"İstek {k}" for k in range(n)], ensure_ascii=False))]
    p = c.post("/api/suggestions/polish").json()
    assert p["polished"] and p["items"][0]["text"] == "İstek 0" and p["items"][0]["rule_text"]
    again = c.get("/api/suggestions").json()                             # önbellekte: GET doğrudan düzenlenmiş döner
    assert again["polished"] and again["items"][0]["text"] == "İstek 0"


def test_view_variants_do_not_duplicate_suggestions(settings):
    """Masked / PersonnelExcluded varyantları: ana view erişilebilirse öneri tekrarlanmaz; yalnız varyanta erişim
    varsa öneri varyanttan gelir."""
    dd = _dd(settings)
    base = dd.tables["clt.vrepurchaseguarantee"]
    v = DDTable("clt.vrepurchaseguaranteemasked", "Geri Alım Garantileri (maskeli)", base.description, "Kredi", "", 1200,
                display_name="CLT.vRepurchaseGuaranteeMasked", variant_of=base.name)
    v.columns = [col(v.name, "GuaranteeAmount", "decimal", bn="Teminat Tutarı"), col(v.name, "GuaranteeDate", "date")]
    dd.tables[v.name] = v
    both = [i["table"] for i in sugg.rule_suggestions(dd, lambda t: True)]
    assert "CLT.vRepurchaseGuarantee" in both and "CLT.vRepurchaseGuaranteeMasked" not in both
    only_masked = [i["table"] for i in sugg.rule_suggestions(dd, lambda t: t.name != base.name)]
    assert "CLT.vRepurchaseGuaranteeMasked" in only_masked
