"""Modelin bağlam penceresi: konuşma geçmişi pencereye göre kırpılır; sığmazsa hata mesajından pencere öğrenilip
geçmiş kısaltılarak yeniden denenir (ör. kurum modeli 16384 token: 'You passed 12289 input tokens …')."""

import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.harness.agent import CHARS_PER_TOKEN, Agent
from app.harness.session import SessionStore
from app.llm.gateway import AssistantTurn, ContextOverflow, LLMGateway
from tests.conftest import FakeLLM

VLLM_MSG = ("You passed 12289 input tokens and requested 4096 output tokens. However, the model's context length is only "
            "16384 tokens, resulting in a maximum input length of 12288 tokens. Please reduce the length of the input prompt. "
            "(parameter=input_tokens, value=12289)")


def _bad_request(msg):
    req = httpx.Request("POST", "http://llm/v1/chat/completions")
    return openai.BadRequestError(msg, response=httpx.Response(400, request=req), body={"message": msg})


def test_gateway_learns_context_from_vllm_error(settings, monkeypatch):
    gw = LLMGateway(settings)

    def create(**kw):
        raise _bad_request(VLLM_MSG)
    monkeypatch.setattr(gw.client.chat.completions, "create", create)
    with pytest.raises(ContextOverflow) as ei:
        gw.chat([{"role": "user", "content": "x"}])
    assert ei.value.context == 16384 and ei.value.input_tokens == 12289
    assert gw.context_window() == 16384                                   # sonraki istekler buna göre kırpılır


def test_gateway_reads_max_model_len_from_models(settings, monkeypatch):
    gw = LLMGateway(settings)
    models = SimpleNamespace(data=[SimpleNamespace(id="baska", model_extra={"max_model_len": 999}),
                                   SimpleNamespace(id=settings.llm_model, model_extra={"max_model_len": 16384})])
    fake = SimpleNamespace(models=SimpleNamespace(list=lambda: models))
    monkeypatch.setattr(gw.client, "with_options", lambda **kw: fake)
    assert gw.context_window() == 16384


def test_setting_overrides_context(settings):
    s = settings.model_copy(update={"llm_context_tokens": 8192})
    assert LLMGateway(s).context_window() == 8192


class SmallCtxLLM(FakeLLM):
    """16384 token pencereli model: ilk çağrıda sığmadı hatası (isteğe bağlı), sonra yanıt."""

    def __init__(self, script, settings, overflow_first=False):
        super().__init__(script, settings)
        self.overflow_first = overflow_first
        self.sizes = []

    def context_window(self):
        return 16384

    def chat(self, messages, tools=None, max_tokens=None):
        self.sizes.append(sum(len(json.dumps(m, ensure_ascii=False)) for m in messages))
        self.max_tokens = max_tokens
        if self.overflow_first:
            self.overflow_first = False
            raise ContextOverflow(VLLM_MSG, 16384, 12289)
        return super().chat(messages, tools)


def _long_session(store):
    s = store.create("standart")
    for i in range(80):   # uzun geçmiş: ~160K karakter (sabit bütçe 120K'yı ve 16K token pencereyi aşar)
        s.llm_messages.append({"role": "user", "content": f"mesaj {i} " + "x" * 1000})
        s.llm_messages.append({"role": "assistant", "content": f"yanıt {i} " + "y" * 1000})
    store.save(s)
    return s


def test_history_is_trimmed_to_model_window(settings, services):
    llm = SmallCtxLLM([AssistantTurn("Tamam.")], settings)
    store = SessionStore(settings.sessions_dir)
    s = _long_session(store)
    list(Agent(llm, services, store).run_turn(s.id, "son tarihte top 100 mevduatı olan müşteriler", None))
    assert llm.max_tokens is None                                         # sınır gönderilmez: modelin kendi sınırı
    est_tokens = llm.sizes[0] / CHARS_PER_TOKEN
    assert est_tokens <= 16384 - 4096                                     # yanıt payı (pencerenin 1/4'ü) bırakılarak pencereye sığar


