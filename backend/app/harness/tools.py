"""Agent araçları. Model yalnızca bu araçlar üzerinden dünyaya dokunur.

Her araç: isim, açıklama, JSON şema, hangi fazlarda açık olduğu, risk seviyesi ve handler.
Handler'lar deterministiktir; hatalar modele düzeltmesi için açık bir mesajla döner.
"""

from __future__ import annotations

import copy
import json
import re
import typing
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.data.connector import Connector, QueryError, QueryResult
from app.data.validator import RolePolicy, SqlValidator
from app.dictionary.repository import DataDictionary
from app.harness.session import Phase, Requirements, Session
from app.spec.models import Dataset, DatasetField, ReportSpec, Theme, semantic_errors


@dataclass
class Services:
    settings: Settings
    dictionary: DataDictionary
    connector: Connector
    validator: SqlValidator
    policies: dict[str, RolePolicy]

    def policy(self, role: str) -> RolePolicy:
        # tanımsız rol (ör. eski kayıtlardaki ad) varsayılan "standart" role düşer
        return self.policies.get(role) or self.policies.get("standart") or next(iter(self.policies.values()))


@dataclass
class ToolContext:
    session: Session
    services: Services


@dataclass
class ToolResult:
    ok: bool
    content: Any                       # modele dönen içerik (JSON'a çevrilir)
    summary: str                       # UI'da görünen kısa özet
    state_changed: bool = False
    next_phase: Phase | None = None
    kickoff: str | None = None         # faz geçişinde yeni fazın ilk (sistem) talimatı


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    phases: tuple[Phase, ...]
    handler: Callable[[ToolContext, dict[str, Any]], ToolResult]
    risk: str = "read"                 # read | write (write → onay gerektirir; şu an hiçbir araç yazmıyor)
    status: str = ""

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


# --------------------------------------------------------------------------- yardımcılar
_CURRENCY_HINT = re.compile(r"(amount|tutar|sales|revenue|ciro|try|tl|fee|limit|ticket|sepet|balance|bakiye)", re.I)
_PERCENT_HINT = re.compile(r"(growth|rate|share|pct|percent|ratio|oran|pay|buyume|yoy|mom)", re.I)
_RATIO_HINT = re.compile(r"(margin|marj|change|degisim|diff|fark|delta)", re.I)


def _infer_format(name: str, ftype: str) -> str | None:
    if ftype != "number":
        return None
    if _PERCENT_HINT.search(name):
        return "percent"
    if _CURRENCY_HINT.search(name):
        return "currency"
    return "number"


def _label_for(col: str, tables: list[str], dictionary: DataDictionary) -> str | None:
    for t in tables:
        tt = dictionary.tables.get(t)
        if not tt:
            continue
        for c in tt.columns:
            if c.name == col.lower():
                return c.business_name
    return None


def profile(result: QueryResult, max_top: int = 5) -> dict[str, Any]:
    cols: dict[str, Any] = {}
    for i, (name, typ) in enumerate(zip(result.columns, result.types)):
        vals = [r[i] for r in result.rows]
        non_null = [v for v in vals if v is not None]
        info: dict[str, Any] = {"type": typ, "nulls": len(vals) - len(non_null)}
        if typ == "number" and non_null:
            nums = [float(v) for v in non_null if isinstance(v, (int, float))]
            if nums:
                info.update(min=_r(min(nums)), max=_r(max(nums)), sum=_r(sum(nums)), avg=_r(sum(nums) / len(nums)))
        elif non_null:
            counts: dict[str, int] = {}
            for v in non_null:
                counts[str(v)] = counts.get(str(v), 0) + 1
            info["distinct"] = len(counts)
            if typ == "date":
                info.update(min=min(map(str, non_null)), max=max(map(str, non_null)))
            else:
                info["top"] = [k for k, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:max_top]]
        cols[name] = info
    return {"row_count": len(result.rows), "truncated": result.truncated, "columns": cols}


def _r(x: float) -> float:
    return round(x, 4) if abs(x) < 100 else round(x, 2)


def _rows_preview(result: QueryResult, n: int) -> list[dict[str, Any]]:
    return [dict(zip(result.columns, r)) for r in result.rows[:n]]


def _run_validated(ctx: ToolContext, sql: str, max_rows: int) -> tuple[QueryResult | None, list[str], list[str], list[str]]:
    """(sonuç, hatalar, tablolar, uyarılar)"""
    pol = ctx.services.policy(ctx.session.user_role)
    v = ctx.services.validator.validate(sql, pol)
    if not v.ok:
        return None, v.errors, v.tables, v.warnings
    try:
        res = ctx.services.connector.execute(v.sql, min(max_rows, pol.max_rows))
    except QueryError as e:
        return None, [f"Veritabanı hatası: {_short_db_error(str(e))}"] + _error_hints(ctx, str(e), v.tables, v.sql), v.tables, v.warnings
    return res, [], v.tables, v.warnings


_SQLSERVER_MSG = re.compile(r"\[SQL Server\]([^\[;]+)")


def _short_db_error(msg: str) -> str:
    """ODBC gürültüsünü atıp SQL Server'ın asıl mesajını bırakır."""
    if "HYT00" in msg or "Query timeout expired" in msg:
        return ("Sorgu zaman aşımına uğradı (QUERY_TIMEOUT_S süresinde bitmedi). Sorguyu daraltın (WHERE / TOP) "
                "ya da sunucu yoğun / belleği yetersiz olabilir.")
    found = [re.sub(r"\s*\(\d+\).*$", "", m).strip().rstrip(".") for m in _SQLSERVER_MSG.findall(msg)]
    return "; ".join(dict.fromkeys(f for f in found if f)) if found else msg[:400]


def _alias_misuse(ctx: ToolContext, sql: str, col: str) -> list[str]:
    """alias.kolon kullanımında alias'ın tablosunda o kolon yoksa: doğru tabloya giden eksik JOIN'leri alias'larla yazar."""
    import sqlglot
    from sqlglot import exp

    dd = ctx.services.dictionary
    try:
        tree = sqlglot.parse_one(sql, read=ctx.services.connector.dialect)
    except Exception:  # noqa: BLE001
        return []
    from app.dictionary.repository import sql_table_key
    aliases = {t.alias_or_name.lower(): k for t in tree.find_all(exp.Table) if t.db
               for k in [sql_table_key(t, ctx.services.dictionary)] if k}
    out = []
    for c in tree.find_all(exp.Column):
        if c.name.lower() != col.lower() or not c.table or c.table.lower() not in aliases:
            continue
        a, table = c.table.lower(), aliases[c.table.lower()]
        if col.lower() in {x.name for x in dd.tables[table].columns} if table in dd.tables else True:
            continue
        owners = [o for o in dd.tables_with_column(col) if o != table]
        best = None
        for o in owners:
            p = dd.join_path([table], o)
            if p is not None and (best is None or len(p) < len(best[1])):
                best = (o, p)
        if not best:
            out.append(f"İPUCU: {a}.{col} hatalı: {table} tablosunda '{col}' yok (bulunduğu tablolar: {', '.join(owners)}).")
            continue
        owner, path = best
        lines, cur_alias, cur = [], a, table
        for i, r in enumerate(path):
            nxt = r.to_table if r.from_table == cur else r.from_table
            na = f"j{i + 1}"
            conds = " AND ".join(f"{na}.{(b if r.from_table == cur else x)} = {cur_alias}.{(x if r.from_table == cur else b)}"
                                 for x, b in r.pairs)
            lines.append(f"JOIN {dd.tables[nxt].display_name or nxt} AS {na} ON {conds}")
            cur_alias, cur = na, nxt
        out.append(f"İPUCU: {a}.{col} hatalı — {table} tablosunda '{col}' kolonu yok; bu kolon {owner} tablosunda. "
                   f"{table} ({a}) ile {owner} arasında ara tablo(lar) gerekiyor. Şu JOIN'leri ekleyip {col} yerine "
                   f"{cur_alias}.{col} kullanın (takma adları değiştirebilirsiniz):\n" + "\n".join(lines))
    return out


def _error_hints(ctx: ToolContext, msg: str, query_tables: list[str], sql: str = "") -> list[str]:
    """Sık SQL Server hataları için sözlükten somut düzeltme önerisi üretir."""
    dd = ctx.services.dictionary
    hints: list[str] = []
    for col in dict.fromkeys(re.findall(r"Invalid column name '([^']+)'", msg)):
        owners = dd.tables_with_column(col)
        if not owners:
            hints.append(f"İPUCU: '{col}' hiçbir tabloda yok. get_table_details ile doğru kolon adını bulun.")
            continue
        misuse = _alias_misuse(ctx, sql, col) if sql else []
        if misuse:
            hints += misuse
            continue
        if any(o in [t.lower() for t in query_tables] for o in owners):
            hints.append(f"İPUCU: '{col}' kolonu {', '.join(owners)} tablosunda; takma adı (alias) doğru tabloya ait mi kontrol edin.")
            continue
        for owner in owners[:2]:
            path = dd.join_path(query_tables, owner)
            if path:
                joins = "\n".join(f"JOIN ... ON {r.join_sql()}" for r in path)
                hints.append(f"İPUCU: '{col}' kolonu {owner} tablosunda. Sorgudaki tablolardan oraya {len(path)} adımlık JOIN zinciriyle ulaşılır "
                             f"(her adımı ekleyin, takma adları kendiniz verin):\n{joins}")
                break
        else:
            hints.append(f"İPUCU: '{col}' kolonu {', '.join(owners)} tablosunda; bu tabloyu JOIN ile ekleyin (get_table_details ile ilişkilere bakın).")
    for obj in dict.fromkeys(re.findall(r"Invalid object name '([^']+)'", msg)):
        hints.append(f"İPUCU: '{obj}' tablosu yok. search_dictionary ile doğru tabloyu bulun ve şemasıyla yazın.")
    for col in dict.fromkeys(re.findall(r"Ambiguous column name '([^']+)'", msg)):
        hints.append(f"İPUCU: '{col}' birden çok tabloda var; kolonu tablo takma adıyla yazın (ör. f.{col}).")
    if "is invalid in the select list because it is not contained in either an aggregate function or the GROUP BY" in msg:
        hints.append("İPUCU: SELECT'teki toplanmayan her ifadeyi GROUP BY'a aynen ekleyin (takma ad değil, ifadenin kendisi).")
    if re.search(r"near 'LIMIT'|Incorrect syntax near 'LIMIT'", msg):
        hints.append("İPUCU: SQL Server'da LIMIT yok; SELECT TOP 10 ... ORDER BY ... kullanın.")
    return hints


def _build_dataset(ctx: ToolContext, raw: dict[str, Any]) -> tuple[Dataset | None, dict[str, Any] | None, list[str]]:
    """SQL'i doğrular, çalıştırır, alan tiplerini/etiketlerini çıkarır."""
    did = str(raw.get("id") or "").strip()
    sql = str(raw.get("sql") or "").strip()
    if not sql and raw.get("verified") is not None:
        # çalışma hafızasındaki doğrulanmış sorguya numarayla referans (küçük modeller SQL'i yeniden yazmasın)
        mem = ctx.session.phase_memory.get("verified_sql", [])
        try:
            sql = mem[int(raw["verified"]) - 1]["sql"]
        except (ValueError, IndexError, TypeError):
            return None, None, [f"[{did}] verified={raw.get('verified')} geçersiz; 1..{len(mem)} arası bir numara verin."]
    if not did or not sql:
        return None, None, ["Her dataset için 'id' ve 'sql' (ya da 'verified' numarası) zorunlu."]
    res, errors, tables, warnings = _run_validated(ctx, sql, ctx.services.settings.max_rows)
    if errors or res is None:
        return None, None, [f"[{did}] {e}" for e in errors]
    if not tables:
        return None, None, [f"[{did}] Sorgu hiçbir tablodan okumuyor (sabit değerler). Veri UYDURMA: değerleri sözlükteki "
                            "tablolardan SQL ile hesaplayın."]
    if not res.rows:
        return None, None, [f"[{did}] Sorgu hiç satır döndürmedi; filtreleri/tarih aralığını kontrol edin."]
    if did.lower().startswith("kpi") and len(res.rows) > 1:
        return None, None, [f"[{did}] KPI dataset'i TEK satır olmalı ama {len(res.rows)} satır döndü (ör. yıl başına bir satır). "
                            "Dönemleri kolonlara çevirin: bu dönemin değeri (sales_amount) + değişim oranı "
                            "(sales_growth = (bu - önceki) / önceki), CASE WHEN YEAR(...) = ... ile tek SELECT'te."]
    overrides = {f.get("name"): f for f in raw.get("fields") or [] if isinstance(f, dict)}
    fields = []
    for name, typ in zip(res.columns, res.types):
        o = overrides.get(name, {})
        fields.append(DatasetField(
            name=name, type=typ,
            label=o.get("label") or _label_for(name, tables, ctx.services.dictionary) or name.replace("_", " ").title(),
            format=o.get("format") if o.get("format") in ("number", "currency", "percent", "compact") else _infer_format(name, typ),
        ))
    try:
        ds = Dataset(id=did, description=str(raw.get("description") or ""), sql=sql, fields=fields)
    except ValidationError as e:
        return None, None, [f"[{did}] {err['msg']}" for err in e.errors()]
    prof = profile(res)
    # adı marj/değişim/fark çağrıştıran ve değerleri -1.5..1.5 aralığında olan sayı kolonları orandır
    for f in ds.fields:
        info = prof["columns"].get(f.name, {})
        if (f.type == "number" and f.format == "number" and _RATIO_HINT.search(f.name) and f.name not in overrides
                and info.get("min") is not None and -1.5 <= info["min"] and info["max"] <= 1.5):
            f.format = "percent"
    prof["sample_rows"] = _rows_preview(res, 5)
    if warnings:
        prof["join_warnings"] = warnings
    return ds, prof, []


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif v is None:
            out.pop(k, None)
        else:
            out[k] = v
    return out


