"""FastAPI uygulaması. Çalıştırma:  uvicorn app.main:app --port 8000 --reload"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import Any, Iterator
from urllib.parse import quote

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ValidationError

from app.config import BACKEND_DIR, get_settings, load_toml
from app.data.connector import QueryError, create_connector
from app.data.validator import RolePolicy, SqlValidator
from app.dictionary.repository import DataDictionary
from app.harness.agent import Agent, Event
from app.harness.session import PHASES, Requirements, SessionStore, TranscriptItem
from app.harness.tools import Services, ToolContext, _build_dataset, _validate_spec
from app.llm.gateway import LLMGateway
from app.spec.models import DatasetField, ReportSpec

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bi-agent")



class AppState:
    services: Services
    gateway: LLMGateway
    store: SessionStore
    agent: Agent


state = AppState()


def build_services() -> Services:
    settings = get_settings()
    connector = create_connector(settings)
    dictionary = DataDictionary(settings, connector).load()
    policy_cfg = load_toml(settings.policy_config)
    policies = {name: RolePolicy(name=name, **cfg) for name, cfg in policy_cfg.get("roles", {}).items()}
    validator = SqlValidator(dictionary, connector.dialect, policy_cfg.get("sql", {}).get("denied_functions", []))
    return Services(settings, dictionary, connector, validator, policies)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    state.services = build_services()
    state.gateway = LLMGateway(settings)
    state.store = SessionStore(settings.sessions_dir)
    state.agent = Agent(state.gateway, state.services, state.store)
    log.info("Sözlük: %d tablo, %d metrik | LLM: %s @ %s | vision: %s", len(state.services.dictionary.tables),
             len(state.services.dictionary.metrics), settings.llm_model, settings.llm_base_url, settings.vision_model or "-")
    yield


app = FastAPI(title="BI Rapor Agent", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_methods=["*"], allow_headers=["*"])


def _session(sid: str):
    try:
        return state.store.get(sid)
    except KeyError:
        raise HTTPException(404, "Oturum bulunamadı")


# --------------------------------------------------------------------------- sağlık
@app.get("/api/health")
def health() -> dict[str, Any]:
    s = get_settings()
    data: dict[str, Any] = {"ok": True, "dialect": state.services.connector.dialect}
    try:
        state.services.connector.ping()
    except Exception as e:  # noqa: BLE001
        data = {"ok": False, "dialect": state.services.connector.dialect, "error": str(e)[:200]}
    llm = state.gateway.health()
    return {"ok": llm["reachable"] and data["ok"], "llm": llm,
            "vision": {"configured": bool(s.vision_model), "model": s.vision_model}, "data": data,
            "dictionary": {"tables": len(state.services.dictionary.tables)}}


# --------------------------------------------------------------------------- oturumlar
@app.get("/api/sessions")
def list_sessions() -> list[dict[str, Any]]:
    return state.store.list()


@app.post("/api/sessions")
def create_session() -> dict[str, Any]:
    return state.store.create(get_settings().user_role).public()


@app.get("/api/sessions/{sid}")
def get_session(sid: str) -> dict[str, Any]:
    return _session(sid).public()


@app.delete("/api/sessions/{sid}")
def delete_session(sid: str) -> dict[str, Any]:
    _session(sid)
    state.store.delete(sid)
    return {"ok": True}


class MessageIn(BaseModel):
    content: str = ""
    images: list[str] | None = None


def _sse(events: Iterator[Event]) -> Iterator[str]:
    for ev in events:
        yield f"event: {ev.type}\ndata: {json.dumps(ev.data, ensure_ascii=False, default=str)}\n\n"


@app.post("/api/sessions/{sid}/messages")
def post_message(sid: str, body: MessageIn) -> StreamingResponse:
    _session(sid)
    if not body.content.strip() and not body.images:
        raise HTTPException(400, "Mesaj boş")
    images = [i for i in (body.images or []) if i.startswith("data:image/")][:3]
    return StreamingResponse(_sse(state.agent.run_turn(sid, body.content, images)), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class PhaseIn(BaseModel):
    phase: str


@app.post("/api/sessions/{sid}/phase")
def set_phase(sid: str, body: PhaseIn) -> dict[str, Any]:
    if body.phase not in PHASES:
        raise HTTPException(400, f"Geçersiz faz. Seçenekler: {PHASES}")
    with state.store.lock(sid):
        s = _session(sid)
        if body.phase == "data" and not s.requirements:
            raise HTTPException(409, "Önce gereksinimler kaydedilmeli.")
        if body.phase == "design" and not s.datasets:
            raise HTTPException(409, "Önce dataset'ler kaydedilmeli.")
        s.set_phase(body.phase)  # type: ignore[arg-type]

        s.add(TranscriptItem(role="system", content=f"Kullanıcı '{body.phase}' fazına döndü"))
        s.llm_messages.append({"role": "user", "content": "[HARNESS] Kullanıcı bu faza geri döndü. Mevcut durumu kısaca özetle ve ne değiştirmek istediğini sor."})
        state.store.save(s)
        return s.public()


@app.put("/api/sessions/{sid}/spec")
def put_spec(sid: str, spec: dict[str, Any] = Body(...)) -> Any:
    with state.store.lock(sid):
        s = _session(sid)
        # Elle düzenlemede SQL değişmiş/yeni dataset'ler de doğrulanır.
        for raw in spec.get("datasets") or []:
            known = next((d for d in s.datasets if d.id == raw.get("id")), None)
            if not known or known.sql.strip() != str(raw.get("sql", "")).strip():
                ds, prof, errs = _build_dataset(ToolContext(s, state.services), raw)
                if errs:
                    return JSONResponse({"detail": errs}, status_code=422)
                s.datasets = [d for d in s.datasets if d.id != ds.id] + [ds]
                s.dataset_profiles[ds.id] = prof
        new_spec, errs, _ = _validate_spec(ToolContext(s, state.services), spec)
        if errs:
            return JSONResponse({"detail": errs}, status_code=422)
        s.set_spec(new_spec)
        state.store.save(s)
        return s.public()


@app.post("/api/sessions/{sid}/demo")
def load_demo(sid: str) -> Any:
    with state.store.lock(sid):
        s = _session(sid)
        raw = json.loads(get_settings().demo_spec.read_text(encoding="utf-8"))
        ctx = ToolContext(s, state.services)
        built, profiles = [], {}
        for d in raw["datasets"]:
            ds, prof, errs = _build_dataset(ctx, d)
            if errs:
                raise HTTPException(500, f"Demo dataset hatası: {errs}")
            # demo'daki elle verilmiş etiket/formatları koru
            ds.fields = [DatasetField.model_validate(f) for f in d.get("fields") or []] or ds.fields
            built.append(ds)
            profiles[ds.id] = prof
        s.datasets, s.dataset_profiles = built, profiles
        req = raw.pop("requirements", None) or {}
        s.requirements = Requirements.model_validate({
            "report_title": raw["title"], "business_goal": raw.get("subtitle") or "", "audience": "Yönetim",
            "kpis": [v["title"] for v in raw["visuals"] if v["type"] == "kpi"],
            "dimensions": [], **req})
        spec = ReportSpec.model_validate({**raw, "datasets": [d.model_dump() for d in built]})
        s.set_phase("design")
        s.set_spec(spec)
        s.title = raw["title"]

        s.add(TranscriptItem(role="system", content="Demo dashboard yüklendi. Değişiklik isteyebilirsiniz (ör. \"bölge grafiğini donut yap\")."))
        s.llm_messages = [{"role": "user", "content": "[HARNESS] Demo dashboard yüklendi. Kullanıcının değişiklik isteklerini bekle."}]
        state.store.save(s)
        return s.public()


# --------------------------------------------------------------------------- dashboard verisi
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()
CACHE_TTL_S = 120


def _dataset_payload(sql: str, role: str) -> dict[str, Any]:
    key = hashlib.sha1(f"{role}|{sql}".encode()).hexdigest()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_S:
            return hit[1]
    pol = state.services.policy(role)
    v = state.services.validator.validate(sql, pol)
    if not v.ok:
        return {"columns": [], "rows": [], "error": "; ".join(v.errors)}
    try:
        r = state.services.connector.execute(v.sql, pol.max_rows)
    except QueryError as e:
        return {"columns": [], "rows": [], "error": str(e)[:300]}
    payload = {"columns": r.columns, "rows": r.rows, **({"truncated": True} if r.truncated else {})}
    with _cache_lock:
        _cache[key] = (time.time(), payload)
        if len(_cache) > 500:
            _cache.clear()
    return payload


def _dashboard_data(s) -> dict[str, Any]:
    datasets = {d.id: d for d in s.datasets}
    if s.spec:
        datasets.update({d.id: d for d in s.spec.datasets})
    return {"datasets": {did: _dataset_payload(d.sql, s.user_role) for did, d in datasets.items()}}


@app.get("/api/sessions/{sid}/dashboard-data")
def dashboard_data(sid: str) -> dict[str, Any]:
    return _dashboard_data(_session(sid))


@app.get("/api/sessions/{sid}/export/html")
def export_html(sid: str) -> HTMLResponse:
    s = _session(sid)
    if not s.spec:
        raise HTTPException(409, "Henüz dashboard yok.")
    viewer = get_settings().viewer_html
    if not viewer.exists():
        raise HTTPException(503, "Viewer derlenmemiş: frontend klasöründe `npm run build:viewer` çalıştırın.")
    payload = json.dumps({"spec": s.spec.model_dump(exclude_none=True), "data": _dashboard_data(s)},
                         ensure_ascii=False, default=str).replace("<", "\\u003c")
    html = viewer.read_text(encoding="utf-8").replace("__REPORT_JSON__", payload, 1)
    title = s.spec.title or "dashboard"
    ascii_name = "".join(c if c.isascii() and c.isalnum() else "_" for c in title)[:60] or "dashboard"
    return HTMLResponse(html, headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}.html\"; "
                                                              f"filename*=UTF-8''{quote(title[:60])}.html"})


# --------------------------------------------------------------------------- sözlük
@app.get("/api/dictionary/search")
def dictionary_search(q: str = "") -> list[dict[str, Any]]:
    return state.services.dictionary.search(q, 10) if q.strip() else []


@app.post("/api/dictionary/reload")
def dictionary_reload() -> dict[str, Any]:
    state.services.dictionary.load()
    return {"ok": True, "tables": len(state.services.dictionary.tables)}


@app.exception_handler(ValidationError)
def _validation_handler(_: Request, exc: ValidationError) -> JSONResponse:
    return JSONResponse({"detail": [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]}, status_code=422)