def test_overflow_retries_with_shorter_history(settings, services):
    llm = SmallCtxLLM([AssistantTurn("Tamam.")], settings, overflow_first=True)
    store = SessionStore(settings.sessions_dir)
    s = _long_session(store)
    events = list(Agent(llm, services, store).run_turn(s.id, "rapor istiyorum", None))
    assert len(llm.sizes) == 2 and llm.sizes[1] < llm.sizes[0]            # ikinci deneme daha kısa
    assert not [e for e in events if e.type == "error"]
    assert any(e.type == "status" and "kısaltılıp" in e.data.get("text", "") for e in events)


LMSTUDIO_MSG = ("Engine protocol predict request returned 400: {\"error\":{\"code\":400,\"message\":\"request (8645 tokens) "
                "exceeds the available context size (8192 tokens), try increasing it\",\"type\":\"exceed_context_size_error\","
                "\"n_prompt_tokens\":8645,\"n_ctx\":8192}}")


def test_gateway_learns_context_from_lmstudio_error(settings, monkeypatch):
    """LM Studio / llama.cpp: /v1/models modelin azami bağlamını (262144) verir, yüklü bağlam (8192) yalnız hatada görünür."""
    gw = LLMGateway(settings)

    def create(**kw):
        raise _bad_request(LMSTUDIO_MSG)
    monkeypatch.setattr(gw.client.chat.completions, "create", create)
    with pytest.raises(ContextOverflow) as ei:
        gw.chat([{"role": "user", "content": "x"}])
    assert ei.value.context == 8192 and ei.value.input_tokens == 8645
    assert gw.context_window() == 8192


def test_gateway_detects_silent_cut_as_overflow(settings, monkeypatch):
    """Bazı sunucular pencere dolunca hata vermez, yanıtı birkaç token'da keser (8183 + 9 = 8192):
    bu bağlam taşması sayılır (agent geçmişi kısaltıp yeniden dener), kullanıcıya boş "kesildi" yanıtı gitmez."""
    gw = LLMGateway(settings)
    resp = SimpleNamespace(choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(content="", tool_calls=None))],
                           usage=SimpleNamespace(prompt_tokens=8183, completion_tokens=9))
    monkeypatch.setattr(gw.client.chat.completions, "create", lambda **kw: resp)
    with pytest.raises(ContextOverflow) as ei:
        gw.chat([{"role": "user", "content": "x"}])
    assert ei.value.context == 8192 and gw.context_window() == 8192


def _resp(content, finish, prompt, completion):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content, tool_calls=None))],
                           usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion))


def test_thinking_cut_at_server_default_is_retried_with_higher_limit(settings, monkeypatch):
    """Sınır gönderilmedi, sunucu varsayılanı (EVREN 16.384) düşünmede doldu, görünür yanıt boş: bir kez açık daha yüksek
    sınır ve 'kısa düşün' yönlendirmesiyle yeniden denenir."""
    gw = LLMGateway(settings)
    calls = []

    def create(**kw):
        calls.append(kw)
        return _resp("", "length", 9000, 16384) if len(calls) == 1 else _resp("Hangi KPI'lar olsun?", "stop", 9100, 800)
    monkeypatch.setattr(gw.client.chat.completions, "create", create)
    monkeypatch.setattr(gw, "context_window", lambda: 262144)
    t = gw.chat([{"role": "user", "content": "x"}])
    assert t.content == "Hangi KPI'lar olsun?" and not t.truncated
    assert "max_tokens" not in calls[0] and calls[1]["max_tokens"] == 32768
    assert "Kısa düşün" in calls[1]["messages"][-1]["content"]


def test_thinking_retry_rejected_by_server_returns_first_result(settings, monkeypatch):
    gw = LLMGateway(settings)
    n = {"i": 0}

    def create(**kw):
        n["i"] += 1
        if n["i"] == 1:
            return _resp("", "length", 9000, 16384)
        raise _bad_request("max_tokens is too large")
    monkeypatch.setattr(gw.client.chat.completions, "create", create)
    monkeypatch.setattr(gw, "context_window", lambda: 262144)
    t = gw.chat([{"role": "user", "content": "x"}])
    assert t.truncated and "kesildi" in t.content and n["i"] == 2