_DEFAULT_SIZE = {"kpi": (3, 2), "gauge": (3, 3), "text": (12, 1), "table": (12, 4), "pie": (4, 4), "donut": (4, 4),
                 "funnel": (4, 4), "treemap": (6, 4), "matrix": (12, 5)}


def _layout_visuals(visuals: list[dict[str, Any]], pages: list[dict[str, Any]] | None = None) -> list[str]:
    """Eksik pozisyonları yerleştirir, taşan/çakışanları aşağı iter. Deterministik düzeltme = modele daha az yük.
    Her sayfanın kendi ızgarası vardır (page yoksa ilk sayfa)."""
    notes: list[str] = []
    page_ids = [p.get("id") for p in pages or [] if isinstance(p, dict)]
    grids: dict[Any, set[tuple[int, int]]] = {}
    occupied: set[tuple[int, int]] = set()

    def free(x: int, y: int, w: int, h: int) -> bool:
        return all((xx, yy) not in occupied for xx in range(x, x + w) for yy in range(y, y + h))

    def place(x: int, y: int, w: int, h: int) -> None:
        occupied.update((xx, yy) for xx in range(x, x + w) for yy in range(y, y + h))

    def find_slot(w: int, h: int, start_y: int = 0) -> tuple[int, int]:
        y = start_y
        while True:
            for x in range(0, 13 - w):
                if free(x, y, w, h):
                    return x, y
            y += 1

    for v in visuals:
        pg = v.get("page") if v.get("page") in page_ids else (page_ids[0] if page_ids else None)
        occupied = grids.setdefault(pg, set())
        p = v.get("position") if isinstance(v.get("position"), dict) else None
        dw, dh = _DEFAULT_SIZE.get(v.get("type", ""), (6, 4))
        if p is None:
            w, h = dw, dh
            x, y = find_slot(w, h)
            v["position"] = {"x": x, "y": y, "w": w, "h": h}
            place(x, y, w, h)
            continue
        try:
            w = max(1, min(12, int(p.get("w", dw))))
            h = max(1, min(12, int(p.get("h", dh))))
            x = max(0, min(12 - w, int(p.get("x", 0))))
            y = max(0, int(p.get("y", 0)))
        except (TypeError, ValueError):
            w, h, x, y = dw, dh, 0, 0
        if not free(x, y, w, h):
            nx, ny = find_slot(w, h, y)
            notes.append(f"'{v.get('id')}' çakışıyordu, (x={nx}, y={ny}) konumuna taşındı.")
            x, y = nx, ny
        v["position"] = {"x": x, "y": y, "w": w, "h": h}
        place(x, y, w, h)

    # sıkıştırma: her sayfada tamamen boş satırlar kaldırılır (görsel başka sayfaya taşındığında ya da silindiğinde
    # sayfanın üstünde / arasında boşluk kalmasın); görsellerin birbirine göre düzeni korunur
    for pg in grids:
        on_page = [v for v in visuals if (v.get("page") if v.get("page") in page_ids else (page_ids[0] if page_ids else None)) == pg]
        used = {y for v in on_page for y in range(v["position"]["y"], v["position"]["y"] + v["position"]["h"])}
        if not used:
            continue
        empty = sorted(set(range(max(used))) - used)
        if not empty:
            continue
        for v in on_page:
            v["position"]["y"] -= sum(1 for e in empty if e < v["position"]["y"])
    return notes


_IGNORED = "UYGULANMADI (desteklenmeyen alan)"


def _model_of(ann: Any) -> type[BaseModel] | None:
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return ann
    for a in typing.get_args(ann):
        if (m := _model_of(a)) is not None:
            return m
    return None


_COLUMN_DIM_ALIASES = ("columns_dim", "columnsDim", "column_dim", "columnDimension", "column", "pivot")


def _matrix_aliases(raw: dict[str, Any]) -> None:
    """Matris sütun boyutunun yaygın yazımları (columns_dim, column …) columnDim'e taşınır; "desteklenmeyen alan" sayılmaz."""
    for v in raw.get("visuals") or []:
        enc = v.get("encoding") if isinstance(v, dict) and v.get("type") == "matrix" else None
        if not isinstance(enc, dict):
            continue
        for alias in _COLUMN_DIM_ALIASES:
            if alias in enc:
                val = enc.pop(alias)
                if not enc.get("columnDim") and isinstance(val, str) and val:
                    enc["columnDim"] = val


def _ignored_fields(model: type[BaseModel], raw: Any, path: str = "") -> list[str]:
    """Şemada olmayan alanlar (spec extra='ignore' ile onları sessizce atar): modele bildirilsin ki
    'değiştirdim' demesin. Dataset'ler session'dan geldiği için atlanır."""
    if not isinstance(raw, dict):
        return []
    out: list[str] = []
    fields = model.model_fields
    for k, v in raw.items():
        if k not in fields:
            out.append(f"{path}{k}")
            continue
        if k == "datasets" or (sub := _model_of(fields[k].annotation)) is None:
            continue
        if isinstance(v, list):
            for i, it in enumerate(v):
                out += _ignored_fields(sub, it, f"{path}{k}[{it.get('id', i) if isinstance(it, dict) else i}].")
        else:
            out += _ignored_fields(sub, v, f"{path}{k}.")
    return out


def _validate_spec(ctx: ToolContext, spec_raw: dict[str, Any]) -> tuple[ReportSpec | None, list[str], list[str]]:
    """Spec'i tamamlar (dataset'ler session'dan), yerleşimi düzeltir, doğrular."""
    s = ctx.session
    raw = copy.deepcopy(spec_raw)
    _matrix_aliases(raw)
    ignored = _ignored_fields(ReportSpec, raw)
    known = {d.id: d for d in s.datasets}
    if s.spec:
        for d in s.spec.datasets:
            known.setdefault(d.id, d)
    # Model dataset'leri tekrar yazmak zorunda değil: visual'larda kullanılanları session'dan ekle.
    visuals = [v for v in raw.get("visuals") or [] if isinstance(v, dict)]
    for v in visuals:   # metin görseli: model boş datasetId ("") yazabiliyor
        if not v.get("datasetId"):
            v.pop("datasetId", None)
    wanted = {d.get("id") for d in raw.get("datasets") or [] if isinstance(d, dict)}
    wanted |= {v.get("datasetId") for v in visuals}
    wanted |= {(v.get("options") or {}).get("sparklineDatasetId") for v in visuals if isinstance(v.get("options"), dict)}
    wanted.discard(None)
    unknown = sorted(str(d) for d in wanted if d not in known)
    if unknown:
        return None, [f"Dataset(ler) kayıtlı değil: {unknown}. Kayıtlı olanlar: {sorted(known)}. "
                      "Yeni veri gerekiyorsa önce add_dataset ile ekleyin."], []
    raw["datasets"] = [known[d].model_dump() for d in known if d in wanted]
    if not raw.get("theme") and s.design_brief:
        b = s.design_brief
        theme: dict[str, Any] = {}
        if b.mode:
            theme["mode"] = b.mode
        if b.palette and len(b.palette) >= 3:
            theme["palette"] = b.palette
        if b.accent:
            theme["accent"] = b.accent
        if b.background:
            theme["background"] = b.background
        raw["theme"] = theme
    if isinstance(raw.get("theme"), dict) and raw["theme"].get("mode") == "dark":
        # Koyu mod seçildi ama renkler verilmediyse okunabilir koyu varsayılanlar
        for k, v in {"background": "#0b1220", "surface": "#111a2e", "text": "#e5e7eb", "mutedText": "#94a3b8",
                     "border": "#1f2a44"}.items():
            raw["theme"].setdefault(k, v)
    notes = _layout_visuals([v for v in raw.get("visuals") or [] if isinstance(v, dict)], raw.get("pages")) \
        + _resolve_filters(ctx, raw)         + _fix_axes(raw) + _chart_rules(ctx, raw)
    if ignored:
        notes.insert(0, f"{_IGNORED}: {', '.join(ignored[:12])}. Bu alanlar şemada yok ve UYGULANMADI. "
                        "Görsel stil için yalnız options.color / background / textColor / valueSize / accentBar (kpi) "
                        "ya da update_report ile theme kullanılabilir; yapılamayan değişikliği kullanıcıya açıkça söyle.")
    # para birimi belirtilmemiş tutar görsellerine kurum varsayılanını ver
    currency_fields = {f["name"] for d in raw["datasets"] for f in d.get("fields") or [] if f.get("format") == "currency"}
    for v in raw.get("visuals") or []:
        if not isinstance(v, dict):
            continue
        opts = v.setdefault("options", {}) if isinstance(v.get("options"), dict) or v.get("options") is None else {}
        enc = v.get("encoding") or {}
        used = {enc.get("value"), *(enc.get("y") or []), *(enc.get("columns") or []), *(enc.get("values") or [])}
        if isinstance(opts, dict) and not opts.get("currency") and (opts.get("format") in ("currency", "compact") or used & currency_fields):
            opts["currency"] = ctx.services.settings.default_currency
    try:
        spec = ReportSpec.model_validate(raw)
    except ValidationError as e:
        return None, [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()][:15], notes
    errs = semantic_errors(spec) + _filter_errors(ctx, spec)
    if errs:
        return None, errs[:15], notes
    return spec, [], notes


_AMOUNT_TYPES = {"decimal", "numeric", "money", "smallmoney", "float", "real", "double"}


def _filterable(c, allow_pii: bool) -> bool:
    """Filtre (dilimleyici) olabilecek kolon: ölçü / tutar değil, kişisel veri değil (rol bilinmese de: metin, tarih, kod)."""
    if c.is_pii and not allow_pii:
        return False
    if (c.role or "").lower() in ("measure", "key") or c.name.endswith("key"):   # vekil anahtar: dilimleyici olarak anlamsız
        return False
    return re.sub(r"\(.*", "", (c.data_type or "").lower()) not in _AMOUNT_TYPES


def _dashboard_tables(ctx: ToolContext) -> dict[str, list[str]]:
    """Dashboard'un (spec + kayıtlı) dataset'lerinin okuduğu tablo / view'lar → onları okuyan dataset id'leri."""
    from app.harness.session import _source_tables
    s = ctx.session
    datasets = {d.id: d for d in s.datasets}
    if s.spec:
        datasets.update({d.id: d for d in s.spec.datasets})
    out: dict[str, list[str]] = {}
    for did, d in datasets.items():
        for t in _source_tables([d.model_dump()]):
            out.setdefault(t, []).append(did)
    return out


