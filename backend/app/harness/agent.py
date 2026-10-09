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
from app.harness.rules import phase_rules
from app.harness.session import Session, SessionStore, ToolInfo, TranscriptItem, now_iso
from app.harness.tools import TOOLS_BY_NAME, Services, ToolContext, ToolResult, to_llm_content, tools_for
from app.harness.vision import analyze_design_images
from app.llm.gateway import AssistantTurn, ContextOverflow, LLMError, LLMGateway, ToolCall

log = logging.getLogger(__name__)

FULL_TOOL_RESULTS = 12    # son N araç sonucu tam, daha eskiler kısaltılır
CHARS_PER_TOKEN = 2.5     # token tahmini (Türkçe metin + JSON için ihtiyatlı: gerçek ~3–3.5)
CONTEXT_MARGIN = 512      # token: tahmin hatası payı
OLD_TOOL_RESULT_CHARS = 700
CACHEABLE_TOOLS = {"search_dictionary", "get_table_details", "discover_object", "find_metrics", "run_sql"}
MAX_REPEATS = 3           # üst üste bu kadar tekrarlanan çağrıda tur durdurulur
MAX_FAIL_STREAK = 6       # aynı araç üst üste bu kadar başarısız olursa tur durdurulur
ASK_REFUSALS_BEFORE_STOP = 2   # öneri hazırken "önce kullanıcıya sor" bu kadar yok sayılırsa tur sistemce bitirilir


def proposal_question(prop: dict[str, Any] | None) -> str:
    """Model önerisinin kullanıcıya özeti + onay sorusu (model kendisi sormadığında sistem yazar)."""
    if not prop:
        return "Veri modeli önerisi hazır. Bu tablolarla devam edeyim mi?"
    kind = {"fact": "olgu", "dimension": "boyut", "view": "view", "bridge": "köprü"}
    lines = ["Raporda kullanmayı önerdiğim tablolar:"]
    for t in prop.get("tables", []):
        rows = (", " + f"{t['row_count']:,}".replace(",", ".") + " satır") if t.get("row_count") else ""
        desc = f" — {t['description']}" if t.get("description") else ""
        lines.append(f"- **{t['table']}** ({kind.get(t.get('kind'), t.get('kind') or '?')}{rows}){desc}")
    new = [c for c in prop.get("candidates", []) if not c.get("already_in_model") and c.get("cardinality") in ("N:1", "1:1", "N:1?")]
    if new:
        lines.append("")
        lines.append("Modelde olmayan ilişki önerileri:")
        lines += [f"- {c['from_table']} → {c['to_table']} ({', '.join(f'{a}={b}' for a, b in c['columns'])}, {c['cardinality']}) — {c['evidence']}"
                  for c in new[:6]]
        lines.append("")
        lines.append("Bu tablolarla ve ilişkilerle devam edeyim mi? İlişkiler ortak modele mi, yalnız bu rapora mı kaydedilsin? "
                     "Eksik ya da fazla tablo varsa belirtin.")
    else:
        lines.append("")
        lines.append("Tablolar arasındaki ilişkiler modelde mevcut. Bu tablolarla devam edeyim mi? Eksik ya da fazla tablo varsa belirtin.")
    return "\n".join(lines)


def _safe_rules(services, s) -> str:
    """Veritabanına özgü kurallar; dosya / sözlük sorunu agent'ı durdurmasın."""
    try:
        return phase_rules(services, s)
    except Exception as e:  # noqa: BLE001
        log.warning("Kurum veri kuralları yüklenemedi: %s", e)
        return ""


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


def _size(m: dict[str, Any]) -> int:
    return len(m.get("content") or "") + sum(len(c["function"]["arguments"]) for c in m.get("tool_calls") or [])


