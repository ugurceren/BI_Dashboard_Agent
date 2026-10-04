"""FastAPI uygulaması. Çalıştırma:  uvicorn app.main:app --port 8000 --reload"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

# Kurum içi HTTPS (LLM geçidi vb.): Python kendi sertifika listesini değil Windows sertifika deposunu kullansın;
# böylece kurumun kendi sertifika otoritesiyle imzalanmış adresler tarayıcıdaki gibi tanınır. Kapatmak: BI_SYSTEM_CERTS=0
if os.environ.get("BI_SYSTEM_CERTS", "1") != "0":
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001 — truststore yoksa Python'un kendi listesiyle devam
        pass

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ValidationError
from sqlglot import exp

from app.config import BACKEND_DIR, get_settings, load_toml
from app.data.connector import QueryError, create_connector
from app.data.model_filters import ModelFilter, ModelFilterEngine
from app.data.views import ViewRegistry, build_view_script, safe_view_name, select_from_view, view_columns
from app.data.validator import RolePolicy, SqlValidator
from app.dictionary.repository import DataDictionary
from app.identity import Identity, current_identity
from app.harness.agent import Agent, Audit, Event
from app.harness.session import PHASES, Requirements, SessionStore, TranscriptItem, now_iso
from app.harness.tools import Services, ToolContext, _build_dataset, _short_db_error, _validate_spec
from app.llm.gateway import LLMGateway


def conns_llm_settings(settings):
    from app.data.connections import llm_settings
    return llm_settings(settings)
from app.spec.models import DatasetField, ReportSpec

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bi-agent")



class AppState:
    services: Services
    gateway: LLMGateway
    store: SessionStore
    agent: Agent
    startup_error: str | None = None


class _BrokenConnector:
    """Bağlantı cümlesi kurulamadığında yer tutucu: her sorguda anlaşılır hata verir."""
    dialect = "tsql"

    def __init__(self, error: str):
        self.error = error

    def execute(self, sql: str, max_rows: int):
        raise QueryError(self.error)

    def ping(self) -> None:
        raise QueryError(self.error)


state = AppState()


def build_services() -> Services:
    """Servisler. Veritabanı / sözlük erişilemezse uygulama yine açılır (boş sözlükle); hata state.startup_error'da
    tutulur ve arayüzde gösterilir — kullanıcı Bağlantı Ayarları'ndan düzeltebilir."""
    settings = get_settings()
    state.startup_error = None
    try:
        connector = create_connector(settings)
    except Exception as e:  # noqa: BLE001
        log.error("Veri bağlantısı kurulamadı: %s", e)
        state.startup_error = f"Veri bağlantısı: {e}"
        connector = _BrokenConnector(str(e))
    dictionary = DataDictionary(settings, connector)
    try:
        dictionary.load()
    except Exception as e:  # noqa: BLE001
        from app.data.odbc import friendly_error
        msg = friendly_error(str(e))
        log.error("Veri sözlüğü yüklenemedi: %s", msg)
        state.startup_error = (state.startup_error + " | " if state.startup_error else "") + f"Veri sözlüğü: {msg}"
        # sözlük olmasa da veritabanındaki yetkili tablo / view'lar görünsün ve sorgulanabilsin
        try:
            dictionary._merge_catalog()
        except Exception as ce:  # noqa: BLE001
            log.warning("Veritabanı kataloğu da okunamadı: %s", ce)
    policy_cfg = load_toml(settings.policy_config)
    policies = {name: RolePolicy(name=name, **cfg) for name, cfg in policy_cfg.get("roles", {}).items()}
    validator = SqlValidator(dictionary, connector.dialect, policy_cfg.get("sql", {}).get("denied_functions", []))
    return Services(settings, dictionary, connector, validator, policies)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    state.services = build_services()
    state.gateway = LLMGateway(conns_llm_settings(settings))
    state.store = SessionStore(settings.sessions_dir)
    removed = state.store.dedupe_titles()
    if removed:
        log.info("Aynı başlıklı %d eski rapor oturumu kaldırıldı (aynı isimle tek rapor).", removed)
    state.agent = Agent(state.gateway, state.services, state.store)
    log.info("Sözlük: %d tablo, %d metrik | LLM: %s @ %s | vision: %s", len(state.services.dictionary.tables),
             len(state.services.dictionary.metrics), state.gateway.s.llm_model, state.gateway.s.llm_base_url,
             state.gateway.s.vision_model or "-")
    yield


app = FastAPI(title="BI Lens", lifespan=lifespan)
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
    dic = {"tables": len(state.services.dictionary.tables)}
    if state.startup_error and "sözlüğü" in state.startup_error:
        dic["error"] = state.startup_error.split("Veri sözlüğü: ", 1)[-1][:300]
    if data["ok"] is False:
        from app.data.odbc import friendly_error
        data["error"] = friendly_error(data.get("error", ""))[:400]
    return {"ok": llm["reachable"] and data["ok"], "llm": llm,
            "vision": {"configured": bool(state.gateway.s.vision_model), "model": state.gateway.s.vision_model}, "data": data,
            "dictionary": dic}


# --------------------------------------------------------------------------- oturumlar
@app.get("/api/sessions")
def list_sessions() -> list[dict[str, Any]]:
    items = state.store.list()
    kinds = state.services.dictionary._table_kinds()
    for it in items:
        it["domains"] = _report_domains(it.get("source_tables") or [], kinds)
    return items


def _report_domains(tables: list[str], kinds: dict[str, str]) -> list[str]:
    """Raporun iş alanı (domain): kullandığı tabloların sözlükteki konu alanı.
    Olgu (fact) tablolarının alanı önce gelir; onaylı view'lar kaynak tablolarına çözülür;
    'Ortak' (tarih, para birimi…) yalnızca başka alan yoksa gösterilir."""
    repo = state.services.dictionary
    order = {"fact": 0, "bridge": 1, "dimension": 2}
    ranked: list[tuple[int, str]] = []
    for name in tables:
        t = repo.tables.get(name)
        if t is None:
            continue
        if kinds.get(name) == "view":  # kolon soy ağacındaki kaynak tabloların alanı
            src = {v.rsplit(".", 1)[0] for k, v in repo.view_lineage.items() if k.startswith(name + ".")}
            ranked += [(order.get(kinds.get(x, ""), 2), repo.tables[x].subject_area) for x in src if x in repo.tables]
        elif t.subject_area:
            ranked.append((order.get(kinds.get(name, ""), 2), t.subject_area.strip()))
    out: list[str] = []
    for _, a in sorted(ranked, key=lambda x: x[0]):
        if a and a not in out:
            out.append(a)
    main = [a for a in out if a.lower() != "ortak"]
    return main or out


def _me(request: Request) -> Identity:
    return current_identity(get_settings(), dict(request.headers))


@app.post("/api/sessions")
def create_session(request: Request) -> dict[str, Any]:
    me = _me(request)
    return state.store.create(me.role, owner=me.username, owner_name=me.display_name).public()


