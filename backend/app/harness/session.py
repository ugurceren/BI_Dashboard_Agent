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
    user_role: str = "analyst"

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
        self.spec = spec
        self.spec_version += 1
        if spec.title and self.title in ("Yeni rapor", ""):
            self.title = spec.title


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
    }


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

    def create(self, user_role: str) -> Session:
        s = Session(user_role=user_role)
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
        tmp.replace(self._path(s.id))
        self._enforce_unique_title(s)

    @staticmethod
    def _norm_title(t: str | None) -> str:
        return " ".join((t or "").replace("İ", "i").replace("I", "ı").lower().split())

    def _enforce_unique_title(self, s: Session) -> None:
        """Aynı isimle tek rapor: bu oturum bir başlığı aldıysa aynı başlıklı diğer oturumlar silinir (üstüne yazma)."""
        key = self._norm_title(s.title)
        if not key or key == self._norm_title(DEFAULT_TITLE):
            return
        for p in self.dir.glob("*.json"):
            if p.stem == s.id:
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if self._norm_title(d.get("title")) == key and not self.lock(p.stem).locked():
                p.unlink(missing_ok=True)

    def title_taken(self, title: str, except_id: str) -> str | None:
        """Başka bir raporda bu isim kullanılıyorsa o raporun id'si."""
        key = self._norm_title(title)
        for item in self.list():
            if item["id"] != except_id and self._norm_title(item["title"]) == key:
                return item["id"]
        return None

    def dedupe_titles(self) -> int:
        """Başlangıçta: aynı başlıklı eski oturumlardan yalnız en son güncelleneni bırakır."""
        seen: set[str] = set()
        removed = 0
        for item in self.list():  # en yeni önce
            key = self._norm_title(item["title"])
            if not key or key == self._norm_title(DEFAULT_TITLE):
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