def _filter_candidates(ctx: ToolContext, text: str, scope: str = "auto") -> list[tuple[float, str, str, str]]:
    """Filtre etiketine / kolon adına en uygun kolonlar: (skor, tablo, kolon, iş adı).
    Önce dashboard'un kullandığı tablolarda aranır; orada eşleşme yoksa sözlüğün tamamında (scope: auto | dashboard | dictionary)."""
    from app.dictionary.repository import _score, tokens

    dd, pol = ctx.services.dictionary, ctx.services.policy(ctx.session.user_role)
    qt = tokens(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text))
    kinds = dd._table_kinds()
    ok = _usable(ctx)
    mine = set(_dashboard_tables(ctx))

    def search(tables, in_dashboard: bool):
        out = []
        for t in tables:
            if not ok(t):
                continue
            for c in t.columns:
                if not _filterable(c, pol.allow_pii):
                    continue
                sc = _score(qt, [(c.business_name, 3), (c.name, 2), (" ".join(c.synonyms), 2.5), (t.business_name, 1)])
                if sc <= 0:
                    continue
                extra = len([w for w in tokens(c.business_name) if not any(w.startswith(q[:4]) or q.startswith(w[:4]) for q in qt)])
                sc = sc / (1 + 0.3 * extra)
                if not in_dashboard:   # sözlükte: boyut tablosu / boyut kolonu tercih edilir
                    sc *= (1.5 if kinds.get(t.name) == "dimension" else 0.5) * (1.3 if c.role == "dimension" else 1)
                out.append((round(sc, 2), t.display_name or t.name, c.display_name or c.name, c.business_name))
        return sorted(out, reverse=True)

    if scope in ("auto", "dashboard"):
        hits = search([dd.tables[n] for n in mine if n in dd.tables], True)
        if hits or scope == "dashboard":
            return hits[:6]
    return search([t for n, t in dd.tables.items() if n not in mine], False)[:6]


def _filter_reach(ctx: ToolContext, table: str, column: str) -> tuple[list[str], list[str]]:
    """Bu kolonla filtre hangi görsellere uygulanır (ilişkiler üzerinden / doğrudan), hangilerine uygulanamaz."""
    from app.data.model_filters import ModelFilter, ModelFilterEngine
    s = ctx.session
    if not s.spec:
        return [], []
    from app.dictionary.model_rels import report_relationships
    eng = ModelFilterEngine(ctx.services.dictionary, ctx.services.connector.dialect,
                            report_relationships(ctx.services.dictionary, s.model_relationships))
    datasets = {d.id: d for d in s.datasets}
    datasets.update({d.id: d for d in s.spec.datasets})
    key = f"{table}.{column}".lower()
    reach: dict[str, bool] = {}
    for did, d in datasets.items():
        try:
            hit = key in eng.apply(d.sql, [ModelFilter(table.lower(), column.lower(), ["x"])])[1]
            if not hit and d.original_sql:
                hit = key in eng.apply(d.original_sql, [ModelFilter(table.lower(), column.lower(), ["x"])])[1]
        except Exception:  # noqa: BLE001
            hit = False
        reach[did] = hit
    yes = [v.title or v.id for v in s.spec.visuals if v.type != "text" and reach.get(v.datasetId or "")]
    no = [v.title or v.id for v in s.spec.visuals if v.type != "text" and v.datasetId and not reach.get(v.datasetId)]
    return yes, no


