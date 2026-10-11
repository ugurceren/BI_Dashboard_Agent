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
    monkeypatch.setattr(agent_mod, "_wait", lambda _ev, sec: waits.append(sec) or False)
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
    monkeypatch.setattr(agent_mod, "_wait", lambda _ev, _sec: False)
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


class SlowLLM(FakeLLM):
    """İstemci zaman aşımı: yeniden denenmemeli (model o sürede yanıtı üretemiyor)."""

    def chat(self, messages, tools=None):
        self.calls.append({"messages": messages})
        raise LLMUnavailable("LLM sunucusu 600 sn içinde yanıt vermedi (zaman aşımı).", timeout=True)


def test_agent_does_not_retry_client_timeout(settings, services, monkeypatch):
    waits = []
    monkeypatch.setattr(agent_mod, "_wait", lambda _ev, sec: waits.append(sec) or False)
    llm = SlowLLM([], settings)
    store = SessionStore(settings.sessions_dir)
    s = store.create("standart")
    events = list(Agent(llm, services, store).run_turn(s.id, "dashboard tasarla"))
    err = next(e for e in events if e.type == "error").data["message"]
    assert len(llm.calls) == 1 and waits == []
    assert "küçük adımlara" in err
    errs = _audit(settings, "llm_error")
    assert len(errs) == 1 and errs[0]["timeout"] is True and errs[0]["retry"] is False


# ------------------------------------------------------------------ Durdur
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.llm.gateway import LLMCancelled


class BlockingLLM(FakeLLM):
    """İptali destekleyen sahte model: cancel kurulana kadar bekler (uzun yanıt)."""

    supports_cancel = True

    def __init__(self, settings):
        super().__init__([AssistantTurn("sonraki yanıt")], settings)
        self.started = threading.Event()
        self.block = True

    def chat(self, messages, tools=None, cancel=None, **_):
        self.calls.append({"messages": messages})
        if self.block:
            self.block = False
            self.started.set()
            assert cancel is not None and cancel.wait(10), "iptal gelmedi"
            raise LLMCancelled("Yanıt kullanıcı tarafından durduruldu.")
        return self.script.pop(0)


def test_cancel_stops_turn_without_error_and_session_is_usable(settings, services):
    llm = BlockingLLM(settings)
    store = SessionStore(settings.sessions_dir)
    agent = Agent(llm, services, store)
    s = store.create("standart")
    assert agent.cancel(s.id) is False                       # çalışan tur yok
    events = []
    th = threading.Thread(target=lambda: events.extend(agent.run_turn(s.id, "büyük bir dashboard tasarla")))
    th.start()
    assert llm.started.wait(10)
    assert agent.cancel(s.id) is True
    th.join(10)
    assert not th.is_alive()
    assert not any(e.type == "error" for e in events) and events[-1].type == "done"
    s = store.get(s.id)
    assert not s.busy and s.transcript[-1].content == "Yanıt kullanıcı tarafından durduruldu."
    assert "durdurdu" in s.llm_messages[-1]["content"]
    assert _audit(settings, "turn_cancelled")
    later = list(agent.run_turn(s.id, "devam"))              # kilit bırakıldı, sonraki mesaj çalışır
    assert not any(e.type == "error" for e in later)
    assert store.get(s.id).transcript[-1].content == "sonraki yanıt"


class _Slow(BaseHTTPRequestHandler):
    """Yanıtı 10 sn geciktiren sahte LLM sunucusu (uzun üretim)."""

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        time.sleep(10)

    def log_message(self, *a):
        pass


def test_gateway_cancel_closes_connection_quickly(settings):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        settings.llm_base_url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
        gw = LLMGateway(settings)
        ev = threading.Event()
        threading.Timer(0.5, ev.set).start()
        t0 = time.monotonic()
        with pytest.raises(LLMCancelled):
            gw.chat([{"role": "user", "content": "x"}], cancel=ev)
        assert time.monotonic() - t0 < 3, "iptal hemen dönmeli (sunucu 10 sn bekletiyor)"
    finally:
        srv.shutdown()


def test_timeout_setting_from_ui_overrides_env(settings, monkeypatch):
    from app.data import connections as conns
    monkeypatch.setattr(conns, "load_connections", lambda: {"llm": {"base_url": "http://x/v1", "model": "m", "timeout_s": 600}})
    eff = conns.llm_settings(settings)
    assert eff.llm_timeout_s == 600 and conns.llm_effective(settings)["timeout_s"] == 600
    monkeypatch.setattr(conns, "load_connections", lambda: {"llm": {"base_url": "http://x/v1", "model": "m"}})
    assert conns.llm_settings(settings).llm_timeout_s == settings.llm_timeout_s     # boş: .env / varsayılan



def test_stream_closed_mid_turn_releases_lock_and_stops_llm(settings, services, monkeypatch):
    """Tarayıcı akışı kapatınca (sayfa yenilendi) kilit açılmalı, oturum "yanıtlıyor"da kalmamalı, LLM isteği kesilmeli."""
    monkeypatch.setattr(agent_mod, "HEARTBEAT_AFTER_S", 0.5)
    llm = BlockingLLM(settings)
    store = SessionStore(settings.sessions_dir)
    agent = Agent(llm, services, store)
    s = store.create("standart")
    gen = agent.run_turn(s.id, "uzun bir istek")
    got = []
    for ev in gen:                      # LLM beklenirken bekleme durumu gelene kadar ilerle
        got.append(ev)
        if llm.started.is_set():
            break
    gen.close()                         # Starlette istemci kopunca böyle kapatır: RuntimeError olmamalı
    assert not store.get(s.id).busy
    assert store.lock(s.id).acquire(blocking=False), "kilit açılmalı"
    store.lock(s.id).release()
    later = list(agent.run_turn(s.id, "devam"))
    assert not any(e.type == "error" for e in later)