@app.get("/api/me")
def me(request: Request) -> dict[str, Any]:
    """Bağlanan kullanıcı (Windows oturumu / LDAP) ve rol politikası."""
    ident = _me(request)
    pol = state.services.policy(ident.role)
    return {**ident.public(), "policy": {"allowed_schemas": pol.allowed_schemas, "denied_tables": pol.denied_tables,
                                          "allow_pii": pol.allow_pii, "max_rows": pol.max_rows}}


def _object_access(pol: RolePolicy, t) -> tuple[bool, str | None]:
    """Nesne erişimi: önce rol politikası (yasaklı şema / tablo), sonra veritabanının kendi yetkisi (SELECT)."""
    reason = pol.denial_reason(t.name)
    if reason:
        return False, reason
    if t.can_select is False:
        return False, "veritabanında SELECT yetkiniz yok"
    if t.in_db is False:
        return False, "sözlükte var, veritabanında bulunamadı"
    return True, None


@app.get("/api/me/access")
def my_access(request: Request) -> dict[str, Any]:
    """Kullanıcının yetkili olduğu nesneler — tek liste: tablo / view / stored procedure / dataset;
    her biri domain (konu alanı) ve erişim durumuyla (Veri Erişimim sayfası: domain ya da nesne tipine göre gruplanır)."""
    from app.harness.session import _source_tables
    ident = _me(request)
    pol = state.services.policy(ident.role)
    dd = state.services.dictionary
    kinds = dd._table_kinds()
    registry = {str(v.get("name", "")).lower(): v for v in ViewRegistry(get_settings().views_registry).all()}
    objects: list[dict[str, Any]] = []
    for t in dd.tables.values():
        ok, reason = _object_access(pol, t)
        pii = [c.display_name or c.name for c in t.columns if c.is_pii]
        is_view = dd.is_view(t.name, kinds)   # tablo / view ayrımı veritabanı kataloğundan
        area = t.subject_area
        extra: dict[str, Any] = {}
        if is_view:
            reg = registry.get(t.name, {})
            src = _source_tables([{"sql": reg.get("original_sql")}]) if reg.get("original_sql") else []
            area = ((_report_domains(src, kinds) if src else []) or _report_domains([t.name], kinds) or [area])[0]
            extra = {"report_id": reg.get("session_id"), "dataset_id": reg.get("dataset_id"), "created_at": reg.get("created_at"),
                     "tables": src}
        objects.append({
            "type": "view" if is_view else "table", "id": t.name, "name": t.display_name or t.name,
            "business_name": t.business_name, "description": t.description, "subject_area": area or "Diğer",
            "row_count": t.row_count, "column_count": len(t.columns), "pii_columns": pii,
            "pii_blocked": bool(pii) and not pol.allow_pii, "accessible": ok, "reason": reason,
            "documented": t.documented, **extra,
        })
    for sp in _procedures(pol, kinds):  # yalnız EXECUTE yetkisi olanlar listelenir
        objects.append({"type": "procedure", "id": sp["id"], "name": sp["name"], "business_name": "", "description": sp["description"],
                        "subject_area": sp["subject_area"], "parameters": sp["parameters"], "tables": sp["tables"],
                        "accessible": True, "reason": "Salt-okunur ekranlarda çalıştırılamaz (yalnız listeleme)"})
    for item in state.store.list():
        try:
            s = state.store.get(item["id"])
        except KeyError:
            continue
        for d in s.datasets:
            v = state.services.validator.validate(d.sql, pol)
            doms = _report_domains(_source_tables([d.model_dump()]), kinds)
            objects.append({"type": "dataset", "id": f"{s.id}.{d.id}", "name": d.id, "business_name": "", "description": d.description,
                            "subject_area": doms[0] if doms else "Diğer", "report_id": s.id, "report_title": s.title,
                            "fields": len(d.fields), "view": d.view, "tables": v.tables, "accessible": v.ok,
                            "reason": None if v.ok else "; ".join(v.errors)[:200]})
    objects.sort(key=lambda o: (o["subject_area"], o["name"].lower()))
    return {"user": ident.public(), "role": ident.role,
            "policy": {"allowed_schemas": pol.allowed_schemas, "denied_tables": pol.denied_tables,
                       "allow_pii": pol.allow_pii, "max_rows": pol.max_rows},
            "catalog": {"ok": dd.catalog_error is None, "error": dd.catalog_error,
                        "undocumented": sum(1 for t in dd.tables.values() if not t.documented),
                        "missing": dd.missing_in_db[:30], "missing_count": len(dd.missing_in_db),
                        "renamed_count": len(dd.name_changes)},
            "objects": objects}



# --------------------------------------------------------------------------- sorgu çalıştır (salt-okunur konsol)
QUERY_MAX_ROWS = 1000


@app.get("/api/query/schema")
def query_schema(request: Request) -> dict[str, Any]:
    """Sorgu ekranı için yetkili nesneler (ağaç + otomatik tamamlama): tablolar, onaylı view'lar, kolonlar, rapor dataset'leri."""
    ident = _me(request)
    pol = state.services.policy(ident.role)
    dd = state.services.dictionary
    kinds = dd._table_kinds()
    from app.harness.session import _source_tables
    view_sources = {str(v.get("name", "")).lower(): _source_tables([{"sql": v.get("original_sql")}])
                    for v in ViewRegistry(get_settings().views_registry).all() if v.get("original_sql")}
    objects = []
    for t in dd.tables.values():
        if not _object_access(pol, t)[0]:  # yalnız yetkili nesneler (politika + veritabanı SELECT yetkisi)
            continue
        kind = "view" if dd.is_view(t.name, kinds) else (kinds.get(t.name) if kinds.get(t.name) != "view" else None) or "table"
        # view'ın domain'i: kaynak SQL'indeki tabloların konu alanı (sözlükteki "Onaylı rapor view'ları" değil)
        area = t.subject_area
        if kind == "view":
            src = view_sources.get(t.name)
            area = ((_report_domains(src, kinds) if src else []) or _report_domains([t.name], kinds) or [t.subject_area])[0]
        objects.append({
            "id": t.name, "name": t.display_name or t.name, "kind": kind,
            "business_name": t.business_name, "description": t.description, "subject_area": area or "Diğer",
            "row_count": t.row_count, "documented": t.documented,
            "columns": [{"name": c.display_name or c.name, "type": c.data_type, "business_name": c.business_name,
                         "description": c.description, "pii": c.is_pii, "blocked": c.is_pii and not pol.allow_pii}
                        for c in t.columns],
        })
    objects.sort(key=lambda o: (o["kind"] == "view", o["subject_area"], o["name"].lower()))
    datasets = []
    for item in state.store.list():
        try:
            s = state.store.get(item["id"])
        except KeyError:
            continue
        for d in s.datasets:
            if state.services.validator.validate(d.sql, pol).ok:
                doms = _report_domains(_source_tables([d.model_dump()]), kinds)
                datasets.append({"report_id": s.id, "report_title": s.title, "id": d.id,
                                 "description": d.description, "sql": d.sql, "view": d.view,
                                 "subject_area": doms[0] if doms else "Diğer", "domains": doms})
    return {"role": ident.role, "max_rows": min(QUERY_MAX_ROWS, pol.max_rows), "allow_pii": pol.allow_pii,
            "allowed_schemas": pol.allowed_schemas, "objects": objects, "datasets": datasets,
            "procedures": _procedures(pol, kinds)}


