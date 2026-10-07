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
