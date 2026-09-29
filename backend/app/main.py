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
from sqlglot import exp

from app.config import BACKEND_DIR, get_settings, load_toml
from app.data.connector import QueryError, create_connector
from app.data.model_filters import ModelFilter, ModelFilterEngine
from app.data.views import ViewRegistry, build_view_script, safe_view_name, select_from_view, view_columns
from app.data.validator import RolePolicy, SqlValidator
from app.dictionary.repository import DataDictionary
from app.harness.agent import Agent, Event
from app.harness.session import PHASES, Requirements, SessionStore, TranscriptItem, now_iso
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
    removed = state.store.dedupe_titles()
    if removed:
        log.info("Aynı başlıklı %d eski rapor oturumu kaldırıldı (aynı isimle tek rapor).", removed)
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


class TitleIn(BaseModel):
    title: str
    overwrite: bool = False   # aynı isimde başka rapor varsa üstüne yaz (o rapor silinir)


@app.put("/api/sessions/{sid}/title")
def rename_session(sid: str, body: TitleIn) -> Any:
    title = " ".join(body.title.split())
    if not title:
        raise HTTPException(400, "Rapor adı boş olamaz.")
    if len(title) > 120:
        raise HTTPException(400, "Rapor adı en fazla 120 karakter olabilir.")
    with state.store.lock(sid):
        s = _session(sid)
        other = state.store.title_taken(title, sid)
        if other and not body.overwrite:
            return JSONResponse({"detail": f"'{title}' adında başka bir rapor var.", "conflict_id": other}, status_code=409)
        s.title = title
        if s.spec:
            s.spec.title = title
            s.spec_version += 1
        if s.requirements:
            s.requirements.report_title = title
        state.store.save(s)   # aynı isimli diğer rapor (overwrite onaylandıysa) burada silinir
        return s.public()


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


def _all_datasets(s) -> dict[str, Any]:
    datasets = {d.id: d for d in s.datasets}
    if s.spec:
        datasets.update({d.id: d for d in s.spec.datasets})
    return datasets


def _engine() -> ModelFilterEngine:
    return ModelFilterEngine(state.services.dictionary, state.services.connector.dialect)


def _bindings(s) -> dict[str, dict[str, str]]:
    """dataset → {alan: 'şema.tablo.kolon'}: hangi dataset kolonu hangi model kolonundan geliyor (tıklayarak filtre için)."""
    eng = _engine()
    return {did: eng.lineage(d.sql) for did, d in _all_datasets(s).items()}