def _procedures(pol: RolePolicy, kinds: dict[str, str]) -> list[dict[str, Any]]:
    """EXECUTE yetkisi olan stored procedure'ler (yalnız listeleme; salt-okunur ekranda EXEC engellidir).
    Domain: SP'nin kullandığı sözlük tablolarının konu alanı (sys.sql_expression_dependencies)."""
    con = state.services.connector
    if getattr(con, "dialect", "") != "tsql":
        return []
    try:
        procs = con.execute(
            "SELECT p.object_id, SCHEMA_NAME(p.schema_id), p.name, CAST(ep.value AS nvarchar(400)) "
            "FROM sys.procedures p LEFT JOIN sys.extended_properties ep ON ep.major_id = p.object_id AND ep.minor_id = 0 "
            "AND ep.class = 1 AND ep.name = 'MS_Description' WHERE p.is_ms_shipped = 0 "
            "AND HAS_PERMS_BY_NAME(QUOTENAME(SCHEMA_NAME(p.schema_id)) + '.' + QUOTENAME(p.name), 'OBJECT', 'EXECUTE') = 1", 5_000).rows
        if not procs:
            return []
        params = con.execute("SELECT pr.object_id, pr.name, TYPE_NAME(pr.user_type_id) FROM sys.parameters pr "
                             "JOIN sys.procedures p ON p.object_id = pr.object_id WHERE p.is_ms_shipped = 0 "
                             "ORDER BY pr.object_id, pr.parameter_id", 50_000).rows
        deps = con.execute("SELECT d.referencing_id, COALESCE(d.referenced_schema_name, 'dbo'), d.referenced_entity_name "
                           "FROM sys.sql_expression_dependencies d JOIN sys.procedures p ON p.object_id = d.referencing_id "
                           "WHERE p.is_ms_shipped = 0 AND d.referenced_entity_name IS NOT NULL", 50_000).rows
    except Exception as e:  # noqa: BLE001 — veri kaynağı yoksa SP listesi boş
        log.info("Stored procedure'ler okunamadı: %s", e)
        return []
    by_params: dict[int, list[str]] = {}
    for oid, name, typ in params:
        by_params.setdefault(oid, []).append(f"{name} {typ}")
    by_deps: dict[int, list[str]] = {}
    for oid, sch, ent in deps:
        by_deps.setdefault(oid, []).append(f"{sch}.{ent}".lower())
    out = []
    for oid, sch, name, desc in procs:
        full = f"{sch}.{name}"
        if pol.denial_reason(full):
            continue
        doms = _report_domains(sorted(set(by_deps.get(oid, []))), kinds)
        out.append({"id": full.lower(), "name": full, "description": desc or "", "parameters": by_params.get(oid, []),
                    "tables": sorted(set(by_deps.get(oid, []))), "subject_area": doms[0] if doms else "Diğer"})
    return sorted(out, key=lambda x: x["name"].lower())


class QueryIn(BaseModel):
    sql: str


@app.post("/api/query")
def run_query(body: QueryIn, request: Request) -> dict[str, Any]:
    """Kullanıcının yazdığı sorguyu rol yetkisi dahilinde, salt-okunur çalıştırır (en çok 1000 satır).
    Doğrulayıcı: tek SELECT, yetkili şema/tablo, PII, yasaklı fonksiyon; bağlantı readonly ve rollback."""
    ident = _me(request)
    pol = state.services.policy(ident.role)
    sql = (body.sql or "").strip()
    if len(sql) > 20000:
        raise HTTPException(413, "Sorgu çok uzun (en çok 20.000 karakter).")
    v = state.services.validator.validate(sql, pol, strict_joins=False, autofix=False)
    audit = Audit(get_settings().audit_log)
    limit = min(QUERY_MAX_ROWS, pol.max_rows)
    if not v.ok:
        audit.write(event="query_console", user=ident.username, role=ident.role, sql=sql[:4000], ok=False, errors=v.errors)
        return {"ok": False, "errors": v.errors, "warnings": v.warnings}
    try:
        res = state.services.connector.execute(v.sql, limit)
    except QueryError as e:
        msg = _short_db_error(str(e))
        audit.write(event="query_console", user=ident.username, role=ident.role, sql=sql[:4000], ok=False, errors=[msg])
        return {"ok": False, "errors": [f"Veritabanı hatası: {msg}"], "warnings": v.warnings}
    audit.write(event="query_console", user=ident.username, role=ident.role, sql=sql[:4000], ok=True,
                rows=len(res.rows), truncated=res.truncated, elapsed_ms=res.elapsed_ms, tables=v.tables)
    return {"ok": True, "columns": res.columns, "types": res.types, "rows": res.rows, "truncated": res.truncated,
            "row_limit": limit, "elapsed_ms": res.elapsed_ms, "tables": v.tables, "warnings": v.warnings}


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
        s.title_locked = True  # agent artık adı değiştirmez
        if s.spec:
            s.spec.title = title
            s.spec_version += 1
        if s.requirements:
            s.requirements.report_title = title
        state.store.save(s)   # aynı isimli diğer rapor (overwrite onaylandıysa) burada silinir
        return s.public()


class StatusIn(BaseModel):
    status: str


@app.put("/api/sessions/{sid}/status")
def set_status(sid: str, body: StatusIn) -> dict[str, Any]:
    """Rapor yaşam döngüsü: idea (fikir) → design (tasarım) → test → live (canlıda)."""
    if body.status not in ("idea", "design", "test", "live"):
        raise HTTPException(400, "Geçersiz statü. Seçenekler: idea, design, test, live")
    with state.store.lock(sid):
        s = _session(sid)
        s.status = body.status  # type: ignore[assignment]
        s.add(TranscriptItem(role="system", content=f"Rapor statüsü değişti: {body.status}"))
        state.store.save(s)
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
        forward = PHASES.index(body.phase) > PHASES.index(s.phase)
        s.set_phase(body.phase)  # type: ignore[arg-type]

        if forward:
            s.add(TranscriptItem(role="system", content=f"Kullanıcı '{body.phase}' fazına geçti (kayıtlı içerikle)"))
            s.llm_messages.append({"role": "user", "content": "[HARNESS] Kullanıcı kayıtlı içerikle bu faza ileri geçti. "
                                   "Kayıtlı dataset'ler/dashboard ile devam et; durumu kısaca özetle ve ne yapmak istediğini sor."})
        else:
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
            # onaylı view'a bağlı dataset: view toplulaştırılmış olduğundan (ör. yalnız bölge) filtre ona ulaşamayabilir.
            # Bu durumda view'ın kaynak SQL'i filtrelenerek çalıştırılır (kolonlar aynı; filtresizken view kullanılır).
            if d.view and d.original_sql and len(applied.get(did, [])) < len({f.key for f in active}):
                try:
                    alt_sql, alt_applied = eng.apply(d.original_sql, active)
                    if len(alt_applied) > len(applied.get(did, [])):
                        sql, applied[did] = alt_sql, alt_applied
                except Exception as e:  # noqa: BLE001
                    log.warning("View kaynağına filtre uygulanamadı (%s): %s", did, e)
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