def _trim(messages: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """Karakter bütçesine sığan son mesajlar + fazın ilk mesajı (görev çapası) her zaman korunur.
    Eski araç sonuçları kısaltılır; başta yetim kalan araç mesajları atılır."""
    if not messages:
        return []
    anchor, rest = messages[0], messages[1:]
    tool_idx = [i for i, m in enumerate(rest) if m["role"] == "tool"]
    old = set(tool_idx[:-FULL_TOOL_RESULTS]) if len(tool_idx) > FULL_TOOL_RESULTS else set()
    rest = [({**m, "content": m["content"][:OLD_TOOL_RESULT_CHARS] + "…(eski sonuç kısaltıldı)"}
             if i in old and len(m["content"]) > OLD_TOOL_RESULT_CHARS else m) for i, m in enumerate(rest)]
    kept: list[dict[str, Any]] = []
    used = _size(anchor)
    for m in reversed(rest):
        used += _size(m)
        if used > budget and kept:
            break
        kept.append(m)
    kept.reverse()
    dropped = len(kept) < len(rest)
    while dropped and kept and kept[0]["role"] == "tool":  # çağrısı kesilmiş yetim araç sonuçları
        kept = kept[1:]
    if dropped:
        kept = [{"role": "user", "content": "[HARNESS] (Daha eski adımlar bağlamdan çıkarıldı; önemli sonuçlar sistem mesajındaki "
                                             "çalışma hafızasında.)"}] + kept
    return [anchor] + kept


_CLAIM_WORDS = ("oluşturdum", "oluşturuldu", "oluşturuluyor", "hazırladım", "hazırlandı", "tamamlandı", "tamamladım",
                "eklendi", "ekledim", "güncellendi", "güncelledim", "değiştirdim", "değiştirildi", "kaydedildi", "kaydettim",
                "dashboard hazır", "rapor hazır",
                # stil isteklerinde sık görülen "yapmadan yaptım" ifadeleri (ör. "40 punto olarak ayarlandı")
                "ayarlandı", "ayarladım", "uygulandı", "uyguladım", "yapıldı", "yaptım", "hale geldi", "hale getirildi",
                "değişti", "olarak güncellen", "kaldırıldı", "kaldırdım", "taşındı", "taşıdım",
                "ayarlanmış", "uygulanmış", "güncellenmiş", "değiştirilmiş", "yapılmış", "eklenmiş")


def _unfulfilled_claim(s: Session, text: str, start_version: int, start_datasets: int) -> str | None:
    """Model bir eylemi yaptığını söylüyor ama bu turda ilgili araç hiç çalışmadıysa düzeltme talimatı döndürür."""
    t = (text or "").lower()
    if not any(w in t for w in _CLAIM_WORDS):
        return None
    if s.phase == "design" and s.spec_version == start_version and s.datasets:
        return ("[HARNESS] Dashboard'u oluşturduğunu / güncellediğini yazdın ama dashboard bu turda DEĞİŞMEDİ. "
                "İstenen şey yapılabiliyorsa metin yazma, ŞİMDİ " + ("create_report_spec" if not s.spec else
                "update_visual / add_visual / update_report") + " aracını çağır. Yapılamıyorsa (ör. desteklenmeyen yazı tipi "
                "boyutu) ya da zaten öyleyse kullanıcıya bunu AÇIKÇA söyle; yapılmamış bir şeyi yapıldı diye anlatma.")
    if s.phase == "data" and len(s.datasets) == start_datasets:
        return "[HARNESS] Dataset'leri kaydettiğini yazdın ama save_datasets çağırmadın. ŞİMDİ save_datasets aracını çağır."
    return None


def _remember_sql(s: Session, args: dict[str, Any], content: Any) -> None:
    """Başarılı run_sql'leri çalışma hafızasına yazar (sistem mesajında gösterilir)."""
    sql = str(args.get("sql") or "").strip()
    if not sql:
        return
    prof = content.get("profile", {}) if isinstance(content, dict) else {}
    mem = [m for m in s.phase_memory.get("verified_sql", []) if m["sql"] != sql]
    mem.append({"purpose": str(args.get("purpose") or "")[:160], "sql": sql,
                "columns": list(prof.get("columns", {}).keys()), "rows": prof.get("row_count")})
    s.phase_memory["verified_sql"] = mem[-12:]


class Agent:
    def __init__(self, gateway: LLMGateway, services: Services, store: SessionStore):
        self.llm = gateway
        self.services = services
        self.store = store
        self.audit = Audit(services.settings.audit_log)
        self._actors: dict[str, str | None] = {}   # oturum → şu an mesajı işlenen kullanıcı (denetim kaydı)

    # ------------------------------------------------------------------ public
    def _budget(self, sys_prompt: str, schemas: list[dict[str, Any]]) -> tuple[int, int | None]:
        """Konuşma geçmişinin karakter bütçesi ve istenecek çıktı token'ı — modelin bağlam penceresine göre.
        Pencere biliniyorsa uygulama ayrıca sınır koymaz: pencereden sistem talimatı, araç tanımları ve yanıt payı
        düşülür, kalanın tamamı geçmişe ayrılır. Pencere bilinmiyorsa ayardaki sabit bütçe (LLM_CONTEXT_CHARS).
        Çıktı sınırı yalnız ayarda verildiyse gönderilir (None: modelin kendi sınırı)."""
        ctx = self.llm.context_window() if callable(getattr(self.llm, "context_window", None)) else None
        limit = self.llm.max_output_tokens() if callable(getattr(self.llm, "max_output_tokens", None)) else None
        if not ctx:
            return self.services.settings.llm_context_chars, limit
        reserve = min(limit, ctx // 2) if limit else max(1024, ctx // 4)   # yanıt için ayrılan pay
        fixed = (len(sys_prompt) + len(json.dumps(schemas, ensure_ascii=False))) / CHARS_PER_TOKEN
        avail = ctx - reserve - fixed - CONTEXT_MARGIN
        return max(1500, int(avail * CHARS_PER_TOKEN)), limit

    def run_turn(self, sid: str, text: str, images: list[str] | None = None, user: str | None = None,
                 role: str | None = None) -> Iterator[Event]:
        """user / role: mesajı gönderen (denetim kaydı) ve onun GÜNCEL veri rolü — rolü geri alınan kullanıcı
        oturumu oluşturduğu andaki yetkiyle sorgu çalıştıramaz."""
        lock = self.store.lock(sid)
        if not lock.acquire(blocking=False):
            yield Event("error", {"message": "Bu oturumda zaten çalışan bir istek var."})
            yield Event("done", {})
            return
        s: Session | None = None
        try:
            s = self.store.get(sid)
            s.busy = True
            if role:
                s.user_role = role
            self._actors[sid] = user
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
            self._actors.pop(sid, None)
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
        claim_nudges = 0
        loop_nudged = False
        start_version, start_datasets = s.spec_version, len(s.datasets)
        for step in range(self.services.settings.max_agent_steps):
            yield Event("status", {"text": "Düşünüyor…" if step == 0 else "Devam ediyor…"})
            tools = tools_for(s.phase)
            remaining = self.services.settings.max_agent_steps - step
            sys_prompt = system_prompt(s, self.services.connector.dialect, steps_left=remaining,
                                       rules=_safe_rules(self.services, s))
            schemas = [t.schema() for t in tools]
            budget, max_out = self._budget(sys_prompt, schemas)
            t0 = time.perf_counter()
            for attempt in range(3):   # bağlam penceresi aşılırsa geçmişi kısaltıp yeniden dene
                messages = [{"role": "system", "content": sys_prompt}] + _trim(s.llm_messages, budget)
                try:
                    turn: AssistantTurn = (self.llm.chat(messages, schemas, max_tokens=max_out) if max_out
                                           else self.llm.chat(messages, schemas))
                    break
                except ContextOverflow as e:
                    if attempt == 2:
                        fixed = int((len(sys_prompt) + len(json.dumps(schemas, ensure_ascii=False))) / CHARS_PER_TOKEN)
                        raise LLMError(
                            f"Modelin bağlam penceresi çok küçük ({e.context or '?'} token): geçmiş kısaltılsa da bu fazın "
                            f"talimatı ve araç tanımları (~{fixed} token) yanıt payıyla birlikte sığmıyor. Model sunucusunda "
                            "bağlam uzunluğunu artırın (LM Studio: modeli yüklerken Context Length en az 32768; vLLM: "
                            "--max-model-len) ya da daha geniş bağlamlı bir model seçin.") from e
                    fresh, max_out = self._budget(sys_prompt, schemas)
                    budget = max(1500, min(fresh, budget // 2))
                    log.info("Bağlam sığmadı (pencere %s, girdi %s token); geçmiş %d karaktere kısaltılıp yeniden deneniyor.",
                             e.context, e.input_tokens, budget)
                    yield Event("status", {"text": "Bağlam sığmadı; eski adımlar kısaltılıp yeniden deneniyor…"})
            self.audit.write(session=s.id, user=self._actors.get(s.id), event="llm", phase=s.phase, ms=int((time.perf_counter() - t0) * 1000),
                             tool_calls=[c.name for c in turn.tool_calls], usage=turn.usage, tool_mode=self.llm.tool_mode)

            if not turn.tool_calls:
                if not turn.content and empty_retries == 0:
                    empty_retries += 1
                    s.llm_messages.append({"role": "user", "content": "[HARNESS] Boş yanıt verdin. Kullanıcıya yanıt yaz ya da bir araç çağır."})
                    continue
                nudge = _unfulfilled_claim(s, turn.content, start_version, start_datasets)
                if nudge and claim_nudges < 2:
                    # model eylemi yaptığını söylüyor ama araç çağırmadı: metni kullanıcıya göstermeden düzelt
                    claim_nudges += 1
                    s.llm_messages.append(turn.to_message())
                    s.llm_messages.append({"role": "user", "content": nudge})
                    self.audit.write(session=s.id, user=self._actors.get(s.id), event="claim_nudge", phase=s.phase)
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

            # model önerisi hazır ama model kullanıcıya sormadan araç çağırmayı sürdürüyor: turu sistem bitirir ve
            # öneriyi onay sorusuyla gösterir (SQL'den önce kullanıcı onayı kuralı)
            if s.phase == "data" and s.phase_memory.get("ask_refusals", 0) >= ASK_REFUSALS_BEFORE_STOP \
                    and s.model_proposal_at is not None and not any(t.role == "user" for t in s.transcript[s.model_proposal_at:]):
                s.phase_memory["ask_refusals"] = 0
                msg = proposal_question(s.phase_memory.get("last_proposal"))
                s.llm_messages.append({"role": "assistant", "content": msg})
                yield self._emit(s, TranscriptItem(role="assistant", content=msg))
                self.store.save(s)
                return

            stuck_tool = next((n for n, c in s.phase_memory.get("fail_streak", {}).items() if c >= MAX_FAIL_STREAK), None)
            if (s.phase_memory.get("repeats", 0) >= MAX_REPEATS or stuck_tool) and not next_phase:
                s.phase_memory["fail_streak"] = {}
                if s.phase == "data" and len(s.phase_memory.get("verified_sql", [])) >= 3 and not loop_nudged:
                    # önce ileri it: doğrulanmış sorgular yeterli, kaydetmesini söyle
                    loop_nudged = True
                    s.phase_memory["repeats"] = 0
                    s.llm_messages.append({"role": "user", "content": (
                        "[HARNESS] Aynı sorguları tekrarlıyorsun. Yeterli doğrulanmış sorgu var: ŞİMDİ save_datasets çağır ve "
                        "çalışma hafızasındaki sorguları numarayla kaydet (ör. {\"id\": \"monthly_trend\", \"verified\": 3}).")})
                    continue
                # döngü kırıcı: model aynı çağrıyı üst üste tekrarlıyor → turu bitir, kullanıcıya açıkla
                s.phase_memory["repeats"] = 0
                verified = len(s.phase_memory.get("verified_sql", []))
                msg = ("Aynı adımı tekrar tekrar denediğimi fark ettim ve durdum." +
                       (f" Bu fazda {verified} sorgu doğrulandı; \"doğrulanmış sorgularla devam et\" yazarsanız bunlarla ilerlerim"
                        " ya da takıldığım kırılımı farklı tarif edebilirsiniz." if verified else
                        " İsteği biraz farklı ifade eder misiniz?"))
                s.llm_messages.append({"role": "assistant", "content": msg})
                yield self._emit(s, TranscriptItem(role="assistant", content=msg))
                self.store.save(s)
                return

            if next_phase:
                s.set_phase(next_phase)
                yield self._emit(s, TranscriptItem(role="system", content={
                    "data": "Veri keşfi fazına geçildi", "design": "Tasarım fazına geçildi"}.get(next_phase, next_phase)))
                carry = f"\n\nKullanıcının bu turdaki mesajı (hâlâ geçerli, dikkate al): «{text}»" if text else ""
                s.llm_messages.append({"role": "user", "content": f"[HARNESS] {kickoff}{carry}"})
                self.store.save(s)              # önce kayıt: arayüz "state" ile hemen veri / filtre ister
                yield Event("state", s.public())

        yield self._emit(s, TranscriptItem(role="assistant", content=(
            "Bu istek için adım sınırına ulaştım. Şu ana kadarki ilerleme kaydedildi; "
            "\"devam et\" yazarak kaldığım yerden sürdürebilirsiniz.")))
        s.llm_messages.append({"role": "assistant", "content": "(adım sınırına ulaşıldı)"})

    def _run_tool(self, s: Session, call: ToolCall) -> Iterator[Event]:
        tool = TOOLS_BY_NAME.get(call.name)
        t0 = time.perf_counter()
        skipped = False
        if call.parse_error:
            result = ToolResult(False, {"error": call.parse_error}, "Argümanlar okunamadı")
        elif tool is None:
            result = ToolResult(False, {"error": f"'{call.name}' diye bir araç yok. Kullanılabilir: {[t.name for t in tools_for(s.phase)]}"},
                                "Bilinmeyen araç")
        elif s.phase not in tool.phases:
            skipped = True
            hint = {"requirements": "Bu fazda yalnız ihtiyacı netleştir: kullanıcıya sor ya da save_requirements çağır.",
                    "data": "Bu fazda veri kümelerini hazırla; dashboard tasarım fazında oluşturulur.",
                    "design": "Bu fazda dashboard'u düzenle; yeni veri gerekiyorsa add_dataset kullan."}.get(s.phase, "")
            result = ToolResult(False, {"error": f"'{call.name}' bu fazda ({s.phase}) kullanılmaz; çağrı atlandı. {hint} "
                                                 f"Kullanılabilir araçlar: {[t.name for t in tools_for(s.phase)]}"},
                                "Bu fazda kullanılmaz — atlandı")
        else:
            cache = s.phase_memory.setdefault("calls", {})
            key = f"{call.name}:{json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)}"
            if call.name in CACHEABLE_TOOLS and key in cache:
                # tekrar koruması: aynı okuma çağrısı — sonucu yeniden ver ama modeli ilerlemeye it
                prev = cache[key]
                s.phase_memory["repeats"] = s.phase_memory.get("repeats", 0) + 1
                advice = ("Bu çağrıyı bu fazda zaten yaptın; sonuç aşağıda. TEKRARLAMA." +
                          (" Sorgu hatalıydı: hata ve İPUCU'ya göre FARKLI bir sorgu yaz ya da bu kırılımı atlayıp doğrulanmış "
                           "sorgularla devam et." if not prev["ok"] else " Bir sonraki adıma geç."))
                result = ToolResult(prev["ok"], {"note": advice, "previous_result": prev["content"]}, "Tekrarlanan çağrı (önbellekten)")
            else:
                s.phase_memory["repeats"] = 0
                if tool.status:
                    yield Event("status", {"text": tool.status})
                try:
                    result = tool.handler(ToolContext(s, self.services), call.arguments)
                except Exception as e:  # noqa: BLE001
                    log.exception("Araç hatası: %s", call.name)
                    result = ToolResult(False, {"error": f"Araç çalışırken hata: {type(e).__name__}: {e}"}, "Araç hatası")
                if call.name in CACHEABLE_TOOLS:
                    cache[key] = {"ok": result.ok, "content": result.content}
                if call.name == "run_sql" and result.ok:
                    _remember_sql(s, call.arguments, result.content)
        ms = int((time.perf_counter() - t0) * 1000)
        fails = s.phase_memory.setdefault("fail_streak", {})
        fails[call.name] = 0 if result.ok else fails.get(call.name, 0) + 1

        s.llm_messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                               "content": to_llm_content(result.content, self.services.settings.tool_result_char_limit)})
        self.audit.write(session=s.id, user=self._actors.get(s.id), event="tool", phase=s.phase, tool=call.name, ok=result.ok, ms=ms,
                         arguments=call.arguments, summary=result.summary,
                         errors=result.content.get("errors") if isinstance(result.content, dict) else None)
        yield self._emit(s, TranscriptItem(role="tool", content=result.summary, tool=ToolInfo(
            name=call.name, arguments=call.arguments, ok=result.ok, summary=result.summary, durationMs=ms, skipped=skipped)))
        if result.state_changed:
            self.store.save(s)   # arayüz bu olayla dashboard verisini / filtreleri ister: diskteki oturum güncel olmalı
            yield Event("state", s.public())
        return result
