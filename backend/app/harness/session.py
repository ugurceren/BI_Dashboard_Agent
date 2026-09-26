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
    title: str = "Yeni rapor"
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
    user_role: str = "analyst"

    def public(self) -> dict[str, Any]:
        return self.model_dump(exclude={"llm_messages", "user_role", "dataset_profiles"})

    def add(self, item: TranscriptItem) -> TranscriptItem:
        item.phase = self.phase
        self.transcript.append(item)
        return item

    def set_phase(self, phase: Phase) -> None:
        if phase != self.phase:
            self.phase = phase
            self.llm_messages = []

    def set_spec(self, spec: ReportSpec) -> None:
        self.spec = spec
        self.spec_version += 1
        if spec.title and self.title in ("Yeni rapor", ""):
            self.title = spec.title


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

    def delete(self, sid: str) -> None:
        self._path(sid).unlink(missing_ok=True)

    def list(self) -> list[dict[str, Any]]:
        out = []
        for p in self.dir.glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append({"id": d["id"], "title": d.get("title", ""), "phase": d.get("phase"), "updatedAt": d.get("updatedAt")})
            except (json.JSONDecodeError, KeyError):
                continue
        return sorted(out, key=lambda x: x.get("updatedAt") or "", reverse=True)