# --------------------------------------------------------------------------- bağlantı ayarları
from app.data import connections as conns  # noqa: E402
from app.data.odbc import friendly_error  # noqa: E402


def _require_settings_access(request: Request) -> Identity:
    """Bağlantı ayarları: admin rolü ya da uygulamanın çalıştığı bilgisayarın kendisi (ağdan gelen kullanıcı değil)."""
    ident = _me(request)
    fwd = [x.strip() for x in (request.headers.get("x-forwarded-for") or "").split(",") if x.strip()]
    local = (request.client.host if request.client else "") in ("127.0.0.1", "::1", "localhost", "testclient") \
        and all(x in ("127.0.0.1", "::1", "::ffff:127.0.0.1") for x in fwd)
    if ident.role != "admin" and not local:
        raise HTTPException(403, "Bağlantı ayarlarını yalnızca admin rolü ya da sunucunun çalıştığı bilgisayar değiştirebilir.")
    return ident


class ConnIn(BaseModel):
    server: str = "localhost"
    database: str = ""
    auth: str = "windows"            # windows | sql
    username: str = ""
    password: str | None = None      # None → kayıtlı şifre korunur
    encrypt: bool = True
    trust_server_certificate: bool = True
    same_as_data: bool = True        # yalnız sözlük için
    sources: dict[str, list[str]] | None = None   # yalnız sözlük: rol → tablolar / sayfalar
    kind: str = "sqlserver"          # yalnız sözlük: sqlserver | mysql | excel
    port: int | None = None          # MySQL (varsayılan 3306)
    excel_path: str = ""             # Excel dosyası (yüklenen ya da ağ yolu)
    mappings: dict[str, dict[str, str]] | None = None   # yalnız sözlük: kaynak → alan → başlık ("" = kullanma)


class ConnectionsIn(BaseModel):
    data: ConnIn
    dictionary: ConnIn


class ConnTestIn(BaseModel):
    target: str = "data"             # data | dictionary
    data: ConnIn
    dictionary: ConnIn | None = None


def _fields(c: ConnIn, saved: dict[str, Any] | None) -> dict[str, Any]:
    d = c.model_dump()
    if d.get("kind") == "mysql":     # MySQL her zaman kullanıcı adı / şifre ile
        d["auth"] = "sql"
    if d["auth"] != "sql":
        d.update(username="", password="")
    elif d["password"] is None:      # boş bırakıldı: kayıtlı şifreyi kullan
        d["password"] = conns.unprotect((saved or {}).get("password_enc") or "") if saved else ""
    d["server"] = (d["server"] or "localhost").strip()
    d["database"] = (d["database"] or "").strip()
    return d


def _resolved(body: ConnTestIn | ConnectionsIn) -> tuple[dict[str, Any], dict[str, Any]]:
    saved = conns.load_connections() or {}
    data = _fields(body.data, saved.get("data"))
    dic_in = body.dictionary or ConnIn(database=conns.DEFAULT_DICTIONARY_DB)
    from app.dictionary.sources import KINDS, ROLES, default_sources
    kind = dic_in.kind if dic_in.kind in KINDS else "sqlserver"
    dic = _fields(dic_in, saved.get("dictionary"))
    if kind == "sqlserver" and dic_in.same_as_data:
        dic = {**data, "database": dic["database"] or conns.DEFAULT_DICTIONARY_DB, "same_as_data": True}
    else:
        dic["same_as_data"] = False
    dic["kind"] = kind
    if kind == "mysql":
        dic["port"] = dic_in.port or 3306
    if kind == "excel":
        dic["excel_path"] = (dic_in.excel_path or "").strip().strip('"')
    src = dic_in.sources or default_sources(kind)
    dic["sources"] = {r: [n.strip() for n in (src.get(r) or []) if n and n.strip()] for r in ROLES}
    used = {n.casefold() for lst in dic["sources"].values() for n in lst}
    dic["mappings"] = {k: {f: str(h or "") for f, h in (v or {}).items()}
                       for k, v in (dic_in.mappings or {}).items() if k.casefold() in used}
    for k in ("sources", "kind", "port", "excel_path", "mappings"):
        data.pop(k, None)
    return data, dic


def _probe(fields: dict[str, Any], database: str | None = None) -> dict[str, Any]:
    from app.data.connector import SqlServerConnector
    odbc = conns.build_odbc(fields, database)
    con = SqlServerConnector(odbc, 15)
    r = con.execute("SELECT @@SERVERNAME, DB_NAME(), CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(64)), "
                    "CAST(SERVERPROPERTY('Edition') AS nvarchar(128)), SUSER_SNAME()", 1)
    srv, db, ver, ed, login = r.rows[0]
    return {"server_name": srv, "database": db, "version": f"SQL Server {ver}", "edition": ed, "login": login,
            "driver": odbc.split(";")[0].replace("DRIVER=", "").strip("{}")}


@app.post("/api/settings/dictionary/upload")
async def upload_dictionary_excel(request: Request, filename: str = "sozluk.xlsx") -> dict[str, Any]:
    """Excel sözlük dosyasını yükler (gövde: dosyanın kendisi). Sayfalar ve önerilen roller döner."""
    _require_settings_access(request)
    import re as _re
    ext = Path(filename).suffix.lower()
    if ext not in (".xlsx", ".xlsm"):
        raise HTTPException(400, "Yalnızca .xlsx / .xlsm dosyaları yüklenebilir.")
    body = await request.body()
    if not body or len(body) > 30 * 1024 * 1024:
        raise HTTPException(400, "Dosya boş ya da 30 MB'tan büyük.")
    safe = _re.sub(r"[^\w.\-]+", "_", Path(filename).stem)[:60] or "sozluk"
    conns.DICTIONARY_FILES_DIR.mkdir(parents=True, exist_ok=True)
    path = conns.DICTIONARY_FILES_DIR / f"{safe}{ext}"
    path.write_bytes(body)
    from app.dictionary.sources import ExcelReader, _norm_key, suggest_role
    try:
        sheets = ExcelReader(path).list_tables()
    except Exception as e:  # noqa: BLE001
        path.unlink(missing_ok=True)
        raise HTTPException(400, f"Excel dosyası okunamadı: {e}")
    return {"ok": True, "path": str(path),
            "tables": [{"name": n, "columns": c, "role": suggest_role({_norm_key(x) for x in c})} for n, c in sheets.items()]}