def h_find_filter_column(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    """Filtre kolonu ara: önce dashboard'un tablolarında, bulunamazsa sözlükte; her aday için uygulanacağı görseller."""
    q = str(a.get("query") or "").strip()
    if not q:
        return ToolResult(False, {"error": "query gerekli (ör. 'bölge', 'şube', 'ürün kategorisi')."}, "Sorgu yok")
    in_dash = _filter_candidates(ctx, q, "dashboard")
    source = "dashboard" if in_dash else "sözlük"
    cands = in_dash or _filter_candidates(ctx, q, "dictionary")
    dd = ctx.services.dictionary
    out = []
    for sc, tb, col, bn in cands[:6]:
        t = dd.tables.get(tb.lower())
        c = next((x for x in t.columns if x.name == col.lower()), None) if t else None
        yes, no = _filter_reach(ctx, tb, col)
        out.append({"table": tb, "column": col, "business_name": bn, "table_name": t.business_name if t else "",
                    **({"description": c.description} if c and c.description else {}),
                    **({"sample_values": c.sample_values} if c and c.sample_values else {}),
                    "applies_to_visuals": yes, "not_applied_visuals": no, "score": sc})
    out.sort(key=lambda c: (len(c["not_applied_visuals"]), -c["score"]))   # tüm görsellere uygulananlar önce
    out = out[:5]
    if not out:
        return ToolResult(True, {"source": None, "candidates": [],
                                 "note": "Ne dashboard'un tablolarında ne sözlükte eşleşen kolon bulundu; kullanıcıya hangi alanla "
                                         "filtrelemek istediğini sor ya da search_dictionary ile farklı kelimelerle ara."},
                          f"'{q}' için filtre kolonu bulunamadı")
    note = ("Adaylar dashboard'un kullandığı tablolardan." if source == "dashboard" else
            "Dashboard'un tablolarında bulunamadı; adaylar sözlükten. Kullanıcıya hangi tablodan geldiğini ve hangi görsellere "
            "uygulanacağını (not_applied_visuals dahil) söyle; uygun değilse farklı bir alan öner.")
    return ToolResult(True, {"source": source, "candidates": out, "note": note},
                      f"'{q}' → {len(out)} aday ({source})")


def _resolve_filters(ctx: ToolContext, raw: dict[str, Any]) -> list[str]:
    """Sözlükte olmayan filtre kolonlarını, etikete tek ve açık bir eşleşme varsa otomatik düzeltir."""
    dd, notes = ctx.services.dictionary, []
    for f in raw.get("filters") or []:
        if not isinstance(f, dict) or not f.get("table") or not f.get("column"):
            continue
        t = dd.tables.get(str(f["table"]).lower())
        if t and any(c.name == str(f["column"]).lower() for c in t.columns):
            continue
        cands = _filter_candidates(ctx, f"{f.get('label', '')} {f['column']}")
        if cands and (len(cands) == 1 or cands[0][0] >= cands[1][0] * 1.25):
            _, tb, col, bn = cands[0]
            notes.append(f"Filtre '{f.get('id')}': {f['table']}.{f['column']} sözlükte yoktu; {tb}.{col} ({bn}) olarak düzeltildi.")
            f["table"], f["column"] = tb, col
    return notes


def _filter_errors(ctx: ToolContext, spec: ReportSpec) -> list[str]:
    dd, pol = ctx.services.dictionary, ctx.services.policy(ctx.session.user_role)
    errs = []
    for f in spec.filters:
        if not (f.table and f.column):
            continue
        t = dd.tables.get(f.table.lower())
        col = next((c for c in t.columns if c.name == f.column.lower()), None) if t else None
        if not t or not col:
            cands = _filter_candidates(ctx, f"{f.label} {f.column}")
            sug = "; ".join(f"{tb}.{c} ({bn})" for _, tb, c, bn in cands) or "get_table_details ile bakın"
            errs.append(f"Filtre '{f.id}': {f.table}.{f.column} sözlükte yok. Uygun boyut kolonları: {sug}")
        elif col.is_pii and not pol.allow_pii:
            errs.append(f"Filtre '{f.id}': {f.table}.{f.column} kişisel veri; filtre olarak kullanılamaz.")
    return errs


# ---------------------------------------------------------------- grafik türü kuralları
# Seçim kuralları (talimatta da var; burada veri profiline göre UYGULANIR):
#   pasta / halka en çok PIE_MAX dilim ve yalnız pozitif değer → aksi halde sıralı yatay çubuk;
#   çubukta çok kategori (>6) ya da uzun etiket → yatay; >BAR_MAX kategori → ilk 15 (büyükten küçüğe);
#   çizgi / alan zaman ekseni ister; çok seride (>6) okunabilirlik uyarısı; gösterge hedef ister; dağılım iki sayı ister.
PIE_MAX, BAR_MAX, SERIES_MAX, LABEL_LONG = 8, 20, 6, 14
MATRIX_COLS, MATRIX_ROWS = 12, 60   # matris: sütun boyutunda en çok 12 değer; ilk seviyede 60'tan fazla grup okunmaz
_TOP_N = re.compile(r"(?:\b(?:ilk|top)\s*|\ben\s+(?:çok|fazla|yüksek|iyi|büyük|düşük|az)\b\D{0,25}?)(\d{1,3})\b", re.I)
_TIME_NAME = re.compile(r"(date|tarih|month|year|yil|yıl|donem|dönem|period|week|hafta|quarter|ceyrek|çeyrek|(^|_)ay($|_)|gun|gün)", re.I)
_TIME_VALUE = re.compile(r"^\d{4}([-/.]\d{1,2}([-/.]\d{1,2})?)?([ T].*)?$|^\d{4}\s*[-/ ]?\s*[QÇ]\d$|^\d{1,2}[-/.]\d{4}$", re.I)


def _chart_rules(ctx: ToolContext, raw: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    profiles = ctx.session.dataset_profiles
    for v in raw.get("visuals") or []:
        if not isinstance(v, dict):
            continue
        cols = (profiles.get(v.get("datasetId")) or {}).get("columns") or {}
        if not cols:
            continue
        typ, vid = v.get("type"), v.get("id")
        enc = v.get("encoding") if isinstance(v.get("encoding"), dict) else {}
        opts = v.setdefault("options", {}) if isinstance(v.get("options"), dict) or v.get("options") is None else {}
        ys = enc.get("y") if isinstance(enc.get("y"), list) else ([enc["y"]] if enc.get("y") else [])

        def info(c: Any) -> dict[str, Any]:
            return cols.get(c) or {} if isinstance(c, str) else {}

        def is_time(c: Any) -> bool:
            i = info(c)
            return i.get("type") == "date" or bool(isinstance(c, str) and _TIME_NAME.search(c)) \
                or bool(i.get("top")) and all(_TIME_VALUE.match(str(t)) for t in i["top"])

        def long_labels(c: Any) -> bool:
            return any(len(str(t)) > LABEL_LONG for t in info(c).get("top") or [])

        if typ == "matrix":
            cd, rows = enc.get("columnDim"), enc.get("rows") if isinstance(enc.get("rows"), list) else []
            n = info(cd).get("distinct") or 0
            if cd and n > MATRIX_COLS and not opts.get("maxColumns"):
                opts["maxColumns"] = MATRIX_COLS
                which = (f"son {MATRIX_COLS} dönem gösteriliyor" if is_time(cd)
                         else f"en büyük {MATRIX_COLS} değer gösteriliyor, kalanı 'Diğer' sütununda toplanır")
                notes.append(f"KURAL '{vid}': sütun boyutunda ({cd}) {n} değer var, matris okunmaz; {which} "
                             "(daha azı için dataset'i daralt ya da filtre ekle).")
            if rows and (info(rows[0]).get("distinct") or 0) > MATRIX_ROWS:
                notes.append(f"KURAL '{vid}': ilk satır seviyesinde ({rows[0]}) {info(rows[0])['distinct']} değer var; "
                             "matris çok uzun olur, filtre / ilk N ya da tablo önerilir.")
            if not cd and len(rows) < 2:
                notes.append(f"KURAL '{vid}': sütun boyutu (columnDim) ve ikinci satır seviyesi yok; tek boyut × ölçü için "
                             "table ya da bar yeterli.")
            continue
        if typ in ("pie", "donut"):
            cat, val = enc.get("category") or enc.get("x"), enc.get("value") or (ys[0] if ys else None)
            n, neg = info(cat).get("distinct") or 0, (info(val).get("min") or 0) < 0
            if cat and val and (n > PIE_MAX or neg):
                v["type"] = "bar"
                v["encoding"] = {**enc, "x": cat, "y": [val], "category": None, "value": None}
                opts.update(horizontal=True, sort="desc")
                why = f"{n} dilim okunmaz (en çok {PIE_MAX})" if n > PIE_MAX else "negatif değerler pasta / halkada gösterilemez"
                notes.append(f"KURAL '{vid}': {why}; sıralı yatay çubuk grafiğe çevrildi. Kullanıcıya bunu söyle.")
            continue
        x = enc.get("x")
        n = info(x).get("distinct") or 0
        if typ == "bar" and x and not is_time(x):
            if opts.get("horizontal") is None and (n > 6 or long_labels(x)):
                opts["horizontal"] = True
                notes.append(f"KURAL '{vid}': {n} kategori / uzun etiket → yatay çubuk.")
            if n > BAR_MAX and not opts.get("limit"):
                opts.update(limit=15, sort=opts.get("sort") or "desc")
                notes.append(f"KURAL '{vid}': {n} kategori çok fazla; en büyük 15 gösteriliyor (tamamı için tablo önerilir).")
        top_n = _TOP_N.search(str(v.get("title") or ""))
        if typ in ("table", "bar") and top_n and not opts.get("limit") and not (x and is_time(x)):
            # başlık "En Çok Satan 10 Ürün" diyor ama limit / sort verilmemiş: model "10 ürün sıralı" diye anlatıyordu
            opts.update(limit=int(top_n.group(1)), sort=opts.get("sort") or "desc")
            notes.append(f"KURAL '{vid}': başlıktaki 'ilk {top_n.group(1)}' için limit={top_n.group(1)} ve büyükten küçüğe sıralama verildi.")
        if typ in ("bar", "line", "area", "combo") and x and is_time(x) and opts.get("sort"):
            opts["sort"] = None
            notes.append(f"KURAL '{vid}': zaman ekseni ({x}) değere göre sıralanmaz; sıralama kaldırıldı (dönemler sırasıyla).")
        if typ in ("line", "area") and x and not is_time(x):
            notes.append(f"KURAL '{vid}': x ekseni ({x}) zaman değil; çizgi / alan zaman trendi içindir, kategori "
                         "karşılaştırması için bar kullan.")
        if enc.get("series") and (info(enc["series"]).get("distinct") or 0) > SERIES_MAX:
            notes.append(f"KURAL '{vid}': {info(enc['series'])['distinct']} seri okunmaz (en çok {SERIES_MAX}); seri "
                         "sayısını azalt ya da bar / tablo kullan.")
        fmts = {f.get("name"): f.get("format") or f.get("type") for d in raw.get("datasets") or [] if d.get("id") == v.get("datasetId")
                for f in d.get("fields") or []}
        if opts.get("stacked") and not enc.get("series") and len(ys) >= 2 and len({fmts.get(y) for y in ys}) > 1:
            opts["stacked"] = None
            notes.append(f"KURAL '{vid}': farklı birimdeki ölçüler ({', '.join(ys)}) üst üste yığılmaz; yığma kaldırıldı "
                         "(tutar + adet için combo kullan).")
        if typ == "combo" and len(ys) < 2:
            notes.append(f"KURAL '{vid}': combo iki ölçü içindir (ör. tutar + adet); tek ölçüde line / bar kullan.")
        if typ == "gauge" and opts.get("target") is None:
            notes.append(f"KURAL '{vid}': gösterge (gauge) hedef değer ister (options.target); hedef yoksa kpi kullan.")
        if typ == "scatter" and (info(x).get("type") != "number" or not ys or info(ys[0]).get("type") != "number"):
            notes.append(f"KURAL '{vid}': dağılım (scatter) iki sayısal ölçü ister (x ve y); kategori için bar kullan.")
    return notes


_PART_TYPES = ("pie", "donut", "funnel", "treemap")


def _normalize_encoding(typ: Any, enc: dict[str, Any], ys: list[str], types: dict[str, Any]) -> str:
    """Küçük modeller her türe x / y yazıyor (huni, ısı haritası, pasta category / value ister): alanlar türün beklediği
    yere taşınır. Aksi halde araç aynı hatayla tekrar tekrar reddediliyor ve model döngüye giriyordu."""
    done = []
    if typ in _PART_TYPES:
        if not enc.get("category") and enc.get("x"):
            enc["category"], enc["x"] = enc["x"], None
            done.append(f"x→category={enc['category']}")
        if not enc.get("value") and ys:
            enc["value"], enc["y"] = ys[0], None
            done.append(f"y→value={enc['value']}")
    elif typ == "heatmap":
        nums = [y for y in ys if types.get(y) == "number"]
        cats = [y for y in ys if y not in nums]
        if not enc.get("category") and cats:
            enc["category"] = cats[0]
            done.append(f"y→category={cats[0]}")
        elif not enc.get("category") and enc.get("series"):
            enc["category"], enc["series"] = enc["series"], None
            done.append(f"series→category={enc['category']}")
        if not enc.get("value") and nums:
            enc["value"] = nums[0]
            done.append(f"y→value={nums[0]}")
        if done:
            enc["y"] = None
    elif typ == "matrix":
        # satır: rows (yoksa x / category); sütun boyutu: columnDim (columns_dim / series / tek kategorik columns);
        # ölçüler: values (yoksa y / value / columns'taki sayılar)
        cols = enc.get("columns") if isinstance(enc.get("columns"), list) else []
        if not enc.get("values"):
            vals = [y for y in ys if types.get(y, "number") == "number"] or ([enc["value"]] if enc.get("value") else [])                 or [c for c in cols if types.get(c) == "number"]
            if vals:
                enc["values"] = vals
                done.append(f"→values={vals}")
        if not enc.get("rows"):
            rows = [c for c in (enc.get("x"), enc.get("category")) if c]                 or [c for c in cols if types.get(c) != "number"][:2]
            if rows:
                enc["rows"] = list(dict.fromkeys(rows))[:2]
                done.append(f"→rows={enc['rows']}")
        if not enc.get("columnDim") and enc.get("series"):
            enc["columnDim"] = enc["series"]
            done.append(f"series→columnDim={enc['columnDim']}")
        if done:
            for k in ("x", "y", "category", "value", "series", "columns"):
                enc.pop(k, None)
    elif typ in ("kpi", "gauge") and not enc.get("value") and ys:
        enc["value"] = ys[0]
        done.append(f"y→value={ys[0]}")
    return ", ".join(done)


def _fix_axes(raw: dict[str, Any]) -> list[str]:
    """Ters verilmiş eksenleri ve anlamsız alan eşleşmelerini düzeltir (küçük modellerin tipik hataları)."""
    types = {d.get("id"): {f.get("name"): f.get("type") for f in d.get("fields") or []} for d in raw.get("datasets") or []}
    notes = []
    for v in raw.get("visuals") or []:
        if not isinstance(v, dict):
            continue
        t, enc = types.get(v.get("datasetId"), {}), v.get("encoding") or {}
        ys = enc.get("y") if isinstance(enc.get("y"), list) else ([enc["y"]] if enc.get("y") else [])
        moved = _normalize_encoding(v.get("type"), enc, ys, t)
        if moved:
            v["encoding"] = enc
            ys = enc.get("y") or []
            notes.append(f"'{v.get('id')}': {v.get('type')} için alanlar yerleştirildi ({moved}).")
        opts = v.get("options") if isinstance(v.get("options"), dict) else {}
        if enc.get("series") and (enc["series"] in ys or t.get(enc["series"]) == "number"):
            notes.append(f"'{v.get('id')}': series='{enc['series']}' bir ölçü alanı; seri ayrımı kaldırıldı.")
            enc["series"] = None
        if v.get("type") == "kpi" and not opts.get("deltaField") and not opts.get("compareField")                 and enc.get("category") and t.get(enc["category"]) == "number" and enc["category"] != enc.get("value"):
            # model önceki dönem değerini category'ye koymuş: karşılaştırma alanı olarak kullan
            opts["compareField"] = enc["category"]
            v["options"] = opts
            notes.append(f"'{v.get('id')}': {enc['category']} önceki dönem değeri olarak compareField'a taşındı.")
            enc["category"] = None
        if opts.get("deltaField") and opts["deltaField"] in (enc.get("value"), *ys):
            notes.append(f"'{v.get('id')}': deltaField değerin kendisiydi ({opts['deltaField']}); kaldırıldı. "
                         "Değişim için *_growth gibi ayrı bir kolon kullanın.")
            opts["deltaField"] = None
        if v.get("type") in ("bar", "line", "area", "combo") and enc.get("x") and len(ys) == 1                 and t.get(enc["x"]) == "number" and t.get(ys[0]) in ("string", "date"):
            enc["x"], enc["y"] = ys[0], [enc["x"]]
            notes.append(f"'{v.get('id')}': x ve y eksenleri ters verilmişti, düzeltildi (x={enc['x']}).")
        elif v.get("type") in ("bar", "line", "area", "combo") and t.get(enc.get("x")) != "string" and t.get(enc.get("x")) != "date":
            # kategori ekseni sayı ya da boş: isim kolonunu (önce category, sonra dataset'teki ilk metin kolonu) eksene al
            label = enc.get("category") if t.get(enc.get("category")) in ("string", "date") else                 next((n for n, ty in t.items() if ty in ("string", "date") and n != enc.get("series")), None)
            if label:
                old_x = enc.get("x")
                enc["y"] = list(dict.fromkeys(([old_x] if old_x and t.get(old_x) == "number" else []) + ys))
                enc["x"], enc["category"] = label, None
                notes.append(f"'{v.get('id')}': kategori ekseninde sayı vardı; x={label} (isim), y={enc['y']} yapıldı.")
        if v.get("type") in ("pie", "donut", "funnel", "treemap") and enc.get("category") and enc.get("value")                 and t.get(enc["category"]) == "number" and t.get(enc["value"]) == "string":
            enc["category"], enc["value"] = enc["value"], enc["category"]
            notes.append(f"'{v.get('id')}': category ve value ters verilmişti, düzeltildi.")
        v["encoding"] = enc
    return notes


def _spec_ok(ctx: ToolContext, spec: ReportSpec, notes: list[str], what: str) -> ToolResult:
    ignored = [n for n in notes if n.startswith(_IGNORED)]
    prev = ctx.session.spec
    if ignored and prev is not None and prev.model_dump() == spec.model_dump():
        return ToolResult(False, {"ok": False, "errors": ignored + [
            "Dashboard DEĞİŞMEDİ. Kullanıcıya değişiklik yapıldı deme: desteklenen bir alanla tekrar dene "
            "ya da bunun yapılamadığını söyle."]}, "Değişiklik uygulanmadı (desteklenmeyen alan)")
    ctx.session.set_spec(spec)
    return ToolResult(True, {"ok": True, "spec_version": ctx.session.spec_version,
                             "visuals": [v.id for v in spec.visuals], "notes": notes},
                      f"{what} (v{ctx.session.spec_version}, {len(spec.visuals)} görsel)"
                      + (" — bazı alanlar uygulanmadı" if ignored else ""), state_changed=True)


# --------------------------------------------------------------------------- handler'lar
def _usable(ctx: ToolContext):
    """Agent'ın görebileceği nesneler: veritabanında var, SELECT yetkisi var, rol politikası izin veriyor
    (yalnız tablo ve view'lar — stored procedure'ler projede kullanılmaz)."""
    dd, pol = ctx.services.dictionary, ctx.services.policy(ctx.session.user_role)
    return lambda t: dd.usable(t) and not pol.denial_reason(t.name)


SEARCH_LIMIT_BEFORE_MODEL = 6   # veri fazında model önerisinden önce en çok bu kadar sözlük araması


def _session_id_hint(ctx: ToolContext, *texts: Any) -> str | None:
    """Tasarım fazında model kayıtlı dataset / görsel kimliklerini (channel_monthly, kpi_sales) veritabanı tablosu sanıp
    sözlükte / SQL'de arıyor ve döngüye giriyordu: bu kimlikler yakalanır, model doğru araca yönlendirilir."""
    s = ctx.session
    if s.phase != "design":
        return None
    hay = " ".join(str(t) for t in texts if t).lower().replace("[", " ").replace("]", " ")
    words = " " + re.sub(r"[^0-9a-zçğıöşü_]+", " ", hay).replace("dbo ", " ") + " "
    loose = words.replace("_", " ")
    def hit(i: str) -> bool:
        return f" {i.lower()} " in words or (" " in i.replace("_", " ") and f" {i.lower().replace('_', ' ')} " in loose)
    ds = next((d for d in s.datasets if hit(d.id)), None)
    if ds:
        return (f"'{ds.id}' veritabanı tablosu DEĞİL: bu raporda kayıtlı bir DATASET (alanlar: "
                f"{', '.join(f.name for f in ds.fields)}). Sözlükte / SQL'de arama yapma; doğrudan add_visual / update_visual "
                f"içinde datasetId='{ds.id}' olarak kullan.")
    vis = next((v for v in (s.spec.visuals if s.spec else []) if hit(v.id)), None)
    if vis:
        return (f"'{vis.id}' bir GÖRSEL kimliği ({vis.type}, '{vis.title}'); veri araması gerekmez. Değiştirmek için "
                f"update_visual (id='{vis.id}') kullan.")
    return None


def h_search_dictionary(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    q = str(a.get("query") or "")
    if (hint := _session_id_hint(ctx, q)):
        return ToolResult(False, {"error": hint}, "Bu bir dataset / görsel kimliği")
    s = ctx.session
    mem = s.phase_memory
    # yerel modeller "2–4 arama yeterli" talimatına rağmen aramayı sürdürebiliyor: veri fazında, model önerisi
    # (propose_model) yapılmadan sınır aşılırsa arama yerine bulunan aday tablolarla öneriye yönlendirilir
    if s.phase == "data" and s.model_proposal_at is None and mem.get("searches", 0) >= SEARCH_LIMIT_BEFORE_MODEL:
        # uyarı yetmiyor (model aramayı sürdürebiliyor): sistem, bulunan aday tablolarla öneriyi kendisi hazırlar
        seen = mem.get("seen_tables", [])[:5]
        if seen:
            prop = h_propose_model(ctx, {"tables": seen})
            if prop.ok:
                return ToolResult(True, {**prop.content, "note": (
                    f"ARAMA SINIRI ({SEARCH_LIMIT_BEFORE_MODEL}): yeni arama yapılmadı; sistem, bulduğun aday tablolarla model "
                    "önerisini hazırladı (aşağıda). Başka arama YAPMA: bu tabloları ve ilişkileri kullanıcıya özetle, eksik / fazla "
                    "tablo varsa sor ve onay iste.")},
                    f"Arama sınırı: sistem model önerisini hazırladı ({len(seen)} tablo)")
        return ToolResult(False, {"error": f"ARAMA SINIRI: bu fazda {SEARCH_LIMIT_BEFORE_MODEL} arama yapıldı. Yeni arama YAPMA; "
                                           "ŞİMDİ propose_model çağır ve kullanıcıya tabloları sor.",
                                  "candidate_tables": seen}, "Arama sınırı: model önerisine geç")
    if s.phase == "data" and s.model_proposal_at is not None \
            and not any(t.role == "user" for t in s.transcript[s.model_proposal_at:]):
        # öneri sunuldu, kullanıcı henüz yanıtlamadı: arama yerine onay sorulmalı (SQL'den önce onay kuralı)
        mem["ask_refusals"] = mem.get("ask_refusals", 0) + 1
        return ToolResult(False, {"error": "Model önerisi hazır ve kullanıcı henüz yanıtlamadı. Yeni arama YAPMA: önerideki "
                                           "tabloları ve ilişkileri kullanıcıya kısaca özetle, eksik / fazla tablo olup olmadığını "
                                           "sor ve onay iste; ardından DUR ve yanıtı bekle."},
                          "Önce kullanıcıya sorulmalı")
    hits = ctx.services.dictionary.search(q, int(a.get("limit") or 6), _usable(ctx))
    metrics = ctx.services.dictionary.search_metrics(q, 3)
    if s.phase == "data":
        mem["searches"] = mem.get("searches", 0) + 1
        mem["seen_tables"] = list(dict.fromkeys([*mem.get("seen_tables", []), *(h["table"] for h in hits)]))[:20]
    return ToolResult(True, {"tables": hits, "governed_metrics": metrics},
                      f"'{q}' → {len(hits)} tablo, {len(metrics)} metrik")


# İhtiyaç fazında salt-okunur keşif (tablo ayrıntısı / nesne keşfi): gerçekte var olan kolonlara bakıp KPI önerilebilsin,
# ama faz veri keşfine dönüşmesin diye sınırlı
REQ_PEEK_LIMIT = 3


def _names_arg(a: dict[str, Any], *keys: str) -> list[str]:
    """Tablo / nesne adları: modeller şemadaki adı (tables / objects) yerine table_name, table, name gibi adlar da yazıyor."""
    for k in (*keys, "table_names", "table_name", "table", "tables", "object_name", "object", "name"):
        v = a.get(k)
        if v:
            return [str(x) for x in v] if isinstance(v, list) else [str(v)]
    return []


def _req_peek_limit(ctx: ToolContext) -> ToolResult | None:
    if ctx.session.phase != "requirements":
        return None
    mem = ctx.session.phase_memory
    mem["req_peeks"] = mem.get("req_peeks", 0) + 1
    if mem["req_peeks"] > REQ_PEEK_LIMIT:
        return ToolResult(False, {"error": f"İhtiyaç fazında en fazla {REQ_PEEK_LIMIT} tablo incelemesi yapılır; ayrıntılı veri keşfi "
                                           "veri fazındadır. Şimdi kullanıcıya eksik soruları sor ya da save_requirements çağır."},
                          "İhtiyaç fazı: inceleme sınırı")
    return None


def h_get_table_details(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    if (r := _req_peek_limit(ctx)):
        return r
    names = _names_arg(a, "tables")
    if (hint := _session_id_hint(ctx, *names)):
        return ToolResult(False, {"error": hint}, "Bu bir dataset / görsel kimliği")
    pol = ctx.services.policy(ctx.session.user_role)
    ok = _usable(ctx)
    out, missing = [], []
    for n in names[:5]:
        t = ctx.services.dictionary.tables.get(str(n).lower())
        d = ctx.services.dictionary.table_details(str(n), pol.allow_pii) if t is not None and ok(t) else None
        if d and ctx.session.model_relationships:
            from app.dictionary.model_rels import report_relationships
            d["joins"] = d["joins"] + [r.describe() + " (yalnız bu rapor)" for r in report_relationships(
                ctx.services.dictionary, ctx.session.model_relationships) if t.name in (r.from_table, r.to_table)]
        (out.append(d) if d else missing.append(n))
    if not out:
        return ToolResult(False, {"error": f"Tablo(lar) sözlükte yok: {missing}. search_dictionary kullanın."}, "Tablo bulunamadı")
    return ToolResult(True, {"tables": out, **({"not_found": missing} if missing else {})},
                      ", ".join(d["table"] for d in out))


def _object_key(dd, name: str) -> str | None:
    """Model'in yazdığı ad → sözlük anahtarı (şema yazılmadıysa dbo, köşeli parantezler atılır)."""
    n = name.strip().replace("[", "").replace("]", "").lower()
    if n in dd.tables:
        return n
    if "." not in n and f"dbo.{n}" in dd.tables:
        return f"dbo.{n}"
    return next((k for k, t in dd.tables.items() if (t.display_name or "").lower() == n), None)


def h_discover_object(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    """SQL Server kataloğundan nesne özellikleri: kolon tipleri, NULL, PK, index, FK, satır sayısı, view kaynağı / tanımı."""
    from app.dictionary.discovery import compact, discover

    if (r := _req_peek_limit(ctx)):
        return r
    names = _names_arg(a, "objects")
    if not names:
        return ToolResult(False, {"error": "objects listesi gerekli (ör. ['dbo.FactResellerSales'])."}, "Nesne adı yok")
    if (hint := _session_id_hint(ctx, *names)):
        return ToolResult(False, {"error": hint}, "Bu bir dataset / görsel kimliği")
    if ctx.services.connector.dialect != "tsql":
        return ToolResult(False, {"error": "Nesne keşfi yalnız SQL Server'da çalışır; get_table_details kullanın."}, "Desteklenmiyor")
    dd = ctx.services.dictionary
    pol = ctx.services.policy(ctx.session.user_role)
    ok = _usable(ctx)
    out, errors = [], []
    for n in names[:3]:
        key = _object_key(dd, str(n))
        if key is None or not ok(dd.tables[key]):
            errors.append(f"{n}: yetkili nesneler arasında yok (search_dictionary ile doğru adı bulun)")
            continue
        try:
            out.append(compact(discover(dd, ctx.services.connector, key, include_pii=pol.allow_pii)))
        except Exception as e:  # noqa: BLE001 — katalog okunamazsa sözlük bilgisi yine kullanılabilir
            errors.append(f"{n}: {_short_db_error(str(e)) if not isinstance(e, LookupError) else e}")
    if not out:
        return ToolResult(False, {"error": "; ".join(errors)}, "Nesne keşfi başarısız")
    return ToolResult(True, {"objects": out, **({"errors": errors} if errors else {})},
                      ", ".join(f"{o['object']} ({o['type']}, {len(o['columns'])} kolon)" for o in out))


def h_propose_model(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    """Bulunan tablolar arasında ilişki önerisi (yabancı anahtar, aynı adlı anahtar + tekillik). Kullanıcıya sorulur."""
    from app.dictionary.model_rels import propose

    names = a.get("tables") or []
    if isinstance(names, str):
        names = [names]
    dd = ctx.services.dictionary
    ok = _usable(ctx)
    keys, missing = [], []
    for n in names[:8]:
        k = _object_key(dd, str(n))
        (keys.append(k) if k and ok(dd.tables[k]) and k not in keys else missing.append(str(n)))
    if len(keys) < 1:
        return ToolResult(False, {"error": f"Yetkili tablo bulunamadı: {missing}. search_dictionary ile doğru adları bulun."},
                          "Tablo bulunamadı")
    s, mem = ctx.session, ctx.session.phase_memory
    if s.model_proposal_at is not None and not any(t.role == "user" for t in s.transcript[s.model_proposal_at:]):
        mem["ask_refusals"] = mem.get("ask_refusals", 0) + 1   # öneri zaten sunuldu, kullanıcıya sorulmadan yenisi
    res = propose(dd, ctx.services.connector, keys, s.model_relationships)
    s.model_proposal_at = len(s.transcript)
    mem["last_proposal"] = {"tables": res["tables"], "candidates": res["candidates"]}
    new = [c for c in res["candidates"] if not c.get("already_in_model")]
    return ToolResult(True, {**res, **({"not_found": missing} if missing else {}),
                             "next": "Tabloları (rol, açıklama, satır sayısı, DataDate) ve ilişki önerilerini kullanıcıya özetle; "
                                     "hangi tablolarla devam edileceğini ve yeni ilişkileri ONAYLATIP save_relationships ile kaydet. "
                                     "Kullanıcı yanıt vermeden kaydetme."},
                      f"{len(keys)} tablo · {len(res['candidates'])} ilişki adayı ({len(new)} yeni)")


def h_save_relationships(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    """Kullanıcının onayladığı ilişkileri ortak modele kaydeder (sonraki raporlar ve filtreler de kullanır)."""
    from app.dictionary.model_rels import RelationshipRegistry, now_iso, rel_id, report_relationships, to_relationship

    s = ctx.session
    scope = str(a.get("scope") or "")
    if scope not in ("global", "report"):
        return ToolResult(False, {"error": "scope gerekli: kullanıcıya ilişkinin ORTAK modele mi (tüm raporlar kullanır) yoksa "
                                           "YALNIZ BU RAPORA mı kaydedileceğini sorun; yanıtına göre 'global' ya da 'report' verin."},
                          "Kapsam sorulmalı")
    if s.model_proposal_at is None:
        return ToolResult(False, {"error": "Önce propose_model ile tabloları ve ilişki önerisini kullanıcıya sunun."}, "Öneri yok")
    if not any(t.role == "user" for t in s.transcript[s.model_proposal_at:]):
        return ToolResult(False, {"error": "Kullanıcı öneriyi henüz onaylamadı. Tabloları ve ilişkileri sorup yanıtını bekleyin; "
                                           "onaydan sonra kaydedin."}, "Kullanıcı onayı bekleniyor")
    dd = ctx.services.dictionary
    ok = _usable(ctx)
    reg = RelationshipRegistry(ctx.services.settings.model_relationships)
    existing = {(r.from_table, r.to_table, tuple(r.pairs))
                for r in [*dd.relationships, *report_relationships(dd, s.model_relationships)]}
    saved, errors = [], []
    for i, r in enumerate((a.get("relationships") or [])[:20]):
        f, t = _object_key(dd, str(r.get("from_table") or "")), _object_key(dd, str(r.get("to_table") or ""))
        label = f"{r.get('from_table')} → {r.get('to_table')}"
        if not f or not t or not ok(dd.tables[f]) or not ok(dd.tables[t]) or f == t:
            errors.append(f"{label}: tablo bulunamadı ya da yetkisiz")
            continue
        card = str(r.get("cardinality") or "N:1").replace("?", "").upper()
        if card not in ("N:1", "1:1"):
            errors.append(f"{label}: '{card}' filtre yaymaz; yalnız N:1 (çok → tek) ya da 1:1 kaydedilir (yönü 'tek' tarafa çevirin)")
            continue
        fc, tc = {c.name for c in dd.tables[f].columns}, {c.name for c in dd.tables[t].columns}
        pairs = [(str(x).lower(), str(y).lower()) for x, y in (r.get("columns") or []) if x and y]
        bad = [f"{x}={y}" for x, y in pairs if x not in fc or y not in tc]
        if not pairs or bad:
            errors.append(f"{label}: kolon(lar) yok: {bad or 'columns boş'}")
            continue
        if (f, t, tuple(pairs)) in existing:
            errors.append(f"{label}: bu ilişki modelde zaten var")
            continue
        entry = {"id": rel_id(f, t, pairs) if scope == "global" else rel_id(f, t, pairs).replace("model:", "report:", 1),
                 "from_table": f, "to_table": t, "pairs": [list(p) for p in pairs], "cardinality": card,
                 "description": str(r.get("description") or "")[:300], "added_by": s.owner or "", "added_at": now_iso(),
                 "session_id": s.id, "source": "agent+kullanıcı onayı"}
        if scope == "global":
            reg.add(entry)
            dd.relationships.append(to_relationship(entry))
        else:
            s.model_relationships = [e for e in s.model_relationships if e["id"] != entry["id"]] + [entry]
        existing.add((f, t, tuple(pairs)))
        saved.append(f"{dd.tables[f].display_name or f} → {dd.tables[t].display_name or t} "
                     f"({', '.join(f'{x}={y}' for x, y in pairs)}, {card})")
    if saved:
        from app.harness.agent import Audit
        Audit(ctx.services.settings.audit_log).write(event="model_relationship_add", session=s.id, user=s.owner, scope=scope,
                                                     relationships=saved)
    if not saved:
        return ToolResult(False, {"error": "; ".join(errors) or "Kaydedilecek ilişki yok."}, "İlişki kaydedilmedi")
    note = ("İlişkiler ORTAK modele eklendi: filtreler bu tablolar arasında yayılır; sonraki raporlar da kullanır."
            if scope == "global" else "İlişkiler YALNIZ BU RAPORA eklendi: bu raporun filtreleri (ve Vitrin'deki yayını) kullanır; "
            "başka raporlara taşınmaz.")
    return ToolResult(True, {"saved": saved, "scope": scope, **({"errors": errors} if errors else {}), "note": note},
                      f"{len(saved)} ilişki {'ortak modele' if scope == 'global' else 'rapor modeline'} eklendi", state_changed=True)


def h_find_metrics(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    m = ctx.services.dictionary.search_metrics(str(a.get("query") or ""), 8)
    return ToolResult(True, {"metrics": m}, f"{len(m)} metrik tanımı")


def h_run_sql(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    sql = str(a.get("sql") or "")
    res, errors, _, warnings = _run_validated(ctx, sql, ctx.services.settings.preview_rows)
    if errors or res is None:
        if (hint := _session_id_hint(ctx, sql)):
            return ToolResult(False, {"ok": False, "errors": errors[:1], "hint": hint}, "Bu bir dataset / görsel kimliği")
        return ToolResult(False, {"ok": False, "errors": errors, "hint": "Hatayı düzeltip tekrar deneyin."},
                          "SQL reddedildi / hata: " + errors[0][:80])
    out = {"ok": True, "profile": profile(res), "rows": _rows_preview(res, 20), "elapsed_ms": res.elapsed_ms}
    if warnings:
        out["join_warnings"] = warnings
    return ToolResult(True, out,
                      f"{len(res.rows)}{'+' if res.truncated else ''} satır, {res.elapsed_ms} ms")


def h_save_requirements(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    try:
        req = Requirements.model_validate(a)
    except ValidationError as e:
        return ToolResult(False, {"errors": [f"{err['loc']}: {err['msg']}" for err in e.errors()]}, "Gereksinim hatalı")
    if not req.kpis:
        return ToolResult(False, {"errors": ["En az bir KPI/ölçü belirtin (kpis)."]}, "KPI eksik")
    s = ctx.session
    s.requirements = req
    if s.title_locked:  # kullanıcı adı elle verdiyse agent değiştirmez
        req.report_title = s.title
    else:
        s.title = req.report_title or s.title
    return ToolResult(True, {"ok": True}, f"Gereksinimler kaydedildi: {req.report_title}", state_changed=True,
                      next_phase="data",
                      kickoff="Gereksinimler kaydedildi. Şimdi veri keşfi fazındasın: veri sözlüğünü kullanarak gerekli tabloları bul, "
                              "SQL'leri test et ve dashboard için dataset'leri save_datasets ile kaydet. Kullanıcıya soru sormadan başla.")


def _save_datasets(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    items = a.get("datasets")
    if not isinstance(items, list) or not items:
        return ToolResult(False, {"errors": ["'datasets' boş olmayan bir liste olmalı."]}, "Dataset listesi boş")
    ids = [str(d.get("id")) for d in items if isinstance(d, dict)]
    if len(set(ids)) != len(ids):
        return ToolResult(False, {"errors": ["Dataset id'leri benzersiz olmalı."]}, "Tekrarlanan id")
    s = ctx.session
    built, profiles, errors, failed = [], {}, [], []
    for raw in items:
        ds, prof, errs = _build_dataset(ctx, raw if isinstance(raw, dict) else {})
        if errs:
            errors += errs
            failed.append(str((raw or {}).get("id")))
        else:
            built.append(ds)
            profiles[ds.id] = prof
    # kısmi kayıt: geçerli olanlar hemen kaydedilir, model yalnız hatalıları yeniden gönderir
    saved = {d.id: d for d in s.datasets}
    for d in built:
        saved[d.id] = d
        s.dataset_profiles[d.id] = profiles[d.id]
    s.datasets = list(saved.values())
    if errors:
        fails = s.phase_memory.get("save_failures", 0) + 1
        s.phase_memory["save_failures"] = fails
        if fails >= 3 and len(s.datasets) >= 3:
            # ilerleme garantisi: yeterli dataset var, inatçı hatalı kırılımı atla
            s.phase_memory["skipped"] = failed
            return ToolResult(True, {"ok": True, "saved": list(saved), "skipped": failed,
                                     "note": "Hatalı dataset'ler atlandı; kaydedilenlerle tasarım fazına geçiliyor."},
                              f"{len(s.datasets)} dataset kaydedildi, {len(failed)} hatalı atlandı ({', '.join(failed)})",
                              state_changed=True, next_phase="design", kickoff=_DESIGN_KICKOFF +
                              f" Not: şu dataset'ler hata nedeniyle atlandı: {', '.join(failed)} — kullanıcıya bunu da söyle.")
        return ToolResult(False, {"ok": False, "errors": errors, "saved": list(saved),
                                  "hint": "Geçerli dataset'ler KAYDEDİLDİ. Yalnızca hatalı olanları düzeltip tekrar gönderin "
                                          "(ya da gerekli değilse atlayın ve kalanları göndermeden bırakın)."},
                          f"{len(built)} kaydedildi, {len(failed)} hatalı ({', '.join(failed)})", state_changed=bool(built))
    return ToolResult(True, {"ok": True, "profiles": profiles, "saved": list(saved)},
                      f"{len(built)} dataset kaydedildi: {', '.join(d.id for d in built)}",
                      state_changed=True, next_phase="design", kickoff=_DESIGN_KICKOFF)


def add_join_relationships(ctx: ToolContext) -> list[str]:
    """Kaydedilen dataset'lerin SQL JOIN'lerinden rapora özel ilişkiler (Model sekmesi + filtre yayılımı)."""
    from app.dictionary.model_rels import derive_from_sql
    s = ctx.session
    try:
        new = derive_from_sql(ctx.services.dictionary, ctx.services.connector, ctx.services.validator.join_guard,
                              s.datasets, s.model_relationships)
    except Exception as e:  # noqa: BLE001 — ilişki çıkarılamasa da dataset kaydı geçerli
        import logging
        logging.getLogger(__name__).warning("JOIN ilişkileri çıkarılamadı: %s", e)
        return []
    s.model_relationships = s.model_relationships + new
    s.joins_derived = True
    dd = ctx.services.dictionary
    return [f"{dd.tables[e['from_table']].display_name or e['from_table']} → {dd.tables[e['to_table']].display_name or e['to_table']} "
            f"({', '.join(f'{x}={y}' for x, y in e['pairs'])}, {e['cardinality']})" for e in new]


def _with_join_rels(ctx: ToolContext, result: ToolResult) -> ToolResult:
    if not result.state_changed:
        return result
    added = add_join_relationships(ctx)
    if added and isinstance(result.content, dict):
        result.content["join_relationships"] = added
        result.content["join_note"] = ("SQL'deki JOIN'lerden rapora özel ilişkiler eklendi (Model sekmesinde görünür, filtreler "
                                       "bu tablolar arasında yayılır).")
        result.summary += f" · {len(added)} JOIN ilişkisi"
    return result


def h_save_datasets(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    return _with_join_rels(ctx, _save_datasets(ctx, a))


def h_add_dataset(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    return _with_join_rels(ctx, _add_dataset(ctx, a))


_DESIGN_KICKOFF = ("Dataset'ler kaydedildi ve tasarım fazına geçildi. Kullanıcıya (1) veriden öne çıkan 3-5 bulguyu "
                   "sayılarla kısaca özetle, (2) hangi dataset'lerin hazır olduğunu söyle, (3) nasıl bir tasarım "
                   "istediğini sor: tarif edebilir, örnek bir dashboard görseli yükleyebilir ya da 'varsayılan "
                   "tasarımla başla' diyebilir. Kullanıcı tasarımı zaten tarif ettiyse doğrudan create_report_spec ile oluştur.")


def _add_dataset(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    ds, prof, errs = _build_dataset(ctx, a)
    if errs:
        return ToolResult(False, {"ok": False, "errors": errs}, "Dataset hatalı")
    s = ctx.session
    s.datasets = [d for d in s.datasets if d.id != ds.id] + [ds]
    s.dataset_profiles[ds.id] = prof
    if s.spec:
        s.spec.datasets = [d for d in s.spec.datasets if d.id != ds.id] + [ds]
        s.spec_version += 1
    return ToolResult(True, {"ok": True, "profile": prof}, f"Dataset eklendi: {ds.id}", state_changed=True)


def h_create_report_spec(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    raw = a.get("spec") if isinstance(a.get("spec"), dict) else a
    spec, errs, notes = _validate_spec(ctx, raw)
    if errs:
        return ToolResult(False, {"ok": False, "errors": errs, "notes": notes}, f"Spec geçersiz ({len(errs)} hata)")
    return _spec_ok(ctx, spec, notes, "Dashboard oluşturuldu")


def _require_spec(ctx: ToolContext) -> ToolResult | None:
    if not ctx.session.spec:
        return ToolResult(False, {"error": "Henüz spec yok. Önce create_report_spec kullanın."}, "Spec yok")
    return None


def h_update_visual(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    if (r := _require_spec(ctx)):
        return r
    changes = a.get("changes") or {}
    # "tüm KPI kartları" gibi isteklerde aynı değişiklik birden çok görsele: ids (model tek tek çağırmayı unutuyordu)
    ids = [str(x) for x in (a.get("ids") or []) if x] or [a.get("id")]
    base = ctx.session.spec.model_dump(exclude_none=True)
    missing = [i for i in ids if not any(v["id"] == i for v in base["visuals"])]
    if missing:
        return ToolResult(False, {"error": f"Visual {missing} yok. Mevcut: {[v['id'] for v in base['visuals']]}"}, "Visual yok")
    for vid in ids:
        idx = next(i for i, v in enumerate(base["visuals"]) if v["id"] == vid)
        ch = copy.deepcopy(changes)
        if "type" in ch and ch["type"] != base["visuals"][idx]["type"]:
            # tip değişince tipe özgü eski encoding'ler kafa karıştırmasın
            ch.setdefault("encoding", {})
        base["visuals"][idx] = _deep_merge(base["visuals"][idx], ch)
    spec, errs, notes = _validate_spec(ctx, base)
    if errs:
        return ToolResult(False, {"ok": False, "errors": errs}, "Güncelleme geçersiz")
    prev = ctx.session.spec
    if prev is not None and prev.model_dump() == spec.model_dump() and _has_supported(changes) \
            and not any(n.startswith("KURAL") for n in notes):
        # istenen (desteklenen) değişiklik zaten uygulanmış: başarısız deme — model "olmadı" sanıp aynı çağrıyı her turda
        # tekrarlıyor ve sonraki istekleri de bozuyordu
        ignored = [n for n in notes if n.startswith(_IGNORED)]
        return ToolResult(True, {"ok": True, "unchanged": True, "note": (
            f"Görsel(ler) ZATEN istenen durumda ({', '.join(ids)}); dashboard değişmedi çünkü değiştirilecek bir şey yoktu. "
            "Bu çağrıyı TEKRARLAMA; kullanıcıya mevcut durumu söyle." + (" " + " ".join(ignored) if ignored else ""))},
            f"Zaten uygulanmış — değişiklik yok ({', '.join(ids)})")
    return _spec_ok(ctx, spec, notes, f"'{', '.join(ids)}' güncellendi")


def _has_supported(changes: dict[str, Any]) -> bool:
    """Değişiklikte şemada olan (uygulanabilir) en az bir alan var mı? (yalnız uydurma alan varsa False)"""
    from app.spec.models import Visual, VisualOptions
    top = {k for k in changes if k != "options" and k in Visual.model_fields}
    opts = changes.get("options") if isinstance(changes.get("options"), dict) else {}
    return bool(top or {k for k in opts if k in VisualOptions.model_fields})


def h_add_visual(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    if (r := _require_spec(ctx)):
        return r
    visual = a.get("visual") if isinstance(a.get("visual"), dict) else a
    base = ctx.session.spec.model_dump(exclude_none=True)
    if any(v["id"] == visual.get("id") for v in base["visuals"]):
        return ToolResult(False, {"error": f"'{visual.get('id')}' zaten var; update_visual kullanın."}, "Tekrarlanan id")
    base["visuals"].append(visual)
    spec, errs, notes = _validate_spec(ctx, base)
    if errs:
        return ToolResult(False, {"ok": False, "errors": errs}, "Visual geçersiz")
    return _spec_ok(ctx, spec, notes, f"'{visual.get('id')}' eklendi")


def h_remove_visual(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    if (r := _require_spec(ctx)):
        return r
    ids = a.get("ids") or ([a["id"]] if a.get("id") else [])
    base = ctx.session.spec.model_dump(exclude_none=True)
    before = len(base["visuals"])
    base["visuals"] = [v for v in base["visuals"] if v["id"] not in ids]
    if len(base["visuals"]) == before:
        return ToolResult(False, {"error": f"Silinecek visual bulunamadı: {ids}"}, "Visual yok")
    spec, errs, notes = _validate_spec(ctx, base)
    if errs:
        return ToolResult(False, {"ok": False, "errors": errs}, "Silme sonrası spec geçersiz")
    return _spec_ok(ctx, spec, notes, f"{before - len(spec.visuals)} görsel silindi")


def h_add_page(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    """Yeni sayfa (Power BI sayfası gibi). İlk kez sayfa eklenince mevcut görseller 'Genel Bakış' sayfasında kalır."""
    if (r := _require_spec(ctx)):
        return r
    pid, title = str(a.get("id") or "").strip(), str(a.get("title") or "").strip()
    base = ctx.session.spec.model_dump(exclude_none=True)
    pages = list(base.get("pages") or [])
    if not pages:   # tek sayfalı rapor: mevcut görseller ilk sayfa olur
        first = "genel" if pid != "genel" else "ozet"
        # model yeni sayfanın adını first_title'a da yazabiliyor: iki sekme aynı adı taşımasın
        first_title = str(a.get("first_title") or "").strip()
        if not first_title or first_title.casefold() == (title or pid).casefold():
            first_title = "Genel Bakış"
        pages = [{"id": first, "title": first_title}]
        for v in base["visuals"]:
            v["page"] = first
    if any(p["id"] == pid for p in pages):
        return ToolResult(False, {"error": f"'{pid}' sayfası zaten var. Mevcut: {[p['id'] for p in pages]}"}, "Tekrarlanan sayfa")
    pages.append({"id": pid, "title": title or pid})
    base["pages"] = pages
    existing = {v["id"] for v in base["visuals"]}
    for v in a.get("visuals") or []:   # isteğe bağlı: sayfanın görselleri birlikte
        if isinstance(v, dict) and v.get("id") in existing:
            a.setdefault("move_visuals", []).append(v["id"])   # mevcut görselin tanımı verilmiş: taşıma sayılır
        elif isinstance(v, dict):
            base["visuals"].append({**v, "page": pid})
    # isteğe bağlı: mevcut görselleri yeni sayfaya taşı ("yeni sayfa ekle ve X'i oraya taşı" tek adımda)
    # model taşınacak görselleri "visuals": ["product_table", …] diye de verebiliyor: metinler taşıma sayılır
    move = [str(x) for x in [*(a.get("move_visuals") or []), *[x for x in (a.get("visuals") or []) if isinstance(x, str)]] if x]
    unknown = [x for x in move if not any(v["id"] == x for v in base["visuals"])]
    if unknown:
        return ToolResult(False, {"error": f"Görsel bulunamadı: {unknown}. Mevcut: {[v['id'] for v in base['visuals']]}"},
                          "Taşınacak görsel yok")
    for v in base["visuals"]:
        if v["id"] in move:
            v["page"] = pid
    spec, errs, notes = _validate_spec(ctx, base)
    if errs:
        return ToolResult(False, {"ok": False, "errors": errs}, "Sayfa eklenemedi")
    moved = f"; taşınan: {', '.join(move)}" if move else ""
    if not move:
        notes.append("Bu sayfaya hiçbir görsel taşınmadı. Kullanıcı görsel taşımak istediyse ŞİMDİ update_visual "
                     "(ids=[...], changes={'page': '" + pid + "'}) çağır; taşımadan 'taşındı' deme.")
    return _spec_ok(ctx, spec, notes, f"'{title or pid}' sayfası eklendi{moved}")


def h_remove_page(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    """Sayfayı siler; görselleri move_to verilirse o sayfaya taşınır, yoksa silinir. Tek sayfa kalırsa sekmeler kalkar."""
    if (r := _require_spec(ctx)):
        return r
    pid, move_to = str(a.get("id") or ""), a.get("move_to")
    base = ctx.session.spec.model_dump(exclude_none=True)
    pages = list(base.get("pages") or [])
    if not any(p["id"] == pid for p in pages):
        return ToolResult(False, {"error": f"'{pid}' sayfası yok. Mevcut: {[p['id'] for p in pages]}"}, "Sayfa yok")
    rest = [p for p in pages if p["id"] != pid]
    if move_to and not any(p["id"] == move_to for p in rest):
        return ToolResult(False, {"error": f"Taşınacak sayfa '{move_to}' yok. Mevcut: {[p['id'] for p in rest]}"}, "Sayfa yok")
    first = pages[0]["id"]
    kept = []
    for v in base["visuals"]:
        on = v.get("page") or first
        if on == pid:
            if move_to:
                kept.append({**v, "page": move_to, "position": {**(v.get("position") or {}), "y": 999}})  # sona yerleşir
            continue
        kept.append(v)
    base["visuals"], base["pages"] = kept, (rest if len(rest) > 1 else [])
    if len(rest) <= 1:
        for v in base["visuals"]:
            v.pop("page", None)
    spec, errs, notes = _validate_spec(ctx, base)
    if errs:
        return ToolResult(False, {"ok": False, "errors": errs}, "Sayfa silinemedi")
    return _spec_ok(ctx, spec, notes, f"'{pid}' sayfası silindi")


_MODE_COLORS = ("background", "surface", "text", "mutedText", "border")


def _match_pages(current: list[dict[str, Any]], a: dict[str, Any]) -> str | None:
    """update_report(pages): model sayfa kimliğini bilmeden yazabiliyor (ör. ilk sayfa 'genel' iken 'page1'). Aynı sayıda
    sayfa verildiyse bilinmeyen kimlikler sıraya göre mevcut sayfalarla eşleştirilir (yeniden adlandırma / sıralama)."""
    new = a.get("pages")
    if not isinstance(new, list) or not current or len(new) != len(current):
        return None
    ids = {p["id"] for p in current}
    fixed = []
    for i, p in enumerate(new):
        if isinstance(p, dict) and p.get("id") not in ids and current[i]["id"] not in {q.get("id") for q in new if isinstance(q, dict)}:
            fixed.append(f"{p.get('id')}→{current[i]['id']}")
            p["id"] = current[i]["id"]
    return f"Sayfa kimlikleri sıraya göre eşleştirildi ({', '.join(fixed)}); mevcut kimlikleri kullan." if fixed else None


def _merge_pages(current: list[dict[str, Any]], a: dict[str, Any]) -> str | None:
    """update_report(pages) listeyi değiştirir; model yalnız adını değiştireceği sayfayı yazınca diğer sayfalar sessizce
    siliniyordu. Verilmeyen sayfalar korunur (sayfa silmek remove_page ile)."""
    new = a.get("pages")
    if not isinstance(new, list) or not current:
        return None
    given = {p.get("id") for p in new if isinstance(p, dict)}
    kept = [p for p in current if p["id"] not in given]
    if not kept:
        return None
    a["pages"] = [*new, *kept]
    return (f"Listede olmayan sayfalar korundu ({', '.join(p['title'] for p in kept)}); sayfa silmek için remove_page kullan.")


def h_update_report(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    if (r := _require_spec(ctx)):
        return r
    base = ctx.session.spec.model_dump(exclude_none=True)
    renamed = _match_pages(base.get("pages") or [], a)
    kept = _merge_pages(base.get("pages") or [], a)
    for k in ("title", "subtitle", "filters", "layout", "pages"):
        if k in a:
            base[k] = _deep_merge(base.get(k) or {}, a[k]) if k == "layout" else a[k]
    if isinstance(a.get("theme"), dict):
        old = base.get("theme") or Theme().model_dump()
        if a["theme"].get("mode") and a["theme"]["mode"] != old.get("mode"):
            # Mod değişti: verilmeyen zemin / yazı renkleri eski moddan kalmasın, yeni modun varsayılanları gelsin
            old = {k: v for k, v in old.items() if k not in _MODE_COLORS or k in a["theme"]}
        base["theme"] = _deep_merge(old, a["theme"])
    spec, errs, notes = _validate_spec(ctx, base)
    if errs:
        return ToolResult(False, {"ok": False, "errors": errs}, "Güncelleme geçersiz")
    notes += [n for n in (renamed, kept) if n]
    extra = sorted(set(a) - {"title", "subtitle", "filters", "layout", "pages", "theme"})
    if extra and not any(n.startswith(_IGNORED) for n in notes):
        notes.insert(0, f"{_IGNORED}: {', '.join(extra)}. update_report yalnız title, subtitle, filters, layout, pages, theme alır.")
    return _spec_ok(ctx, spec, notes, "Rapor ayarları güncellendi")


# --------------------------------------------------------------------------- şemalar
_STR = {"type": "string"}
_STRS = {"type": "array", "items": {"type": "string"}}
_POSITION = {"type": "object", "description": "12 kolonluk ızgara. x:0-11, w:1-12, y ve h satır birimi. Verilmezse otomatik yerleşir.",
             "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}, "w": {"type": "integer"}, "h": {"type": "integer"}}}
_VISUAL = {
    "type": "object",
    "required": ["id", "type", "title"],
    "properties": {
        "id": _STR,
        "type": {"type": "string", "enum": ["kpi", "line", "area", "bar", "pie", "donut", "table", "scatter", "heatmap",
                                            "funnel", "gauge", "treemap", "combo", "text", "matrix"]},
        "title": _STR, "subtitle": _STR, "datasetId": _STR,
        "encoding": {"type": "object", "description": "Alan adları dataset kolon adlarıyla birebir aynı olmalı.",
                     "properties": {"x": _STR, "y": _STRS, "series": _STR, "category": _STR, "value": _STR, "columns": _STRS,
                                    "rows": {**_STRS, "description": "matrix: satır boyutları, 1-2 seviye (ör. [bölge, şube])"},
                                    "columnDim": {**_STR, "description": "matrix: sütun boyutu; değerleri veriden sütun olur (ör. ay)"},
                                    "values": {**_STRS, "description": "matrix: ölçüler, bir ya da birden çok"}}},
        "options": {"type": "object", "description": "stacked, horizontal, smooth, showLabels, showLegend, format(number|currency|percent|compact), "
                                                     "decimals, sort(asc|desc), limit, aggregate, deltaField (hazır değişim oranı kolonu), "
                                                     "compareField (kpi: önceki dönem değeri kolonu), deltaLabel, "
                                                     "sparklineDatasetId, sparklineField, target, text, color (hex ya da renk adı; kpi'da değer+şerit rengi), "
                                                     "KPI kart stili: background (kart zemini), textColor — kullanıcı renk ADI "
                                                     "söylediyse (lacivert, mavi…) adı olduğu gibi yaz, sistem hex'e çevirir; "
                                                     "valueSize(sm|md|lg|xl), accentBar (true: solda renkli şerit). "
                                                     "Listede olmayan alan (ör. fontSize, labelPosition) DESTEKLENMEZ ve uygulanmaz; "
                                                     "ignoreFilters (true: görsel filtrelerden etkilenmez — YALNIZ kullanıcı "
                                                     "açıkça isterse; varsayılan: filtreler tüm görselleri etkiler). "
                                                     "matrix: rowTotals, columnTotals, subtotals (varsayılan true), "
                                                     "aggregate(sum|avg|count), maxColumns, conditionalColor (true: hücre zemini "
                                                     "değere göre renklenir)"},
        "position": _POSITION,
        "page": {"type": "string", "description": "Sayfa id'si (birden çok sayfa varsa). Boşsa ilk sayfa."},
    },
}
_PAGES = {"type": "array", "description": "Sayfalar (Power BI sayfaları gibi, sırasıyla sekme). Tek sayfalı raporda boş bırak.",
          "items": {"type": "object", "required": ["id", "title"], "properties": {"id": _STR, "title": _STR}}}
_THEME = {"type": "object", "description": "mode(light|dark), palette(hex listesi, >=3), background, surface, text, mutedText, accent, border (hex), "
                                           "fontFamily, radius(px), cardStyle(flat|outlined|elevated), density(compact|comfortable), headerStyle(plain|banner)"}
_FILTERS = {"type": "array", "description": "Dilimleyiciler. table+column bir BOYUT tablosunun kolonu olmalı (ör. dbo.DimSalesTerritory / "
                                            "SalesTerritoryGroup); sistem filtreyi ilişkiler üzerinden tüm görsellere yayar.",
            "items": {"type": "object", "required": ["id", "label", "table", "column"],
                      "properties": {"id": _STR, "label": _STR, "table": _STR, "column": _STR,
                                     "type": {"type": "string", "enum": ["select", "multiselect"]}}}}
_DATASET = {"type": "object", "required": ["id"],
            "properties": {"id": {"type": "string", "description": "snake_case"}, "description": _STR,
                           "sql": {"type": "string", "description": "SELECT sorgusu (ya da sql yerine verified kullanın)"},
                           "verified": {"type": "integer", "description": "Çalışma hafızasındaki doğrulanmış sorgunun numarası (sql yazmadan)"},
                           "fields": {"type": "array", "description": "İsteğe bağlı etiket/format: [{name,label,format}]",
                                      "items": {"type": "object", "properties": {"name": _STR, "label": _STR, "format": _STR}}}}}

ALL: tuple[Phase, ...] = ("requirements", "data", "design")
DATA_DESIGN: tuple[Phase, ...] = ("data", "design")

TOOLS: list[Tool] = [
    Tool("search_dictionary", "Veri sözlüğünde iş terimiyle tablo/kolon/metrik arar (Türkçe veya İngilizce). Hangi verinin var olduğunu öğrenmenin tek yolu.",
         {"type": "object", "required": ["query"], "properties": {"query": {"type": "string", "description": "ör. 'kredi kartı satış tutarı bölge'"},
                                                                   "limit": {"type": "integer"}}},
         ALL, h_search_dictionary, status="Veri sözlüğü aranıyor…"),
    Tool("get_table_details", "Tabloların tüm kolonlarını, rollerini (measure/dimension/key), örnek değerlerini ve join ilişkilerini getirir.",
         {"type": "object", "required": ["tables"], "properties": {"tables": {**_STRS, "description": "şema.tablo adları, en fazla 5"}}},
         ALL, h_get_table_details, status="Tablo detayları okunuyor…"),
    Tool("discover_object", "Nesne keşfi: tablo / view'ın veritabanındaki TEKNİK özelliklerini getirir — kolon SQL tipi "
         "(uzunluk, hassasiyet), NULL olabilir mi, identity / hesaplanan / varsayılan değer, birincil anahtar, index'ler, "
         "yabancı anahtarlar (giden ve gelen), satır sayısı, oluşturma / değişiklik tarihi, açıklama; view ise kaynak nesneleri "
         "ve SQL tanımı. JOIN anahtarının tekilliğini, tarih kolonunun tipini, NULL riskini veya bir view'ın neyi okuduğunu "
         "doğrulamak için kullan. Veri satırı okumaz.",
         {"type": "object", "required": ["objects"], "properties": {
             "objects": {**_STRS, "description": "şema.nesne (ek veritabanında db.şema.nesne) adları, en fazla 3"}}},
         ALL, h_discover_object, status="Nesne özellikleri veritabanı kataloğundan okunuyor…"),
    Tool("propose_model", "Veri modeli önerisi: bulduğun tabloların özetini (fact / boyut / view, satır sayısı, DataDate, PK) ve "
         "aralarındaki ilişki adaylarını (veritabanı yabancı anahtarları; aynı adlı anahtar kolonları + hangi tarafın tekil "
         "olduğu) getirir. Veri fazının başında, SQL yazmadan önce çağır; sonucu kullanıcıya sorup onay al.",
         {"type": "object", "required": ["tables"], "properties": {
             "tables": {**_STRS, "description": "search_dictionary ile bulduğun aday tablolar (şema.nesne), en fazla 8"}}},
         DATA_DESIGN, h_propose_model, status="Tablolar arası ilişkiler inceleniyor…"),
    Tool("save_relationships", "Kullanıcının ONAYLADIĞI ilişkileri ortak veri modeline kaydeder: filtreler bu ilişkilerden yayılır, "
         "sonraki raporlar da kullanır. Yalnız kullanıcı onay verdikten sonra çağır (sistem onaysız kaydı reddeder). "
         "Yön: from_table çok (N) tarafı, to_table tek (1) tarafı.",
         {"type": "object", "required": ["scope", "relationships"], "properties": {
             "scope": {"type": "string", "enum": ["global", "report"],
                       "description": "kullanıcının seçimi: global = ortak model (tüm raporlar), report = yalnız bu rapor"},
             "relationships": {"type": "array", "items": {
             "type": "object", "required": ["from_table", "to_table", "columns"],
             "properties": {"from_table": _STR, "to_table": _STR,
                            "columns": {"type": "array", "description": "[[from_kolon, to_kolon], ...] (anlık görüntülerde tarih kolonu çifti dahil)",
                                        "items": {"type": "array", "items": _STR}},
                            "cardinality": {"type": "string", "enum": ["N:1", "1:1"]}, "description": _STR}}}}},
         DATA_DESIGN, h_save_relationships, status="İlişkiler modele kaydediliyor…"),
    Tool("find_metrics", "Kurumsal olarak tanımlı (onaylı) metrik formüllerini arar. Varsa bu formülleri kullan.",
         {"type": "object", "properties": {"query": _STR}}, DATA_DESIGN, h_find_metrics, status="Metrik tanımları aranıyor…"),
    Tool("run_sql", "Salt-okunur SELECT sorgusunu doğrular ve çalıştırır; ilk satırları ve kolon profilini döndürür. Keşif ve test için.",
         {"type": "object", "required": ["sql"], "properties": {"sql": _STR, "purpose": {"type": "string", "description": "Bu sorgu neyi test ediyor"}}},
         DATA_DESIGN, h_run_sql, status="SQL doğrulanıp çalıştırılıyor…"),
    Tool("save_requirements", "Rapor gereksinimleri netleşince kaydeder ve veri keşfi fazına geçer.",
         {"type": "object", "required": ["report_title", "business_goal", "kpis", "dimensions"],
          "properties": {"report_title": _STR, "business_goal": _STR, "audience": _STR, "kpis": _STRS, "dimensions": _STRS,
                         "time_range": _STR, "filters": _STRS, "notes": _STR}},
         ("requirements",), h_save_requirements, status="Gereksinimler kaydediliyor…"),
    Tool("save_datasets", "Dashboard'un kullanacağı dataset'leri (her biri bir SQL) doğrular, çalıştırır ve kaydeder; tasarım fazına geçer. "
                          "Her görsel türü için uygun şekilde toplulaştırılmış ayrı dataset'ler tasarla (ör. KPI özeti tek satır, aylık trend, bölge kırılımı).",
         {"type": "object", "required": ["datasets"], "properties": {"datasets": {"type": "array", "items": _DATASET}}},
         ("data",), h_save_datasets, status="Dataset'ler doğrulanıp kaydediliyor…"),
    Tool("add_dataset", "Tasarım sırasında yeni bir görsel için ek dataset ekler veya aynı id'li dataset'i değiştirir.",
         _DATASET, ("design",), h_add_dataset, status="Dataset ekleniyor…"),
    Tool("create_report_spec", "Dashboard'un tamamını (tema, filtreler, görseller, yerleşim) oluşturur veya baştan yazar. "
                               "datasets alanını yazma: visual.datasetId ile kayıtlı dataset'lere referans ver.",
         {"type": "object", "required": ["spec"], "properties": {"spec": {
             "type": "object", "required": ["title", "visuals"],
             "properties": {"title": _STR, "subtitle": _STR, "theme": _THEME, "filters": _FILTERS,
                            "layout": {"type": "object", "properties": {"rowHeight": {"type": "integer"}}},
                            "pages": _PAGES, "visuals": {"type": "array", "items": _VISUAL}}}}},
         ("design",), h_create_report_spec, status="Dashboard tasarlanıyor…"),
    Tool("update_visual", "Görseli kısmi olarak günceller (tip, başlık, encoding, options, position, page). Sadece değişen alanları "
                          "gönder; bir alanı silmek için null ver. Aynı değişikliği birden çok görsele uygulamak için (ör. tüm KPI "
                          "kartları) id yerine ids listesi ver.",
         {"type": "object", "required": ["changes"], "properties": {"id": _STR, "ids": _STRS, "changes": {"type": "object"}}},
         ("design",), h_update_visual, status="Görsel güncelleniyor…"),
    Tool("add_visual", "Dashboard'a yeni görsel ekler.", {"type": "object", "required": ["visual"], "properties": {"visual": _VISUAL}},
         ("design",), h_add_visual, status="Görsel ekleniyor…"),
    Tool("remove_visual", "Görsel(ler)i siler.", {"type": "object", "required": ["ids"], "properties": {"ids": _STRS}},
         ("design",), h_remove_visual, status="Görsel siliniyor…"),
    Tool("update_report", "Başlık, alt başlık, filtreler, satır yüksekliği, tema (kısmi) ya da sayfa listesini "
                          "(yeniden adlandırma / sıralama: pages tam liste) günceller.",
         {"type": "object", "properties": {"title": _STR, "subtitle": _STR, "filters": _FILTERS, "theme": _THEME,
                                           "layout": {"type": "object", "properties": {"rowHeight": {"type": "integer"}}},
                                           "pages": _PAGES}},
         ("design",), h_update_report, status="Rapor ayarları güncelleniyor…"),
    Tool("find_filter_column", "Filtre (dilimleyici) için kolon arar: ÖNCE dashboard'un kullandığı tablo / view'larda, orada "
                               "yoksa sözlükte. Her aday için filtrenin uygulanacağı ve uygulanamayacağı görselleri verir. "
                               "Filtre eklemeden önce kullan; sonra update_report (filters) ile ekle.",
         {"type": "object", "required": ["query"], "properties": {"query": {"type": "string", "description": "ör. bölge, şube, kanal"}}},
         ("design",), h_find_filter_column, status="Filtre alanı aranıyor…"),
    Tool("add_page", "Dashboard'a yeni sayfa (sekme) ekler; isteğe bağlı olarak görselleriyle birlikte. Rapor tek sayfalıysa "
                     "mevcut görseller ilk sayfada ('Genel Bakış', first_title ile değiştirilebilir) kalır. Mevcut görselleri yeni sayfaya "
                     "taşımak için move_visuals (görsel id listesi) ver; sonradan update_visual (changes.page) ile de taşınabilir.",
         {"type": "object", "required": ["id", "title"],
          "properties": {"id": _STR, "title": _STR, "first_title": _STR, "visuals": {"type": "array", "items": _VISUAL},
                         "move_visuals": {"type": "array", "items": _STR}}},
         ("design",), h_add_page, status="Sayfa ekleniyor…"),
    Tool("remove_page", "Sayfayı siler. move_to verilirse görselleri o sayfaya taşınır, verilmezse görselleri de silinir.",
         {"type": "object", "required": ["id"], "properties": {"id": _STR, "move_to": _STR}},
         ("design",), h_remove_page, status="Sayfa siliniyor…"),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def tools_for(phase: Phase) -> list[Tool]:
    return [t for t in TOOLS if phase in t.phases]


def to_llm_content(content: Any, limit: int) -> str:
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
    if len(text) > limit:
        text = text[:limit] + f"\n…(kısaltıldı, toplam {len(text)} karakter)"
    return text
