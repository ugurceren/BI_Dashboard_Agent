"""Agent döngüsü: model → araç → sonuç → model … (Claude Code'daki harness mantığı).

Bir kullanıcı mesajı için:
  1. (görsel varsa) tasarım özeti çıkar
  2. faz prompt'u + fazın araçlarıyla modeli çağır
  3. araç çağrılarını doğrula/çalıştır, sonuçları modele geri ver
  4. araç faz değiştirirse bağlamı sıfırla, yapılandırılmış durumu aktar, yeni fazda devam et
  5. model metinle yanıt verince ya da adım sınırında dur
Tüm adımlar UI'a olay (event) olarak akar ve denetim kaydına (audit) yazılır.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from app.harness.phases import system_prompt
from app.harness.session import Session, SessionStore, ToolInfo, TranscriptItem, now_iso
from app.harness.tools import TOOLS_BY_NAME, Services, ToolContext, ToolResult, to_llm_content, tools_for
from app.harness.vision import analyze_design_images
from app.llm.gateway import AssistantTurn, LLMError, LLMGateway, ToolCall

log = logging.getLogger(__name__)

KEEP_MESSAGES = 40        # modele gönderilen son mesaj sayısı (faz içinde)
FULL_TOOL_RESULTS = 8     # son N araç sonucu tam, daha eskiler kısaltılır
OLD_TOOL_RESULT_CHARS = 600


@dataclass
class Event:
    type: str   # transcript | status | state | error | done
    data: Any


class Audit:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, **record: Any) -> None:
        record["ts"] = now_iso()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _trim(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    msgs = messages[-KEEP_MESSAGES:]
    # baştaki yetim araç sonuçlarını / sonuçları kesilmiş araç çağrılarını at
    while msgs and (msgs[0]["role"] == "tool" or (msgs[0]["role"] == "assistant" and msgs[0].get("tool_calls"))):
        msgs = msgs[1:]
    tool_idx = [i for i, m in enumerate(msgs) if m["role"] == "tool"]
    old = set(tool_idx[:-FULL_TOOL_RESULTS]) if len(tool_idx) > FULL_TOOL_RESULTS else set()
    out = []
    for i, m in enumerate(msgs):
        if i in old and len(m["content"]) > OLD_TOOL_RESULT_CHARS:
            m = {**m, "content": m["content"][:OLD_TOOL_RESULT_CHARS] + "…(eski sonuç kısaltıldı)"}
        out.append(m)
    return out


class Agent:
    def __init__(self, gateway: LLMGateway, services: Services, store: SessionStore):
        self.llm = gateway
        self.services = services
        self.store = store
        self.audit = Audit(services.settings.audit_log)

    # ------------------------------------------------------------------ public
    def run_turn(self, sid: str, text: str, images: list[str] | None = None) -> Iterator[Event]:
        lock = self.store.lock(sid)
        if not lock.acquire(blocking=False):
            yield Event("error", {"message": "Bu oturumda zaten çalışan bir istek var."})
            yield Event("done", {})
            return
        s: Session | None = None
        try:
            s = self.store.get(sid)
            s.busy = True
            self.store.save(s)
            yield from self._turn(s, text.strip(), images or [])
        except LLMError as e:
            log.warning("LLM hatası: %s", e)
            if s:
                yield self._emit(s, TranscriptItem(role="system", content=f"⚠️ {e}"))
            yield Event("error", {"message": str(e)})
        except Exception as e:  # noqa: BLE001
            log.exception("Agent hatası")
            yield Event("error", {"message": f"Beklenmeyen hata: {type(e).__name__}: {e}"})
        finally:
            if s:
                s.busy = False
                self.store.save(s)
                yield Event("state", s.public())
            lock.release()
            yield Event("done", {})

    # ------------------------------------------------------------------ iç
    def _emit(self, s: Session, item: TranscriptItem) -> Event:
        s.add(item)
        return Event("transcript", item.model_dump())

    def _turn(self, s: Session, text: str, images: list[str]) -> Iterator[Event]:
        yield self._emit(s, TranscriptItem(role="user", content=text, images=images or None))
        llm_text = text

        if images:
            yield Event("status", {"text": "Örnek görsel analiz ediliyor…"})
            t0 = time.perf_counter()
            brief, notes = analyze_design_images(self.llm, images, text)
            s.design_brief = brief
            yield self._emit(s, TranscriptItem(role="tool", content="Tasarım görseli analiz edildi", tool=ToolInfo(
                name="analyze_design_image", arguments={"images": len(images)}, ok=True, summary=" · ".join(notes),
                durationMs=int((time.perf_counter() - t0) * 1000))))
            llm_text += ("\n\n[Kullanıcı örnek bir dashboard görseli yükledi. Sistemin çıkardığı tasarım özeti: "
                         + json.dumps(brief.model_dump(exclude_none=True), ensure_ascii=False) + "]")
            if s.phase != "design":
                llm_text += "\n[Not: tasarım özeti kaydedildi, tasarım fazında kullanılacak.]"
            yield Event("state", s.public())

        s.llm_messages.append({"role": "user", "content": llm_text or "(boş mesaj)"})
        self.store.save(s)

        empty_retries = 0
        for step in range(self.services.settings.max_agent_steps):
            yield Event("status", {"text": "Düşünüyor…" if step == 0 else "Devam ediyor…"})
            tools = tools_for(s.phase)
            messages = [{"role": "system", "content": system_prompt(s, self.services.connector.dialect)}] + _trim(s.llm_messages)
            t0 = time.perf_counter()
            turn: AssistantTurn = self.llm.chat(messages, [t.schema() for t in tools])
            self.audit.write(session=s.id, event="llm", phase=s.phase, ms=int((time.perf_counter() - t0) * 1000),
                             tool_calls=[c.name for c in turn.tool_calls], usage=turn.usage, tool_mode=self.llm.tool_mode)

            if not turn.tool_calls:
                if not turn.content and empty_retries == 0:
                    empty_retries += 1
                    s.llm_messages.append({"role": "user", "content": "[HARNESS] Boş yanıt verdin. Kullanıcıya yanıt yaz ya da bir araç çağır."})
                    continue
                s.llm_messages.append(turn.to_message())
                yield self._emit(s, TranscriptItem(role="assistant", content=turn.content or "(yanıt yok)"))
                self.store.save(s)
                return

            s.llm_messages.append(turn.to_message())
            if turn.content:
                yield self._emit(s, TranscriptItem(role="assistant", content=turn.content))

            next_phase, kickoff = None, None
            for call in turn.tool_calls:
                result = yield from self._run_tool(s, call)
                if result.ok and result.next_phase:
                    next_phase, kickoff = result.next_phase, result.kickoff
            self.store.save(s)

            if next_phase:
                s.set_phase(next_phase)
                yield self._emit(s, TranscriptItem(role="system", content={
                    "data": "Veri keşfi fazına geçildi", "design": "Tasarım fazına geçildi"}.get(next_phase, next_phase)))
                s.llm_messages.append({"role": "user", "content": f"[HARNESS] {kickoff}"})
                yield Event("state", s.public())
                self.store.save(s)

        yield self._emit(s, TranscriptItem(role="assistant", content=(
            "Bu istek için adım sınırına ulaştım. Şu ana kadarki ilerleme kaydedildi; "
            "\"devam et\" yazarak kaldığım yerden sürdürebilirsiniz.")))
        s.llm_messages.append({"role": "assistant", "content": "(adım sınırına ulaşıldı)"})

    def _run_tool(self, s: Session, call: ToolCall) -> Iterator[Event]:
        tool = TOOLS_BY_NAME.get(call.name)
        t0 = time.perf_counter()
        if call.parse_error:
            result = ToolResult(False, {"error": call.parse_error}, "Argümanlar okunamadı")
        elif tool is None:
            result = ToolResult(False, {"error": f"'{call.name}' diye bir araç yok. Kullanılabilir: {[t.name for t in tools_for(s.phase)]}"},
                                "Bilinmeyen araç")
        elif s.phase not in tool.phases:
            result = ToolResult(False, {"error": f"'{call.name}' bu fazda ({s.phase}) kullanılamaz. Kullanılabilir: {[t.name for t in tools_for(s.phase)]}"},
                                "Araç bu fazda kapalı")
        else:
            if tool.status:
                yield Event("status", {"text": tool.status})
            try:
                result = tool.handler(ToolContext(s, self.services), call.arguments)
            except Exception as e:  # noqa: BLE001
                log.exception("Araç hatası: %s", call.name)
                result = ToolResult(False, {"error": f"Araç çalışırken hata: {type(e).__name__}: {e}"}, "Araç hatası")
        ms = int((time.perf_counter() - t0) * 1000)

        s.llm_messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                               "content": to_llm_content(result.content, self.services.settings.tool_result_char_limit)})
        self.audit.write(session=s.id, event="tool", phase=s.phase, tool=call.name, ok=result.ok, ms=ms,
                         arguments=call.arguments, summary=result.summary,
                         errors=result.content.get("errors") if isinstance(result.content, dict) else None)
        yield self._emit(s, TranscriptItem(role="tool", content=result.summary, tool=ToolInfo(
            name=call.name, arguments=call.arguments, ok=result.ok, summary=result.summary, durationMs=ms)))
        if result.state_changed:
            yield Event("state", s.public())
        return result
