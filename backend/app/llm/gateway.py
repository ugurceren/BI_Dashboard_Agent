"""Modelden bağımsız LLM gateway (OpenAI uyumlu API: vLLM, Ollama, LM Studio, LiteLLM, TGI ...).

İki araç çağırma modu:
  * native — sunucunun `tools` desteği (vLLM: --enable-auto-tool-choice --tool-call-parser hermes)
  * prompt — araç şemaları sistem mesajına yazılır, model <tool_call>{...}</tool_call> üretir.
    Sunucu tools'u desteklemiyorsa `auto` modunda otomatik olarak buna düşülür.
Her iki modda da harness'in iç mesaj formatı OpenAI formatıdır.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import openai
from openai import OpenAI

from app.config import Settings

log = logging.getLogger(__name__)

_THINK = re.compile(r"<think>.*?</think>\s*", re.S)
_TOOL_CALL = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.S)
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


class LLMError(Exception):
    pass


class LLMUnavailable(LLMError):
    """LLM sunucusu geçici olarak yanıt veremedi (502 / 503 / 504 / 429 / 408, bağlantı hatası, zaman aşımı).
    İstek değiştirilmeden yeniden denenebilir; yeniden denemeyi agent yapar (kullanıcıya durum gösterir, kaydeder).
    retry_after: sunucunun Retry-After başlığı (sn); timeout: istemci zaman aşımı (yeniden deneme bir kezle sınırlı)."""

    def __init__(self, msg: str, status: int | None = None, retry_after: float | None = None, timeout: bool = False):
        super().__init__(msg)
        self.status, self.retry_after, self.timeout = status, retry_after, timeout


class ContextOverflow(LLMError):
    """İstek modelin bağlam penceresine sığmadı (girdi + istenen çıktı > pencere). context: modelin penceresi."""

    def __init__(self, msg: str, context: int | None = None, input_tokens: int | None = None):
        super().__init__(msg)
        self.context, self.input_tokens = context, input_tokens


# vLLM: "...the model's context length is only 16384 tokens..." / OpenAI: "maximum context length is 16384 tokens"
# llama.cpp / LM Studio: "request (8645 tokens) exceeds the available context size (8192 tokens)" + "n_ctx":8192
_CTX_RE = re.compile(r"(?:context length is only|context length of only|maximum context length is|max_model_len[^0-9]{0,20}"
                     r"|available context size \(|n_ctx\W{0,4})\s*(\d{3,7})", re.I)
_IN_RE = re.compile(r"passed (\d+) input tokens|resulted in (\d+) tokens|messages resulted in (\d+)|request \((\d+) tokens\)"
                    r"|n_prompt_tokens\W{0,4}(\d+)", re.I)
_OVERFLOW_HINTS = ("context length", "context size", "too long", "exceed_context")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    parse_error: str | None = None


SILENT_CUT_TOKENS = 64          # sınırsız istekte bundan kısa kesilen yanıt: pencere doldu
RETHINK_TOKENS = 32768          # sunucu sınırında boş kesilen düşünen modele ikinci deneme için açık çıktı sınırı
TRUNCATED_NOTE = ("\n\n_(Yanıt uzunluk sınırında kesildi: model düşünme aşamasında sınıra takıldı. İsteği daha kısa ya da "
                  "adım adım yazmayı deneyin.)_")


@dataclass
class AssistantTurn:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    truncated: bool = False          # çıktı sınırında kesildi (finish_reason = length)

    def to_message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                for c in self.tool_calls
            ]
        return msg


def parse_json_loose(text: str) -> Any:
    """Model çıktısındaki JSON'u toleranslı ayrıştırır (kod bloğu, baştaki/sondaki metin, sondaki virgül)."""
    t = text.strip()
    m = _FENCE.match(t)
    if m:
        t = m.group(1)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start = min([i for i in (t.find("{"), t.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("JSON bulunamadı")
    end = max(t.rfind("}"), t.rfind("]"))
    chunk = re.sub(r",\s*([}\]])", r"\1", t[start:end + 1])
    return json.loads(chunk)


def _parse_arguments(raw: Any) -> tuple[dict[str, Any], str | None]:
    if isinstance(raw, dict):
        return raw, None
    if raw is None or raw == "":
        return {}, None
    try:
        val = parse_json_loose(str(raw))
        return (val if isinstance(val, dict) else {"value": val}), None
    except (ValueError, json.JSONDecodeError) as e:
        return {}, f"Araç argümanları geçerli JSON değil: {e}"


def _extract_prompt_tool_calls(content: str) -> tuple[str, list[ToolCall]]:
    calls: list[ToolCall] = []
    for m in _TOOL_CALL.finditer(content):
        body = m.group(1).strip()
        if not body:
            continue
        try:
            obj = parse_json_loose(body)
        except (ValueError, json.JSONDecodeError) as e:
            calls.append(ToolCall(f"call_{uuid.uuid4().hex[:8]}", "_invalid", {}, f"<tool_call> JSON'u okunamadı: {e}"))
            continue
        name = obj.get("name") or obj.get("tool") or ""
        args, err = _parse_arguments(obj.get("arguments", obj.get("parameters", {})))
        calls.append(ToolCall(f"call_{uuid.uuid4().hex[:8]}", name, args, err))
    text = _TOOL_CALL.sub("", content).strip() if calls else content
    return text, calls


def _prompt_tools_block(tools: list[dict[str, Any]]) -> str:
    lines = [
        "# Araçlar",
        "Aşağıdaki araçları kullanabilirsin. Bir araç çağırmak için yanıtına TAM OLARAK şu biçimde bir blok yaz",
        "(birden fazla blok yazabilirsin; araç çağırırken başka açıklama yazma):",
        '<tool_call>{"name": "<araç_adı>", "arguments": {<JSON argümanlar>}}</tool_call>',
        "Araç sonucu sana <tool_response> içinde dönecek. Araç gerekmiyorsa normal metinle yanıt ver.",
        "",
        "<tools>",
    ]
    for t in tools:
        f = t["function"]
        lines.append(json.dumps({"name": f["name"], "description": f["description"], "parameters": f["parameters"]},
                                ensure_ascii=False))
    lines.append("</tools>")
    return "\n".join(lines)


def _to_prompt_mode(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m["role"]
        if role == "system" and tools:
            out.append({"role": "system", "content": f"{m['content']}\n\n{_prompt_tools_block(tools)}"})
        elif role == "assistant" and m.get("tool_calls"):
            blocks = [f'<tool_call>{json.dumps({"name": c["function"]["name"], "arguments": json.loads(c["function"]["arguments"] or "{}")}, ensure_ascii=False)}</tool_call>'
                      for c in m["tool_calls"]]
            out.append({"role": "assistant", "content": ((m.get("content") or "") + "\n" + "\n".join(blocks)).strip()})
        elif role == "tool":
            item = f"<tool_response>\n{m['content']}\n</tool_response>"
            if out and out[-1]["role"] == "user" and out[-1]["content"].startswith("<tool_response>"):
                out[-1]["content"] += "\n" + item
            else:
                out.append({"role": "user", "content": item})
        else:
            out.append({k: v for k, v in m.items() if k in ("role", "content")})
    return out


class LLMGateway:
    def __init__(self, settings: Settings):
        self.s = settings
        # max_retries=0: geçici hatalarda (502 / 503 / 504 …) yeniden denemeyi agent bekleyerek yapar (LLMUnavailable);
        # kütüphanenin 1 sn içindeki tek denemesi kısa kesintileri atlatmıyor, yalnız yükü artırıyordu
        self.client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key or "EMPTY",
                             timeout=settings.llm_timeout_s, max_retries=0)
        self.tool_mode = settings.llm_tool_mode  # auto → ilk hatada prompt'a düşebilir
        self._ctx: int | None = settings.llm_context_tokens or None   # bağlam penceresi (öğrenilince önbellek)
        self._ctx_probed = bool(self._ctx)
        vision_url = settings.vision_base_url or settings.llm_base_url
        self.vision_client = OpenAI(base_url=vision_url, api_key=settings.vision_api_key or settings.llm_api_key or "EMPTY",
                                    timeout=settings.llm_timeout_s, max_retries=1)

    # ------------------------------------------------------------------ sohbet
    # ------------------------------------------------------------------ bağlam penceresi
    def context_window(self) -> int | None:
        """Modelin bağlam penceresi (token): ayar (LLM_CONTEXT_TOKENS) → sunucunun /models yanıtındaki max_model_len
        (vLLM) → ilk 'context length' hatasından öğrenilen değer. Bilinmiyorsa None."""
        if self._ctx:
            return self._ctx
        if not self._ctx_probed:
            self._ctx_probed = True
            try:
                for m in self.client.with_options(timeout=5, max_retries=0).models.list().data:
                    if m.id == self.s.llm_model:
                        extra = getattr(m, "model_extra", None) or {}
                        v = extra.get("max_model_len") or extra.get("context_length") or extra.get("max_context_length")
                        if v:
                            self._ctx = int(v)
                            log.info("LLM bağlam penceresi: %d token (%s)", self._ctx, self.s.llm_model)
                        break
            except Exception as e:  # noqa: BLE001 — öğrenilemezse ilk hatadan öğrenilir
                log.info("Bağlam penceresi sunucudan alınamadı: %s", e)
            if not self._ctx:
                self._ctx = self._lmstudio_context()
        return self._ctx

    def _lmstudio_context(self) -> int | None:
        """LM Studio: /api/v0/models yüklü modelin bağlam uzunluğunu verir (loaded_context_length)."""
        import httpx
        root = re.sub(r"/v1/?$", "", self.s.llm_base_url.rstrip("/"))
        key = self.s.llm_api_key
        try:
            r = httpx.get(f"{root}/api/v0/models", timeout=5,
                          headers={"Authorization": f"Bearer {key}"} if key and key != "EMPTY" else {})
            for m in (r.json() or {}).get("data", []) if r.status_code == 200 else []:
                if m.get("id") == self.s.llm_model:
                    v = m.get("loaded_context_length") or m.get("max_context_length")
                    if v:
                        log.info("LLM bağlam penceresi (LM Studio): %s token", v)
                        return int(v)
        except Exception as e:  # noqa: BLE001
            log.info("LM Studio model bilgisi alınamadı: %s", e)
        return None

    def max_output_tokens(self) -> int | None:
        """Yanıt token sınırı: ayar (LLM_MAX_TOKENS / Bağlantı Ayarları) > 0 ise o. Değilse None: istekte sınır
        gönderilmez, modelin / sunucunun kendi sınırı geçerlidir (yeni modellerde yüksek)."""
        if self.s.llm_max_tokens and self.s.llm_max_tokens > 0:
            return int(self.s.llm_max_tokens)
        return None

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
             max_tokens: int | None = None, _retried: bool = False) -> AssistantTurn:
        turn = self._chat(messages, tools, max_tokens)
        # Düşünen modeller (Qwen3 vb.) çıktı sınırının tamamını <think> bölümüne harcayıp görünür yanıt yazamayabilir:
        # yanıt boş kesildiyse bir kez iki kat sınırla yeniden istenir (bağlam penceresinin dörtte birini aşmadan).
        if turn.truncated and not turn.content.replace(TRUNCATED_NOTE.strip(), "").strip() and not turn.tool_calls and not _retried:
            limit = max_tokens or self.max_output_tokens()
            p, c = turn.usage.get("prompt", 0), turn.usage.get("completion", 0)
            if not limit and p and c < SILENT_CUT_TOKENS:
                # sınır göndermedik ama yanıt birkaç token'da kesildi: sunucu bağlam penceresi dolunca hata vermeden
                # kesiyor (llama.cpp / bazı ağ geçitleri; ör. 8183 + 9 = 8192). Pencere öğrenilir, geçmiş kısaltılıp yeniden denenir.
                self._ctx = p + c
                log.warning("LLM yanıtı %d token'da sessizce kesildi (girdi %d): bağlam penceresi %d kabul ediliyor", c, p, p + c)
                raise ContextOverflow(f"Yanıt bağlam penceresi dolduğu için kesildi (girdi {p} + yanıt {c} token).", p + c, p)
            ctx = self.context_window()
            if not limit:
                # sınır göndermedik: kesen sunucunun varsayılan çıktı sınırı (ör. EVREN 16.384). Düşünen model payın tamamını
                # düşünmeye harcadı: bir kez daha yüksek açık sınırla ve "kısa düşün" yönlendirmesiyle denenir.
                bigger = max(c * 2, RETHINK_TOKENS)
                if ctx:
                    bigger = min(bigger, max(ctx - p - 1024, 0))
                if bigger <= c:
                    return turn
                log.info("LLM yanıtı düşünme bölümünde kesildi (%d token, sunucu sınırı); %d ile yeniden deneniyor", c, bigger)
                nudge = {"role": "user", "content": "[HARNESS] Önceki denemede yanıtın düşünme aşamasında uzunluk sınırına takıldı ve "
                                                    "görünür yanıt yazılamadı. Kısa düşün: doğrudan yanıt ver ya da gereken aracı çağır."}
                try:
                    return self.chat([*messages, nudge], tools, bigger, _retried=True)
                except LLMError as e:   # sunucu bu sınırı kabul etmezse (ör. azami çıktı aşıldı) ilk sonuç gösterilir
                    log.warning("Yüksek sınırla yeniden deneme başarısız: %s", e)
                    return turn
            bigger = min(limit * 2, max(limit, (ctx // 4) if ctx else limit * 2))
            if bigger > limit:
                log.info("LLM yanıtı düşünme bölümünde kesildi; %d → %d token ile yeniden deneniyor", limit, bigger)
                return self.chat(messages, tools, bigger, _retried=True)
        return turn

    def _chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
              max_tokens: int | None = None) -> AssistantTurn:
        use_native = bool(tools) and self.tool_mode in ("auto", "native")
        try:
            if use_native:
                resp = self._create(messages, tools, max_tokens)
            else:
                resp = self._create(_to_prompt_mode(messages, tools), None, max_tokens)
        except openai.BadRequestError as e:
            if use_native and self.tool_mode == "auto" and _looks_like_tool_unsupported(e):
                log.warning("Sunucu native tool calling desteklemiyor, prompt moduna geçiliyor: %s", e)
                self.tool_mode = "prompt"
                return self._chat(messages, tools, max_tokens)
            text = _err_text(e)
            ctx = _CTX_RE.search(text)
            if ctx or any(h in text.lower() for h in _OVERFLOW_HINTS):
                if ctx:
                    self._ctx = int(ctx.group(1))   # öğrenildi: sonraki istekler buna göre kırpılır
                m = _IN_RE.search(text)
                n_in = int(next(g for g in m.groups() if g)) if m else None
                raise ContextOverflow(f"İstek modelin bağlam penceresine sığmadı: {text}", self._ctx, n_in) from e
            raise LLMError(f"LLM isteği reddedildi: {text}") from e
        except openai.APITimeoutError as e:     # APIConnectionError'ın alt sınıfı: önce yakalanmalı
            raise LLMUnavailable(f"LLM sunucusu {self.s.llm_timeout_s:g} sn içinde yanıt vermedi (zaman aşımı).",
                                 timeout=True) from e
        except openai.APIConnectionError as e:
            raise LLMUnavailable(f"LLM sunucusuna bağlanılamadı ({self.s.llm_base_url}). Sunucu açık mı?") from e
        except openai.APIStatusError as e:
            code = e.status_code
            msg = _STATUS_TR.get(code) or f"LLM hatası ({code}): {_err_text(e)}"
            if code in TRANSIENT_STATUS:
                raise LLMUnavailable(msg, status=code, retry_after=_retry_after(e)) from e
            raise LLMError(msg) from e

        choice = resp.choices[0]
        msg = choice.message
        content = msg.content or ""
        if self.s.llm_strip_thinking:
            content = _THINK.sub("", content)
            if "<think>" in content and "</think>" not in content:  # kesilmiş düşünce
                content = content.split("<think>")[0]
        calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            args, err = _parse_arguments(tc.function.arguments)
            calls.append(ToolCall(tc.id or f"call_{uuid.uuid4().hex[:8]}", tc.function.name, args, err))
        if not calls and "<tool_call>" in content:
            content, calls = _extract_prompt_tool_calls(content)
        usage = {}
        if resp.usage:
            usage = {"prompt": resp.usage.prompt_tokens or 0, "completion": resp.usage.completion_tokens or 0}
        cut = choice.finish_reason == "length"
        if cut and not calls:
            content += TRUNCATED_NOTE
        return AssistantTurn(content.strip(), calls, usage, truncated=cut)

    def _create(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, max_tokens: int | None = None):
        kwargs: dict[str, Any] = dict(model=self.s.llm_model, messages=messages, temperature=self.s.llm_temperature)
        limit = max_tokens or self.max_output_tokens()
        if limit:                       # sınır yoksa gönderilmez: modelin kendi (yüksek) sınırı geçerli
            kwargs["max_tokens"] = limit
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if self.s.llm_extra_body:
            kwargs["extra_body"] = self.s.llm_extra_body
        return self.client.chat.completions.create(**kwargs)

    # ------------------------------------------------------------------ görsel
    @property
    def vision_enabled(self) -> bool:
        return bool(self.s.vision_model)

    def vision(self, prompt: str, image_data_urls: list[str]) -> str:
        if not self.s.vision_model:
            raise LLMError("VISION_MODEL tanımlı değil.")
        parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        parts += [{"type": "image_url", "image_url": {"url": u}} for u in image_data_urls]
        try:
            resp = self.vision_client.chat.completions.create(
                model=self.s.vision_model, messages=[{"role": "user", "content": parts}],
                temperature=0.1, max_tokens=1500)
        except openai.APIConnectionError as e:
            raise LLMError("Görsel model sunucusuna bağlanılamadı.") from e
        except openai.APIError as e:
            raise LLMError(f"Görsel model hatası: {_err_text(e)}") from e
        return _THINK.sub("", resp.choices[0].message.content or "").strip()

    # ------------------------------------------------------------------ sağlık
    def health(self) -> dict[str, Any]:
        info: dict[str, Any] = {"reachable": False, "model": self.s.llm_model, "base_url": self.s.llm_base_url,
                                "tool_mode": self.tool_mode}
        try:
            models = [m.id for m in self.client.with_options(timeout=5, max_retries=0).models.list().data]
            info["reachable"] = True
            info["available_models"] = models[:20]
            if models and self.s.llm_model not in models:
                info["error"] = f"'{self.s.llm_model}' sunucuda yok. Mevcut: {', '.join(models[:5])}"
            ctx = self.context_window()
            if ctx:
                info["context_tokens"] = ctx   # konuşma geçmişi buna göre kırpılır
        except Exception as e:  # noqa: BLE001
            info["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        return info


TRANSIENT_STATUS = {408, 429, 502, 503, 504}
_STATUS_TR = {
    502: "LLM sunucusu yanıt veremedi (502 Bad Gateway): ağ geçidinin arkasındaki model sunucusu bağlantıyı kesti ya da yeniden başlıyor.",
    503: "LLM sunucusu şu an kullanılamıyor (503 Service Unavailable): model sunucusu yeniden başlıyor, kapasitesi dolu ya da istek sınırı aşıldı.",
    504: "LLM sunucusu zamanında yanıt vermedi (504 Gateway Timeout): yanıt, ağ geçidinin bekleme süresini aştı.",
    429: "LLM sunucusu istek sınırına takıldı (429 Too Many Requests).",
    408: "LLM sunucusu isteği zaman aşımıyla kapattı (408 Request Timeout).",
}
_HTML_TITLE = re.compile(r"<title>\s*(.*?)\s*</title>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")


def _err_text(e: openai.APIError) -> str:
    """Hata gövdesinden okunur mesaj: JSON ise error.message; HTML ise (nginx hata sayfası) başlığı ya da etiketsiz metin."""
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        m = err.get("message") if isinstance(err, dict) else (body.get("message") or err)
        if m:
            return _clean(str(m))[:300]
    return _clean(str(e))[:300]


def _clean(text: str) -> str:
    if "<" in text and ">" in text and re.search(r"<(html|head|body|center|h1|title)\b", text, re.I):
        m = _HTML_TITLE.search(text)
        text = m.group(1) if m else _TAGS.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _retry_after(e: openai.APIStatusError) -> float | None:
    try:
        v = e.response.headers.get("retry-after")
        return max(0.0, float(v)) if v else None
    except (AttributeError, TypeError, ValueError):   # HTTP tarihi biçimi vb.: yok sayılır
        return None


def _looks_like_tool_unsupported(e: openai.BadRequestError) -> bool:
    t = _err_text(e).lower()
    return any(k in t for k in ("tool", "function", "auto-tool-choice", "tool_choice"))
