"""LLM sunucusu geçici hataları (502 / 503 / 504): okunur mesaj, bekleyerek yeniden deneme, denetim kaydı."""

import json

import httpx
import openai
import pytest

import app.harness.agent as agent_mod
from app.harness.agent import Agent
from app.harness.session import SessionStore
from app.llm.gateway import AssistantTurn, LLMError, LLMGateway, LLMUnavailable
from tests.conftest import FakeLLM

NGINX_503 = ("<html> <head><title>503 Service Temporarily Unavailable</title></head> <body> <center>"
             "<h1>503 Service Temporarily Unavailable</h1></center> <hr><center>nginx</center> </body> </html>")


def _status_error(code: int, body: str, headers: dict | None = None) -> openai.APIStatusError:
    req = httpx.Request("POST", "http://llm/v1/chat/completions")
    resp = httpx.Response(code, request=req, text=body, headers=headers or {})
    return openai.APIStatusError(f"Error code: {code} - {body}", response=resp, body=None)


def _gateway(settings, exc):
    gw = LLMGateway(settings)

    def boom(**_):
        raise exc
    gw.client.chat.completions.create = boom   # type: ignore[method-assign]
    return gw


@pytest.mark.parametrize("code", [502, 503, 504])
def test_gateway_transient_status_is_retryable_and_readable(settings, code):
    with pytest.raises(LLMUnavailable) as ei:
        _gateway(settings, _status_error(code, NGINX_503, {"retry-after": "7"})).chat([{"role": "user", "content": "x"}])
    e = ei.value
    assert e.status == code and e.retry_after == 7 and not e.timeout
    assert "<html>" not in str(e) and f"({code} " in str(e)


def test_gateway_other_status_is_not_retried_and_html_stripped(settings):
    with pytest.raises(LLMError) as ei:
        _gateway(settings, _status_error(500, NGINX_503.replace("503", "500"))).chat([{"role": "user", "content": "x"}])
    assert not isinstance(ei.value, LLMUnavailable)
    assert "<" not in str(ei.value) and "500 Service Temporarily Unavailable" in str(ei.value)


def test_gateway_timeout_is_retryable_once(settings):
    req = httpx.Request("POST", "http://llm/v1/chat/completions")
    with pytest.raises(LLMUnavailable) as ei:
        _gateway(settings, openai.APITimeoutError(request=req)).chat([{"role": "user", "content": "x"}])
    assert ei.value.timeout and "zaman aşımı" in str(ei.value)


class FlakyLLM(FakeLLM):
    """İlk `fails` çağrıda 503 verir, sonra senaryoya döner."""

    def __init__(self, fails: int, script, settings):
        super().__init__(script, settings)
        self.fails = fails

    def chat(self, messages, tools=None):
        if self.fails > 0:
            self.fails -= 1
            self.calls.append({"messages": messages, "failed": True})
            raise LLMUnavailable("LLM sunucusu şu an kullanılamıyor (503 Service Unavailable).", status=503)
        return super().chat(messages, tools)


def _audit(settings, event):
    return [json.loads(x) for x in settings.audit_log.read_text(encoding="utf-8").splitlines()
            if json.loads(x).get("event") == event]


def test_agent_waits_and_retries_then_succeeds(settings, services, monkeypatch):
    waits = []
    monkeypatch.setattr(agent_mod.time, "sleep", waits.append)
    llm = FlakyLLM(2, [AssistantTurn("Merhaba, hangi raporu istiyorsunuz?")], settings)
    store = SessionStore(settings.sessions_dir)
    s = store.create("standart")
    events = list(Agent(llm, services, store).run_turn(s.id, "merhaba"))
    statuses = [e.data["text"] for e in events if e.type == "status"]
    assert any("yeniden deneniyor (1/4)" in t for t in statuses) and any("(2/4)" in t for t in statuses)
    assert waits == list(agent_mod.LLM_RETRY_DELAYS[:2])
    assert not any(e.type == "error" for e in events)
    assert store.get(s.id).transcript[-1].content.startswith("Merhaba")
    errs = _audit(settings, "llm_error")
    assert [r["attempt"] for r in errs] == [1, 2] and all(r["status"] == 503 and r["retry"] for r in errs)
    assert all(r["prompt_tokens_est"] > 0 for r in errs)


def test_agent_gives_up_with_clear_message(settings, services, monkeypatch):
    monkeypatch.setattr(agent_mod.time, "sleep", lambda _s: None)
    llm = FlakyLLM(99, [], settings)
    store = SessionStore(settings.sessions_dir)
    s = store.create("standart")
    events = list(Agent(llm, services, store).run_turn(s.id, "merhaba"))
    err = next(e for e in events if e.type == "error").data["message"]
    assert "5 deneme yapıldı" in err and "<html>" not in err
    assert len(llm.calls) == 1 + len(agent_mod.LLM_RETRY_DELAYS)
    errs = _audit(settings, "llm_error")
    assert len(errs) == 5 and errs[-1]["retry"] is False
    assert len(_audit(settings, "llm_failed")) == 1
    assert not store.get(s.id).busy
