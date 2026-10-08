"""Session durumu ve dosya tabanlı kalıcılık.

İki ayrı geçmiş tutulur:
  * transcript    — kullanıcının gördüğü her şey (UI)
  * llm_messages  — modelin o fazdaki bağlamı; faz değişince sıfırlanır ve yapılandırılmış
                    durum (requirements / datasets / spec) yeni fazın sistem mesajıyla aktarılır.
Bu sayede küçük bağlam pencereli lokal modeller uzun oturumlarda da çalışır.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.spec.models import Dataset, ReportSpec

Phase = Literal["requirements", "data", "design"]
PHASES: list[Phase] = ["requirements", "data", "design"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


DEFAULT_TITLE = "Yeni rapor"


class ToolInfo(BaseModel):
    name: str
    arguments: Any = None
    ok: bool = True
    summary: str = ""
    durationMs: int = 0


class TranscriptItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    role: Literal["user", "assistant", "tool", "system"]
    content: str = ""
    images: list[str] | None = None
    tool: ToolInfo | None = None
    phase: Phase = "requirements"
    createdAt: str = Field(default_factory=now_iso)


class Requirements(BaseModel):
    report_title: str
    business_goal: str = ""
    audience: str = ""
    kpis: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    time_range: str = ""
    filters: list[str] = Field(default_factory=list)
    notes: str | None = None


class DesignBrief(BaseModel):
    summary: str = ""
    mode: Literal["light", "dark"] | None = None
    palette: list[str] | None = None
    background: str | None = None
    accent: str | None = None
    layout: str | None = None
    chart_types: list[str] | None = None
    style_notes: list[str] | None = None


class Session(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    title: str = "Yeni rapor"  # DEFAULT_TITLE
    phase: Phase = "requirements"
    transcript: list[TranscriptItem] = Field(default_factory=list)
    requirements: Requirements | None = None
    datasets: list[Dataset] = Field(default_factory=list)
    design_brief: DesignBrief | None = None
    spec: ReportSpec | None = None
    spec_version: int = 0
    busy: bool = False
    createdAt: str = Field(default_factory=now_iso)
    updatedAt: str = Field(default_factory=now_iso)
    # --- yalnızca harness içi
    llm_messages: list[dict[str, Any]] = Field(default_factory=list)
    dataset_profiles: dict[str, Any] = Field(default_factory=dict)  # faz geçişinde bulgular kaybolmasın
    # faz içi çalışma hafızası: doğrulanan sorgular + tekrar eden araç çağrılarının önbelleği (faz değişince sıfırlanır)
    phase_memory: dict[str, Any] = Field(default_factory=dict)
    user_role: str = "standart"
    status: Literal["idea", "design", "test", "live"] | None = None   # yaşam döngüsü; None → içerikten türetilir
    model_relationships: list[dict[str, Any]] = Field(default_factory=list)   # yalnız bu rapora özel onaylı ilişkiler
    model_proposal_at: int | None = None   # propose_model anındaki transcript uzunluğu: ilişki ancak sonraki kullanıcı onayıyla kaydedilir
    title_locked: bool = False        # kullanıcı adı elle verdiyse True: agent başlığı değiştirmez
    owner: str | None = None          # oluşturan kullanıcı (DOMAIN\kullanıcı)
    owner_name: str | None = None     # görünen ad

    def public(self) -> dict[str, Any]:
        return self.model_dump(exclude={"llm_messages", "user_role", "dataset_profiles", "phase_memory"})

    def add(self, item: TranscriptItem) -> TranscriptItem:
        item.phase = self.phase
        self.transcript.append(item)
        return item

    def set_phase(self, phase: Phase) -> None:
        if phase != self.phase:
            self.phase = phase
            self.llm_messages = []
            self.phase_memory = {}

    def set_spec(self, spec: ReportSpec) -> None:
        if self.title_locked and self.title:
            spec.title = self.title  # kullanıcının verdiği ad dashboard başlığında da korunur
        self.spec = spec
        self.spec_version += 1
        if spec.title and self.title in ("Yeni rapor", ""):
            self.title = spec.title


def _source_tables(datasets: list[dict[str, Any]]) -> list[str]:
    """Veri kümelerinin SQL'inde geçen şema.tablo adları (view'a geçmişse orijinal SQL de)."""
    import sqlglot
    from sqlglot import exp
    names: set[str] = set()
    for ds in datasets:
        for sql in (ds.get("sql"), ds.get("original_sql")):
            if not sql:
                continue
            try:
                for tree in sqlglot.parse(sql, read="tsql"):
                    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)} if tree else set()
                    for t in tree.find_all(exp.Table) if tree else []:
                        if t.name and t.name.lower() not in ctes:   # ek veritabanı: db.şema.nesne
                            prefix = f"{t.catalog}." if t.catalog else ""
                            names.add(f"{prefix}{t.db or 'dbo'}.{t.name}".lower())
            except Exception:  # bozuk SQL envanteri düşürmesin
                continue
    return sorted(names)


def _summary(d: dict[str, Any]) -> dict[str, Any]:
    """Rapor envanteri kartı için özet: amaç, kapsam, içerik ve görünüm bilgisi."""
    req = d.get("requirements") or {}
    spec = d.get("spec") or {}
    visuals = spec.get("visuals") or []
    theme = spec.get("theme") or {}
    return {
        "id": d["id"], "title": d.get("title", ""), "phase": d.get("phase"),
        "updatedAt": d.get("updatedAt"), "createdAt": d.get("createdAt"),
        "subtitle": spec.get("subtitle"), "business_goal": req.get("business_goal"), "audience": req.get("audience"),
        "kpis": req.get("kpis") or [], "dimensions": req.get("dimensions") or [], "time_range": req.get("time_range"),
        "visual_count": len(visuals), "dataset_count": len(d.get("datasets") or []),
        "visual_types": sorted({v.get("type") for v in visuals if v.get("type")}),
        "kpi_titles": [v.get("title") for v in visuals if v.get("type") == "kpi"][:6],
        "filters": [f.get("label") for f in spec.get("filters") or []],
        "views": sorted({ds.get("view") for ds in d.get("datasets") or [] if ds.get("view")}),
        "theme": {"mode": theme.get("mode"), "accent": theme.get("accent"), "background": theme.get("background"),
                  "palette": (theme.get("palette") or [])[:5]} if theme else None,
        "has_spec": bool(spec),
        "status": d.get("status") or ("design" if spec else "idea"),
        "status_explicit": bool(d.get("status")),
        "owner": d.get("owner"), "owner_name": d.get("owner_name"),
        "source_tables": _source_tables(d.get("datasets") or []),
    }


def _replace(src: Path, dst: Path, tries: int = 40) -> None:
    """Atomik değiştirme. Windows'ta hedef dosya o an başka bir istekte okunuyorsa (liste, oturum açma) os.replace
    'Erişim engellendi' verir; kısa aralıklarla yeniden denenir (okuma milisaniyeler sürer)."""
    for i in range(tries):
        try:
            src.replace(dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.025 * (1 + i // 10))


class SessionStore:
    def __init__(self, directory: Path):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def lock(self, sid: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(sid, threading.Lock())

    def _path(self, sid: str) -> Path:
        if not sid.isalnum():
            raise KeyError(sid)
        return self.dir / f"{sid}.json"

    def create(self, user_role: str, owner: str | None = None, owner_name: str | None = None) -> Session:
        s = Session(user_role=user_role, owner=owner, owner_name=owner_name)
        self.save(s)
        return s

    def get(self, sid: str) -> Session:
        p = self._path(sid)
        if not p.exists():
            raise KeyError(sid)
        s = Session.model_validate_json(p.read_text(encoding="utf-8"))
        if s.busy and not self.lock(sid).locked():  # sunucu çökmüşse takılı kalmasın
            s.busy = False
        return s

    def save(self, s: Session) -> None:
        s.updatedAt = now_iso()
        tmp = self._path(s.id).with_suffix(".tmp")
        tmp.write_text(s.model_dump_json(), encoding="utf-8")
        _replace(tmp, self._path(s.id))
        self._enforce_unique_title(s)

    @staticmethod
    def _norm_title(t: str | None) -> str:
        return " ".join((t or "").replace("İ", "i").replace("I", "ı").lower().split())

    @staticmethod
    def _owner_key(owner: str | None) -> str:
        return (owner or "").strip().lower()

    def _enforce_unique_title(self, s: Session) -> None:
        """Aynı isimle tek rapor (aynı sahip içinde): bu oturum bir başlığı aldıysa sahibin aynı başlıklı diğer
        oturumları silinir (üstüne yazma). Başka kullanıcıların raporlarına dokunulmaz."""
        key = self._norm_title(s.title)
        owner = self._owner_key(s.owner)
        if not key or key == self._norm_title(DEFAULT_TITLE):
            return
        for p in self.dir.glob("*.json"):
            if p.stem == s.id:
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if self._norm_title(d.get("title")) == key and self._owner_key(d.get("owner")) == owner \
                    and not self.lock(p.stem).locked():
                p.unlink(missing_ok=True)

    def title_taken(self, title: str, except_id: str, owner: str | None = None) -> str | None:
        """Aynı sahibin başka bir raporunda bu isim kullanılıyorsa o raporun id'si."""
        key = self._norm_title(title)
        for item in self.list():
            if item["id"] != except_id and self._norm_title(item["title"]) == key \
                    and self._owner_key(item.get("owner")) == self._owner_key(owner):
                return item["id"]
        return None

    def dedupe_titles(self) -> int:
        """Başlangıçta: her sahibin aynı başlıklı eski oturumlarından yalnız en son güncelleneni bırakır."""
        seen: set[tuple[str, str]] = set()
        removed = 0
        for item in self.list():  # en yeni önce
            key = (self._owner_key(item.get("owner")), self._norm_title(item["title"]))
            if not key[1] or key[1] == self._norm_title(DEFAULT_TITLE):
                continue
            if key in seen:
                self._path(item["id"]).unlink(missing_ok=True)
                removed += 1
            seen.add(key)
        return removed

    def delete(self, sid: str) -> None:
        self._path(sid).unlink(missing_ok=True)

    def list(self) -> list[dict[str, Any]]:
        out = []
        for p in self.dir.glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append(_summary(d))
            except (json.JSONDecodeError, KeyError):
                continue
        return sorted(out, key=lambda x: x.get("updatedAt") or "", reverse=True)