def _filter_defs(s, bindings: dict[str, dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """Spec filtrelerini model kolonuna çözer (table+column ya da field'ın kökeni)."""
    if not s.spec:
        return []
    out = []
    for f in s.spec.filters:
        key = f"{f.table}.{f.column}".lower() if f.table and f.column else None
        if not key and f.field:
            bindings = bindings if bindings is not None else _bindings(s)
            key = next((b[f.field] for b in bindings.values() if f.field in b), None)
        out.append({"id": f.id, "label": f.label, "type": f.type, "field": f.field, "key": key})
    return out


_options_cache: dict[str, tuple[float, list[Any]]] = {}


def _filter_options(key: str, role: str) -> list[Any]:
    hit = _options_cache.get(f"{role}|{key}")
    if hit and time.time() - hit[0] < CACHE_TTL_S * 5:
        return hit[1]
    table, _, column = key.rpartition(".")
    dd = state.services.dictionary
    t = dd.tables.get(table)
    col = next((c for c in t.columns if c.name == column), None) if t else None
    if not t or not col:
        return []
    dialect = state.services.connector.dialect
    c = exp.column(col.display_name or column)
    sql = exp.select(c).distinct().from_(exp.to_table(t.display_name or table, dialect=dialect)).where(c.is_(exp.null()).not_())         .order_by(c).limit(500).sql(dialect=dialect)
    payload = _dataset_payload(sql, role)  # validator: izinli şema + PII kontrolü burada da geçerli
    values = [r[0] for r in payload.get("rows", [])]
    _options_cache[f"{role}|{key}"] = (time.time(), values)
    return values


def _dashboard_data(s, selections: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """selections: [{key: 'şema.tablo.kolon', values: [...], exclude: [dataset_id, ...]}]"""
    eng = _engine()
    out: dict[str, Any] = {}
    applied: dict[str, list[str]] = {}
    for did, d in _all_datasets(s).items():
        sql = d.sql
        active = [ModelFilter(*sel["key"].rsplit(".", 1), list(sel["values"]))
                  for sel in selections or [] if sel.get("values") and did not in (sel.get("exclude") or [])
                  and "." in sel.get("key", "")]
        if active:
            try:
                sql, applied[did] = eng.apply(sql, active)
            except Exception as e:  # noqa: BLE001 — filtre uygulanamazsa filtresiz göster
                log.warning("Filtre uygulanamadı (%s): %s", did, e)
                applied[did] = []
        out[did] = _dataset_payload(sql, s.user_role)
    return {"datasets": out, "applied": applied}


@app.get("/api/sessions/{sid}/dashboard-data")
def dashboard_data(sid: str) -> dict[str, Any]:
    return _dashboard_data(_session(sid))


class SelectionIn(BaseModel):
    key: str
    values: list[Any]
    exclude: list[str] | None = None


class DashboardQuery(BaseModel):
    selections: list[SelectionIn] = []


@app.post("/api/sessions/{sid}/dashboard-data")
def dashboard_data_filtered(sid: str, body: DashboardQuery) -> dict[str, Any]:
    """Model filtreleriyle dashboard verisi: seçimler ilişkiler üzerinden her dataset'e yayılır."""
    return _dashboard_data(_session(sid), [sel.model_dump() for sel in body.selections])


@app.get("/api/sessions/{sid}/filters")
def dashboard_filters(sid: str) -> dict[str, Any]:
    """Dilimleyici tanımları + seçenekleri ve dataset alanlarının model kökenleri."""
    s = _session(sid)
    bindings = _bindings(s)
    defs = _filter_defs(s, bindings)
    for f in defs:
        f["options"] = _filter_options(f["key"], s.user_role) if f["key"] else []
    return {"filters": defs, "bindings": bindings}


@app.get("/api/sessions/{sid}/export/html")
def export_html(sid: str) -> HTMLResponse:
    s = _session(sid)
    if not s.spec:
        raise HTTPException(409, "Henüz dashboard yok.")
    viewer = get_settings().viewer_html
    if not viewer.exists():
        raise HTTPException(503, "Viewer derlenmemiş: frontend klasöründe `npm run build:viewer` çalıştırın.")
    data = _dashboard_data(s)
    data["bindings"] = _bindings(s)
    data["filter_keys"] = {f["id"]: f["key"] for f in _filter_defs(s, data["bindings"])}
    payload = json.dumps({"spec": s.spec.model_dump(exclude_none=True), "data": data},
                         ensure_ascii=False, default=str).replace("<", "\\u003c")
    html = viewer.read_text(encoding="utf-8").replace("__REPORT_JSON__", payload, 1)
    title = s.spec.title or "dashboard"
    ascii_name = "".join(c if c.isascii() and c.isalnum() else "_" for c in title)[:60] or "dashboard"
    return HTMLResponse(html, headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}.html\"; "
                                                              f"filename*=UTF-8''{quote(title[:60])}.html"})


# --------------------------------------------------------------------------- sözlük
# --------------------------------------------------------------------------- dataset → onaylı view
class ViewIn(BaseModel):
    name: str | None = None      # view adı (şemasız); boşsa v_<dataset_id>


def _find_dataset(s, did: str):
    ds = next((d for d in s.datasets if d.id == did), None) or (next((d for d in s.spec.datasets if d.id == did), None) if s.spec else None)
    if not ds:
        raise HTTPException(404, f"Dataset bulunamadı: {did}")
    return ds


@app.post("/api/sessions/{sid}/datasets/{did}/view-script")
def dataset_view_script(sid: str, did: str, body: ViewIn) -> dict[str, Any]:
    """Dataset için inceleme amaçlı CREATE OR ALTER VIEW scripti üretir (çalıştırmaz)."""
    s = _session(sid)
    ds = _find_dataset(s, did)
    settings = get_settings()
    name = (body.name or safe_view_name(did)).strip()
    source_sql = ds.original_sql or ds.sql
    v = state.services.validator.validate(source_sql, state.services.policy(s.user_role))
    if not v.ok:
        raise HTTPException(422, "; ".join(v.errors))
    vs = build_view_script(schema=settings.view_schema, name=name, dataset_id=did, description=ds.description,
                           validated_sql=v.sql, columns=[f.name for f in ds.fields], session_title=s.title)
    if vs.errors:
        return JSONResponse({"detail": vs.errors}, status_code=422)
    settings.view_scripts_dir.mkdir(parents=True, exist_ok=True)
    path = settings.view_scripts_dir / f"{settings.view_schema}.{name}.sql"
    path.write_text(vs.script, encoding="utf-8")
    exists = view_columns(state.services.connector, settings.view_schema, name) is not None
    return {"view": vs.name, "script": vs.script, "file": str(path), "exists": exists}


@app.post("/api/sessions/{sid}/datasets/{did}/use-view")
def dataset_use_view(sid: str, did: str, body: ViewIn) -> Any:
    """View oluşturulduysa: doğrular, sözlüğe ekler, dataset'i view'dan okuyacak şekilde değiştirir."""
    settings = get_settings()
    name = (body.name or safe_view_name(did)).strip()
    with state.store.lock(sid):
        s = _session(sid)
        ds = _find_dataset(s, did)
        cols = view_columns(state.services.connector, settings.view_schema, name)
        if cols is None:
            raise HTTPException(409, f"{settings.view_schema}.{name} veritabanında bulunamadı. Scripti çalıştırdınız mı?")
        want = [f.name for f in ds.fields]
        missing = [c for c in want if c.lower() not in {x.lower() for x in cols}]
        if missing:
            raise HTTPException(409, f"View kolonları dataset ile uyuşmuyor; eksik: {missing}. Scripti yeniden üretip çalıştırın.")
        original = ds.original_sql or ds.sql
        eng = _engine()
        lineage = eng.lineage(original)
        entry = {"name": f"{settings.view_schema}.{name}", "business_name": ds.description or did,
                 "description": f"'{s.title}' raporunun '{did}' dataset'i (onaylı view).", "dataset_id": did,
                 "session_id": sid, "original_sql": original, "created_at": now_iso(),
                 "columns": [{"name": f.name, "label": f.label, "type": f.type, "format": f.format,
                              "lineage": lineage.get(f.name)} for f in ds.fields]}
        ViewRegistry(settings.views_registry).upsert(entry)
        state.services.dictionary.add_view(entry)
        # hangi filtreler view'a geçince artık uygulanamıyor?
        filters = [f for f in _filter_defs(s) if f["key"]]
        new_sql = select_from_view(settings.view_schema, name, want)
        def applied(sql: str) -> set[str]:
            try:
                return set(eng.apply(sql, [ModelFilter(*f["key"].rsplit(".", 1), ["__probe__"]) for f in filters])[1])
            except Exception:  # noqa: BLE001
                return set()
        lost = [f["label"] for f in filters if f["key"] in applied(original) - applied(new_sql)]
        for d in [*s.datasets, *(s.spec.datasets if s.spec else [])]:
            if d.id == did:
                d.original_sql, d.sql, d.view = original, new_sql, entry["name"]
        s.spec_version += 1
        s.add(TranscriptItem(role="system", content=f"'{did}' dataset'i artık onaylı view'dan okunuyor: {entry['name']}"
                             + (f" (bu görsele artık uygulanamayan filtreler: {', '.join(lost)})" if lost else "")))
        state.store.save(s)
        return {"session": s.public(), "view": entry["name"], "lost_filters": lost}


@app.get("/api/views")
def list_views() -> list[dict[str, Any]]:
    return ViewRegistry(get_settings().views_registry).all()


@app.get("/api/dictionary/search")
def dictionary_search(q: str = "") -> list[dict[str, Any]]:
    return state.services.dictionary.search(q, 10) if q.strip() else []


@app.get("/api/dictionary/model")
def dictionary_model(session: str | None = None) -> dict[str, Any]:
    """İlişkisel model; session verilirse o rapordaki dataset'lerin kullandığı tablolar da işaretlenir."""
    model = state.services.dictionary.model()
    used: dict[str, list[str]] = {}
    if session:
        try:
            s = state.store.get(session)
        except KeyError:
            raise HTTPException(404, "Oturum bulunamadı")
        datasets = {d.id: d for d in s.datasets}
        if s.spec:
            datasets.update({d.id: d for d in s.spec.datasets})
        pol = state.services.policy(s.user_role)
        for d in datasets.values():
            for t in state.services.validator.validate(d.sql, pol).tables:
                used.setdefault(t, []).append(d.id)
    model["used_tables"] = used
    model["dialect"] = state.services.connector.dialect
    return model


@app.post("/api/dictionary/reload")
def dictionary_reload() -> dict[str, Any]:
    state.services.dictionary.load()
    return {"ok": True, "tables": len(state.services.dictionary.tables)}


@app.exception_handler(ValidationError)
def _validation_handler(_: Request, exc: ValidationError) -> JSONResponse:
    return JSONResponse({"detail": [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]}, status_code=422)