@app.get("/api/settings/dictionary/template.xlsx")
def dictionary_template(request: Request, layout: str = "multi") -> Response:
    """Şu an yüklü sözlüğü Excel olarak indirir; düzenleyip Excel kaynağı olarak geri yüklenebilir.
    layout=multi: Tablolar / Kolonlar / İlişkiler / Metrikler sayfaları · layout=single: tek "Sözlük" sayfası
    (her satır bir kolon, tablo bilgileri table_* kolonlarında; ilişkiler foreign key / kolon adlarından çıkarılır)."""
    _require_settings_access(request)
    import io

    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from app.dictionary.sources import DEFAULTS, ROLES
    dd = state.services.dictionary
    _disp = lambda t: (dd.tables[t].display_name or t) if t in dd.tables else t  # noqa: E731 — özgün yazım (dbo.FactX)
    rows: dict[str, list[list[Any]]] = {
        "tables": [[t.display_name or t.name, t.business_name, t.description, t.subject_area, t.grain, t.row_count, t.table_type]
                   for t in dd.tables.values() if t.table_type != "view"],
        "columns": [[t.display_name or t.name, c.display_name or c.name, c.business_name, c.description, c.data_type, c.role,
                     c.default_aggregation, ", ".join(c.synonyms), "Evet" if c.is_pii else "", c.sample_values]
                    for t in dd.tables.values() if t.table_type != "view" for c in t.columns],
        # onaylı view'lar (ve onların ilişkileri) sözlüğe uygulamaca eklenir; şablona girmez
        "relationships": [[r.id, _disp(r.from_table), a, _disp(r.to_table), b, r.cardinality, r.role, "Evet" if r.active else "Hayır"]
                          for r in dd.relationships for a, b in r.pairs
                          if not any((dd.tables.get(t) and dd.tables[t].table_type == "view") for t in (r.from_table, r.to_table))],
        "metrics": [[m.name, m.expression_sql, m.business_name, m.description, m.base_table, m.value_format, ", ".join(m.synonyms)]
                    for m in dd.metrics],
    }
    order = {"relationships": ["relationship_id", "from_table", "from_column", "to_table", "to_column", "cardinality", "role", "is_active"]}
    order["columns"] = ["table_name", "column_name", "business_name", "description", "data_type", "column_role",
                        "default_aggregation", "synonyms", "is_pii", "sample_values"]
    order["tables"] = ["table_name", "business_name", "description", "subject_area", "grain", "row_count", "table_type"]
    sheets: list[tuple[str, list[str], list[list[Any]]]]
    if layout == "single":
        tinfo = {r[0].lower(): r for r in rows["tables"]}
        flat = []
        for c in rows["columns"]:
            t = tinfo.get(str(c[0]).lower(), [c[0], None, None, None, None, None, None])
            flat.append([c[0], t[1], t[2], t[3], t[4], t[5], t[6], *c[1:]])
        sheets = [("Sözlük", ["table_name", "table_business_name", "table_description", "subject_area", "grain", "row_count",
                              "table_type", *order["columns"][1:]], flat)]
    else:
        sheets = [(DEFAULTS["excel"][role][0], order[role] if role in order else spec["required"] + spec["optional"], rows[role])
                  for role, spec in ROLES.items()]
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, header, data in sheets:
        ws = wb.create_sheet(title)
        ws.append(header)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="2563EB")
        for r in data:
            ws.append(r)
        ws.freeze_panes = "A2"
        for i, h in enumerate(header, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = max(14, min(40, len(h) + 6))
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="veri_sozlugu{"_tek_sayfa" if layout == "single" else ""}.xlsx"'})


# ---------------------------------------------------------------- dil modeli (LLM) bağlantısı
class VisionIn(BaseModel):
    enabled: bool = True
    same_as_main: bool = True
    base_url: str = ""
    api_key: str | None = None       # None → kayıtlı anahtar korunur
    model: str = ""


class LlmIn(BaseModel):
    base_url: str
    api_key: str | None = None       # None → kayıtlı anahtar korunur
    model: str
    tool_mode: str = "auto"          # auto | native | prompt
    extra_body: dict[str, Any] | None = None
    vision: VisionIn = VisionIn()


class LlmTestIn(BaseModel):
    target: str = "main"             # main | vision
    llm: LlmIn


def _llm_keys(body: LlmIn) -> tuple[str, str]:
    """Formdan gelen anahtar yoksa kayıtlı (connections.json ya da .env) anahtar kullanılır."""
    cur = conns.llm_settings(get_settings())
    main = body.api_key if body.api_key is not None else (cur.llm_api_key or "")
    vis = body.vision.api_key if body.vision.api_key is not None else (cur.vision_api_key or "")
    return main, vis


def _llm_target(body: LlmIn, target: str) -> tuple[str, str, str]:
    main_key, vis_key = _llm_keys(body)
    if target == "vision" and not body.vision.same_as_main:
        return body.vision.base_url.strip(), vis_key, body.vision.model.strip()
    return body.base_url.strip(), main_key, (body.vision.model if target == "vision" else body.model).strip()


@app.post("/api/settings/llm/models")
def llm_models(body: LlmTestIn, request: Request) -> dict[str, Any]:
    """Sunucudaki modeller (OpenAI uyumlu /models)."""
    _require_settings_access(request)
    from openai import OpenAI
    url, key, _ = _llm_target(body.llm, body.target)
    try:
        ms = OpenAI(base_url=url, api_key=key or "EMPTY", timeout=15, max_retries=0).models.list().data
        return {"ok": True, "models": sorted(m.id for m in ms)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": _llm_error(e), "models": []}


_PIXEL = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAEklEQVR4nGP4z8CAFWEXHbQSACj/"
          "P8Hj9zQrAAAAAElFTkSuQmCC")


@app.post("/api/settings/llm/test")
def llm_test(body: LlmTestIn, request: Request) -> dict[str, Any]:
    """Modele gerçek kısa bir istek: ana model → metin + araç çağırma desteği; görsel model → küçük bir görsel."""
    _require_settings_access(request)
    import time as _t

    from openai import OpenAI
    url, key, model = _llm_target(body.llm, body.target)
    if not url or not model:
        return {"ok": False, "error": "API adresi ve model adı gerekli."}
    cli = OpenAI(base_url=url, api_key=key or "EMPTY", timeout=60, max_retries=0)
    extra = {"extra_body": body.llm.extra_body} if body.llm.extra_body else {}
    t0 = _t.perf_counter()
    try:
        if body.target == "vision":
            r = cli.chat.completions.create(model=model, max_tokens=40, temperature=0, messages=[{"role": "user", "content": [
                {"type": "text", "text": "Bu görselde hangi renk var? Tek kelimeyle yanıtla."},
                {"type": "image_url", "image_url": {"url": _PIXEL}}]}], **extra)
            return {"ok": True, "model": model, "elapsed_ms": int((_t.perf_counter() - t0) * 1000),
                    "reply": (r.choices[0].message.content or "").strip()[:120]}
        r = cli.chat.completions.create(model=model, max_tokens=20, temperature=0,
                                        messages=[{"role": "user", "content": "Yalnızca 'Tamam' yaz."}], **extra)
        out = {"ok": True, "model": model, "elapsed_ms": int((_t.perf_counter() - t0) * 1000),
               "reply": (r.choices[0].message.content or "").strip()[:120]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": _llm_error(e)}
    try:  # araç çağırma (agent'ın veri / tasarım araçları için)
        tool = {"type": "function", "function": {"name": "kaydet", "description": "Bir sayıyı kaydeder",
                                                  "parameters": {"type": "object", "properties": {"sayi": {"type": "integer"}},
                                                                 "required": ["sayi"]}}}
        r = cli.chat.completions.create(model=model, max_tokens=200, temperature=0, tools=[tool],
                                        messages=[{"role": "user", "content": "kaydet aracını sayi=7 ile çağır."}], **extra)
        out["tools"] = "native" if r.choices[0].message.tool_calls else "prompt"
    except Exception:  # noqa: BLE001
        out["tools"] = "prompt"
    return out


def _llm_error(e: Exception) -> str:
    import openai
    if isinstance(e, openai.AuthenticationError):
        return "API anahtarı geçersiz ya da eksik (401)."
    if isinstance(e, openai.NotFoundError):
        return "Adres ya da model bulunamadı (404): API adresi genelde .../v1 ile biter; model adını kontrol edin."
    if isinstance(e, openai.APIConnectionError):
        return "Sunucuya bağlanılamadı: adres doğru mu, kurumsal proxy / sertifika engeli var mı?"
    if isinstance(e, openai.APITimeoutError):
        return "Sunucu zaman aşımına uğradı."
    if isinstance(e, openai.APIStatusError):
        return f"Sunucu hatası ({e.status_code}): {str(e)[:200]}"
    return f"{type(e).__name__}: {str(e)[:200]}"


@app.get("/api/settings/llm")
def get_llm(request: Request) -> dict[str, Any]:
    _require_settings_access(request)
    return conns.llm_effective(get_settings())


@app.put("/api/settings/llm")
def save_llm(body: LlmIn, request: Request) -> dict[str, Any]:
    ident = _require_settings_access(request)
    if not body.base_url.strip() or not body.model.strip():
        raise HTTPException(400, "API adresi ve model adı gerekli.")
    if body.tool_mode not in ("auto", "native", "prompt"):
        raise HTTPException(400, "Araç çağırma modu auto / native / prompt olmalı.")
    main_key, vis_key = _llm_keys(body)
    v = body.vision
    conns.save_connections({"llm": {
        "base_url": body.base_url.strip(), "model": body.model.strip(), "tool_mode": body.tool_mode,
        "extra_body": body.extra_body, "api_key_enc": conns.protect(main_key),
        "vision": {"enabled": v.enabled and bool(v.model.strip()), "same_as_main": v.same_as_main, "model": v.model.strip(),
                   "base_url": "" if v.same_as_main else v.base_url.strip(),
                   "api_key_enc": "" if v.same_as_main else conns.protect(vis_key)},
    }})
    _rebuild_gateway()
    log.info("LLM bağlantısı güncellendi (%s): %s @ %s | görsel: %s", ident.username, body.model, body.base_url,
             v.model if v.enabled else "-")
    return {"ok": True, **state.gateway.health()}


@app.delete("/api/settings/llm")
def reset_llm(request: Request) -> dict[str, Any]:
    """Arayüzden kaydedilen LLM ayarını sil: .env'deki LLM_* / VISION_* ayarlarına dön."""
    _require_settings_access(request)
    conns.save_connections({"llm": None})
    _rebuild_gateway()
    return {"ok": True}


def _rebuild_gateway() -> None:
    state.gateway = LLMGateway(conns.llm_settings(get_settings()))
    if getattr(state, "agent", None) is not None:
        state.agent.llm = state.gateway


@app.get("/api/settings/connections/export")
def export_connections(request: Request) -> Response:
    """Geçerli bağlantı ayarlarını JSON olarak indirir (başka bir bilgisayara taşımak için).
    Şifreler dosyaya YAZILMAZ; içe aktarınca şifre yeniden girilir."""
    _require_settings_access(request)
    eff = conns.effective(get_settings())
    drop = {"has_password", "password", "password_enc"}
    clean = lambda d: {k: v for k, v in (d or {}).items() if k not in drop}  # noqa: E731
    dic = clean(eff["dictionary"])
    note = None
    if dic.get("kind") == "excel" and dic.get("excel_path"):
        note = ("Excel sözlük dosyası bu bilgisayardaki yolu gösterir; diğer bilgisayarda dosyayı yeniden yükleyin "
                "ya da ortak bir ağ yolu kullanın.")
    payload = {
        "app": "BI Lens", "type": "connection-settings", "version": 1,
        "exported_at": now_iso(), "exported_by": _me(request).username, "source": eff["source"],
        "data": clean(eff["data"]), "dictionary": dic,
        "llm": {k: v for k, v in conns.llm_effective(get_settings()).items() if k not in ("has_api_key", "source")} | {
            "vision": {k: v for k, v in conns.llm_effective(get_settings())["vision"].items() if k != "has_api_key"}},
        "notes": [n for n in ["Şifreler ve API anahtarları bu dosyada yoktur; içe aktardıktan sonra gerekenleri girin.",
                             note] if n],
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="bi_lens_baglanti_ayarlari_{now_iso()[:10]}.json"'})


@app.get("/api/settings/connections")
def get_connections(request: Request) -> dict[str, Any]:
    _require_settings_access(request)
    eff = conns.effective(get_settings())
    drivers = conns.installed_drivers()
    return {**eff, "drivers": [d for d in drivers if "SQL Server" in d],
            "driver": conns.best_sql_server_driver(drivers), "default_dictionary_db": conns.DEFAULT_DICTIONARY_DB,
            "startup_error": state.startup_error, "file": "backend/config/connections.json"}


@app.post("/api/settings/connections/test")
def test_connection(body: ConnTestIn, request: Request) -> dict[str, Any]:
    _require_settings_access(request)
    data, dic = _resolved(body)
    if body.target != "dictionary":
        try:
            return {"ok": True, **_probe(data)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": friendly_error(str(e))}
    # sözlük: kaynağa bağlan (SQL Server / MySQL / Excel), seçilen tablo / sayfaları gerçekten oku (rol başına satır sayısı)
    from app.dictionary.sources import collect
    if dic["kind"] == "none":
        return {"ok": True, "server_name": "—", "database": "Sözlük kullanılmıyor", "version": "yalnız veritabanı kataloğu",
                "dictionary_counts": {}, "warnings": ["Tablo ve view'lar veritabanından (yetkinize göre), açıklamalar MS_Description'dan, "
                                                      "ilişkiler foreign key'lerden gelir."]}
    try:
        reader, info = conns.open_dictionary_reader(dic)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": friendly_error(str(e))}
    catalog = _data_catalog(data)
    try:
        got = collect(reader, dic["sources"], dic.get("mappings"), catalog.get("known"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, **info, "error": "Bağlantı kuruldu ama sözlük okunamadı: " + friendly_error(str(e))}
    counts = got.counts
    info.update(dictionary_counts=counts, dictionary_tables=counts.get("tables", 0), warnings=got.warnings,
                derived_tables=got.derived_tables, sources_info=got.sources)
    if "relationships" not in got.explicit and got.rows.get("columns"):  # ilişki sözlüğü yok: otomatik kaç ilişki bulunur?
        try:
            n, src = DataDictionary(get_settings(), state.services.connector).preview_auto_relationships(
                got.rows.get("tables") or [], got.rows["columns"])
            counts["relationships"] = n
            info["relationship_source"] = src
        except Exception as e:  # noqa: BLE001
            log.info("Otomatik ilişki önizlemesi yapılamadı: %s", e)
    match = _dictionary_match(data, got, catalog)
    if match:
        info["catalog_match"] = match
    if got.errors:
        return {"ok": False, **info, "error": " ".join(got.errors)}
    return {"ok": True, **info}


def _data_catalog(data: dict[str, Any]) -> dict[str, Any]:
    """Veri kaynağının kataloğu (nesneler + farklı kolon adları) — sözlük testi / kaydı için. Hata olursa {"error"}."""
    if not data.get("database"):
        return {}
    from app.data.connector import SqlServerConnector
    try:
        dd = DataDictionary(get_settings(), SqlServerConnector(data.get("odbc") or conns.build_odbc(data), 15))
        cat = dd._read_catalog(columns=False)
        if cat is None:
            return {"error": dd.catalog_error}
        return {"objects": cat[0], "known": dd.known(cat, dd.column_names())}
    except Exception as e:  # noqa: BLE001
        return {"error": friendly_error(str(e))}


def _dictionary_match(data: dict[str, Any], got: Any, catalog: dict[str, Any]) -> dict[str, Any] | None:
    """Sözlükteki tablo / view adları veri kaynağında hangi nesnelere eşleniyor: bulunanlar, adı düzeltilenler,
    bulunamayanlar ve SELECT yetkisi olmayanlar (Ayarlar sayfasında gösterilir)."""
    names = sorted({str(r["table_name"]) for r in got.rows.get("tables") or [] if r.get("table_name")})
    if not names or not data.get("database"):
        return None
    from app.dictionary.names import NameResolver
    if "objects" not in catalog:
        return {"ok": False, "database": data["database"], "error": catalog.get("error")}
    objs = catalog["objects"]
    res = NameResolver((s, o) for s, o, *_ in objs)
    resolved = {res.resolve(n) for n in names}
    perms = {f"{s}.{o}".lower(): bool(p) for s, o, _t, p, *_ in objs}
    missing = sorted(res.missing.values())
    no_select = sorted(n for n in resolved if perms.get(n.lower()) is False)
    return {"ok": True, "database": data["database"], "total": len(resolved), "found": len(resolved) - len(missing),
            "missing": missing[:30], "missing_count": len(missing), "no_select": no_select[:30], "no_select_count": len(no_select),
            "renamed": [[a, b] for a, b in list(res.changed.items())[:10]], "renamed_count": len(res.changed),
            "db_objects": len(objs)}


@app.post("/api/settings/dictionary/tables")
def dictionary_candidate_tables(body: ConnTestIn, request: Request) -> dict[str, Any]:
    """Sözlük veritabanındaki tablolar + kolonlarına göre önerilen sözlük rolü."""
    _require_settings_access(request)
    _, dic = _resolved(body)
    from app.dictionary.sources import ROLES, _norm_key, suggest_role
    try:
        reader, _ = conns.open_dictionary_reader(dic)
        tables = reader.list_tables()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": friendly_error(str(e)), "tables": []}
    out = [{"name": n, "columns": cols, "role": suggest_role({_norm_key(c) for c in cols})} for n, cols in tables.items()]
    return {"ok": True, "tables": out,
            "roles": {k: {"label": v["label"], "required": v["required"], "optional": v["optional"], "must": v["must"]}
                      for k, v in ROLES.items()}}


@app.post("/api/settings/instances")
def find_instances(request: Request) -> dict[str, Any]:
    _require_settings_access(request)
    local = conns.local_instances()
    net = [x for x in conns.network_instances() if x.lower() not in {y.lower() for y in local}]
    return {"local": local, "network": net}


@app.post("/api/settings/databases")
def list_databases(body: ConnTestIn, request: Request) -> dict[str, Any]:
    _require_settings_access(request)
    data, dic = _resolved(body)
    fields = dic if body.target == "dictionary" else data
    if body.target == "dictionary" and dic["kind"] == "mysql":
        try:
            from app.dictionary.sources import MySQLReader
            r = MySQLReader(dic["server"], dic.get("port") or 3306, dic["username"], dic.get("password") or "", "")
            return {"ok": True, "databases": r.databases()}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": friendly_error(str(e)), "databases": []}
    try:
        from app.data.connector import SqlServerConnector
        r = SqlServerConnector(conns.build_odbc(fields, "master"), 15).execute(
            "SELECT name FROM sys.databases WHERE state = 0 AND HAS_DBACCESS(name) = 1 "
            "AND name NOT IN ('master','tempdb','model','msdb') ORDER BY name", 500)
        return {"ok": True, "databases": [row[0] for row in r.rows]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": friendly_error(str(e)), "databases": []}


@app.put("/api/settings/connections")
def save_connections(body: ConnectionsIn, request: Request) -> dict[str, Any]:
    ident = _require_settings_access(request)
    data, dic = _resolved(body)
    if not data["database"]:
        raise HTTPException(400, "Veri kaynağı için veritabanı seçin.")
    # sözlük tabloları hatalıysa kaydetme: çalışan eski ayar korunsun (sunucuya o an ulaşılamıyorsa kayda izin ver)
    from app.dictionary import sources as dsrc
    try:
        got = None if dic["kind"] == "none" else dsrc.collect(conns.open_dictionary_reader(dic)[0], dic["sources"], dic.get("mappings"),
                                                               _data_catalog(data).get("known"))
    except Exception:  # noqa: BLE001
        got = None
    if got and got.errors:
        raise HTTPException(422, "Kaydedilmedi — sözlük tabloları hatalı: " + " ".join(got.errors))
    _remember_mappings(dic, got)
    store = lambda f: {k: v for k, v in f.items() if k != "password"} | {"password_enc": conns.protect(f.get("password") or "")}  # noqa: E731
    dic_section = ({"kind": "sqlserver", "same_as_data": True, "database": dic["database"], "sources": dic["sources"],
                    "mappings": dic["mappings"]}
                   if dic["kind"] == "sqlserver" and dic.get("same_as_data") else store(dic))
    conns.save_connections({"data": store(data), "dictionary": dic_section})  # llm bölümü korunur
    _rebuild_services()
    log.info("Bağlantı ayarları güncellendi (%s): veri=%s/%s sözlük=%s/%s", ident.username,
             data["server"], data["database"], dic["server"], dic["database"])
    return {"ok": state.startup_error is None, "error": state.startup_error,
            "tables": len(state.services.dictionary.tables)}


# ---------------------------------------------------------------- bölüm bazlı kayıt (veri kaynağı / sözlük ayrı ayrı)
class DataSectionIn(BaseModel):
    data: ConnIn


class DictSectionIn(BaseModel):
    dictionary: ConnIn
    data: ConnIn | None = None       # yalnız doğrulama için (kaydedilmez)


def _remember_mappings(dic: dict[str, Any], got: Any) -> None:
    """İçerikten bulunan sütun eşlemeleri de kaydedilir (elle seçilenlerle birlikte) — her açılışta yeniden aranmaz."""
    if not got:
        return
    merged = {k: dict(v) for k, v in (dic.get("mappings") or {}).items()}
    for name, m in got.learned_mappings().items():
        merged.setdefault(name, {}).update({f: h for f, h in m.items() if f not in merged.get(name, {})})
    dic["mappings"] = merged


def _store(f: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in f.items() if k not in ("password", "odbc")} | {"password_enc": conns.protect(f.get("password") or "")}


@app.put("/api/settings/connections/data")
def save_data_connection(body: DataSectionIn, request: Request) -> dict[str, Any]:
    """Yalnız veri kaynağını kaydeder (sözlük ve LLM ayarına dokunmaz)."""
    ident = _require_settings_access(request)
    data, _ = _resolved(ConnTestIn(target="data", data=body.data))
    if not data["database"]:
        raise HTTPException(400, "Veri kaynağı için veritabanı seçin.")
    conns.save_connections({"data": _store(data)})
    _rebuild_services()
    log.info("Veri kaynağı güncellendi (%s): %s/%s", ident.username, data["server"], data["database"])
    return {"ok": state.startup_error is None, "error": state.startup_error, "tables": len(state.services.dictionary.tables)}


@app.put("/api/settings/connections/dictionary")
def save_dictionary_connection(body: DictSectionIn, request: Request) -> dict[str, Any]:
    """Yalnız veri sözlüğünü kaydeder. "Veri kaynağıyla aynı" seçiliyse sunucu / kimlik bilgisi kopyalanmaz:
    çalışma anında veri kaynağının geçerli ayarı (arayüz ya da .env) kullanılır."""
    ident = _require_settings_access(request)
    saved = conns.load_connections() or {}
    _, dic = _resolved(ConnTestIn(target="dictionary", data=body.data or ConnIn(), dictionary=body.dictionary))
    if dic["kind"] == "sqlserver" and dic.get("same_as_data"):
        section = {"kind": "sqlserver", "same_as_data": True, "database": dic["database"], "sources": dic["sources"]}
    else:
        section = _store(dic)
    # içerikten tanıma için veri kaynağının kataloğu: formdaki veri kaynağı, yoksa kayıtlı / .env
    if body.data is not None:
        data_fields, _ = _resolved(ConnTestIn(target="data", data=body.data))
    else:
        data_fields = {"odbc": conns.data_odbc(get_settings()), "database": "(geçerli veri kaynağı)"}
    # sözlük tabloları hatalıysa kaydetme (sunucuya o an ulaşılamıyorsa kayda izin ver); doğrulama çalışma anındaki bağlantıyla
    from app.dictionary import sources as dsrc
    runtime = conns.dictionary_conn_fields({"data": saved.get("data"), "dictionary": section})
    runtime["kind"] = dic["kind"]
    if dic["kind"] != "sqlserver" or not dic.get("same_as_data"):
        runtime = dic
    try:
        got = None if dic["kind"] == "none" else dsrc.collect(conns.open_dictionary_reader(runtime)[0], dic["sources"],
                                                               dic.get("mappings"), _data_catalog(data_fields).get("known"))
    except Exception:  # noqa: BLE001
        got = None
    if got and got.errors:
        raise HTTPException(422, "Kaydedilmedi — sözlük tabloları hatalı: " + " ".join(got.errors))
    _remember_mappings(dic, got)
    section["mappings"] = dic["mappings"]
    conns.save_connections({"dictionary": section})
    _rebuild_services()
    log.info("Veri sözlüğü güncellendi (%s): %s %s", ident.username, dic["kind"], dic.get("database", ""))
    return {"ok": state.startup_error is None, "error": state.startup_error, "tables": len(state.services.dictionary.tables)}


@app.delete("/api/settings/connections/{section}")
def reset_connection_section(section: str, request: Request) -> dict[str, Any]:
    """Tek bölümü sil (data → .env SQLSERVER_ODBC, dictionary → dictionary.toml)."""
    _require_settings_access(request)
    if section not in ("data", "dictionary"):
        raise HTTPException(404, "Bölüm data ya da dictionary olmalı.")
    conns.save_connections({section: None})
    _rebuild_services()
    return {"ok": state.startup_error is None, "error": state.startup_error}


@app.delete("/api/settings/connections")
def reset_connections(request: Request) -> dict[str, Any]:
    """Arayüzden kaydedilen veritabanı ayarlarını sil: .env / dictionary.toml'a geri dön (LLM ayarı korunur)."""
    _require_settings_access(request)
    conns.save_connections({"data": None, "dictionary": None})
    _rebuild_services()
    return {"ok": state.startup_error is None, "error": state.startup_error}


def _rebuild_services() -> None:
    state.services = build_services()
    state.agent.services = state.services


@app.exception_handler(ValidationError)
def _validation_handler(_: Request, exc: ValidationError) -> JSONResponse:
    return JSONResponse({"detail": [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]}, status_code=422)


# --------------------------------------------------------------------------- arayüz (derlenmiş)
# frontend/dist varsa backend arayüzü de sunar: Node.js / Vite olmadan tek port (kurum / çevrimdışı kurulum).
# Geliştirmede Vite (5173) kullanılmaya devam eder; bu yalnızca http://127.0.0.1:<port>/ adresini de çalışır kılar.
_DIST = BACKEND_DIR.parent / "frontend" / "dist"
if (_DIST / "index.html").exists():
    from fastapi.staticfiles import StaticFiles

    class _Frontend(StaticFiles):
        """index.html önbelleğe alınmaz (yeni sürümde eski arayüz gelmesin); assets/ dosyaları adında hash taşır."""

        async def get_response(self, path, scope):  # type: ignore[override]
            resp = await super().get_response(path, scope)
            if resp.media_type == "text/html" or path in ("", ".", "index.html"):
                resp.headers["Cache-Control"] = "no-cache"
            return resp

    app.mount("/", _Frontend(directory=_DIST, html=True), name="frontend")
