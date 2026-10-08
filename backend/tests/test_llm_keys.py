"""LLM adresi başka bir sunucuya değişince kayıtlı API anahtarı yeni sunucuya gönderilmez (anahtar sızması);
LM Studio (Tailscale üzerinden uzak cihaz) bağlam penceresi ve hata biçimi."""

from types import SimpleNamespace

import pytest

import app.main as m
from app.main import LlmIn, _llm_keys, _origin


@pytest.fixture()
def saved_key(settings, monkeypatch):
    cur = settings.model_copy(update={"llm_base_url": "https://evren-llmapi.example.org/v1", "llm_api_key": "GIZLI-EVREN",
                                      "vision_base_url": None, "vision_api_key": None})
    monkeypatch.setattr(m.conns, "llm_settings", lambda s: cur)
    return cur


def _body(url, key=None, **vision):
    return LlmIn.model_validate({"base_url": url, "model": "m", "api_key": key,
                                 "vision": {"enabled": True, "same_as_main": True, "model": "v", "api_key": None, **vision}})


def test_origin():
    assert _origin("http://100.121.208.108:1234/v1") == "http://100.121.208.108:1234"
    assert _origin("https://evren-llmapi.example.org/v1/") == _origin("https://EVREN-llmapi.example.org/v1/chat")


def test_saved_key_not_sent_to_new_server(saved_key):
    main, vis = _llm_keys(_body("http://100.121.208.108:1234/v1"))       # anahtar alanı boş, adres değişti
    assert main == "" and vis == "" and "GIZLI" not in main + vis


def test_saved_key_kept_for_same_server(saved_key):
    main, _ = _llm_keys(_body("https://evren-llmapi.example.org/v1"))
    assert main == "GIZLI-EVREN"


def test_typed_key_always_used(saved_key):
    main, vis = _llm_keys(_body("http://100.121.208.108:1234/v1", key="yeni-anahtar"))
    assert main == "yeni-anahtar"


def test_models_endpoint_does_not_leak_key(saved_key, monkeypatch):
    from fastapi.testclient import TestClient
    seen = {}

    class FakeOpenAI:
        def __init__(self, base_url, api_key, **kw):
            seen.update(base_url=base_url, api_key=api_key)
            self.models = SimpleNamespace(list=lambda: SimpleNamespace(data=[SimpleNamespace(id="qwen")]))
    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    body = {"target": "main", "llm": {"base_url": "http://100.121.208.108:1234/v1", "model": "", "api_key": None,
                                      "vision": {"enabled": False, "same_as_main": True, "model": ""}}}
    r = TestClient(m.app).post("/api/settings/llm/models", json=body)
    assert r.status_code == 200, r.text
    assert seen["base_url"].startswith("http://100.121.208.108") and seen["api_key"] in ("", "EMPTY")


def test_lmstudio_context_error_is_recognized(settings, monkeypatch):
    import httpx
    import openai

    from app.llm.gateway import ContextOverflow, LLMGateway
    gw = LLMGateway(settings)
    msg = ("Trying to keep the first 9120 tokens when context the overflows. However, the model is loaded with context "
           "length of only 8192 tokens, which is not enough.")

    def create(**kw):
        req = httpx.Request("POST", "http://x/v1/chat/completions")
        raise openai.BadRequestError(msg, response=httpx.Response(400, request=req), body={"message": msg})
    monkeypatch.setattr(gw.client.chat.completions, "create", create)
    with pytest.raises(ContextOverflow):
        gw.chat([{"role": "user", "content": "x"}])
    assert gw.context_window() == 8192


def test_pasted_key_and_url_are_cleaned():
    b = LlmIn.model_validate({"base_url": " 100.121.208.108:1234/v1/ ", "model": "m", "api_key": " sk-lm-a:b\r\n\x16",
                              "vision": {"base_url": "", "api_key": "​k "}})
    assert b.base_url == "http://100.121.208.108:1234/v1" and b.api_key == "sk-lm-a:b"
    assert b.vision.base_url == "" and b.vision.api_key == "k"


def test_thinking_cutoff_is_retried_with_larger_limit(settings, monkeypatch):
    """Düşünen model çıktı sınırını <think> ile doldurup boş kesilirse bir kez iki kat sınırla yeniden istenir."""
    from types import SimpleNamespace as NS

    from app.llm.gateway import LLMGateway
    gw = LLMGateway(settings.model_copy(update={"llm_max_tokens": 4096, "llm_context_tokens": 262144}))
    seen = []

    def create(**kw):
        seen.append(kw.get("max_tokens"))
        text, reason = ("<think>uzun uzun düşünüyor", "length") if len(seen) == 1 else ("Tablolar: … devam edeyim mi?", "stop")
        return NS(choices=[NS(message=NS(content=text, tool_calls=None), finish_reason=reason)], usage=None)
    monkeypatch.setattr(gw.client.chat.completions, "create", create)
    turn = gw.chat([{"role": "user", "content": "x"}], max_tokens=4096)
    assert seen == [4096, 8192] and turn.content == "Tablolar: … devam edeyim mi?" and not turn.truncated


@pytest.mark.parametrize("setting,expected", [(0, None), (4096, 4096)])
def test_max_output_tokens_only_when_set(settings, setting, expected):
    """Sınır verilmediyse istekte max_tokens gönderilmez (modelin kendi sınırı); verildiyse o kullanılır."""
    from types import SimpleNamespace as NS

    from app.llm.gateway import LLMGateway
    gw = LLMGateway(settings.model_copy(update={"llm_max_tokens": setting}))
    assert gw.max_output_tokens() == expected
    sent = {}
    gw.client.chat.completions.create = lambda **kw: sent.update(kw) or NS(
        choices=[NS(message=NS(content="ok", tool_calls=None), finish_reason="stop")], usage=None)
    gw.chat([{"role": "user", "content": "x"}])
    assert sent.get("max_tokens") == expected


def test_history_budget_uses_full_window(settings, services):
    """Bağlam penceresi biliniyorsa geçmiş sabit LLM_CONTEXT_CHARS ile kırpılmaz; pencerenin tamamı kullanılır."""
    from app.harness.agent import Agent
    from app.harness.session import SessionStore
    from types import SimpleNamespace as NS
    llm = NS(context_window=lambda: 262144, max_output_tokens=lambda: None, s=settings)
    agent = Agent(llm, services, SessionStore(settings.sessions_dir))
    budget, limit = agent._budget("sistem", [])
    assert limit is None and budget > settings.llm_context_chars * 3      # ~490 bin karakter, 120 bine sabitlenmez


def test_ui_max_tokens_overrides_env(conn_file, settings):
    from app.data import connections as c
    c.save_connections({"llm": {"base_url": "http://x/v1", "model": "m", "max_tokens": 12000}})
    assert c.llm_settings(settings.model_copy(update={"llm_max_tokens": 4096})).llm_max_tokens == 12000
