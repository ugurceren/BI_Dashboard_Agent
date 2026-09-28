"""Agent araçları. Model yalnızca bu araçlar üzerinden dünyaya dokunur.

Her araç: isim, açıklama, JSON şema, hangi fazlarda açık olduğu, risk seviyesi ve handler.
Handler'lar deterministiktir; hatalar modele düzeltmesi için açık bir mesajla döner.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import ValidationError

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
        return self.policies.get(role) or self.policies["analyst"]


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
    aliases = {t.alias_or_name.lower(): f"{t.db}.{t.name}".lower() for t in tree.find_all(exp.Table) if t.db}
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
                 "funnel": (4, 4), "treemap": (6, 4)}


def _layout_visuals(visuals: list[dict[str, Any]]) -> list[str]:
    """Eksik pozisyonları yerleştirir, taşan/çakışanları aşağı iter. Deterministik düzeltme = modele daha az yük."""
    notes: list[str] = []
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
    return notes


def _validate_spec(ctx: ToolContext, spec_raw: dict[str, Any]) -> tuple[ReportSpec | None, list[str], list[str]]:
    """Spec'i tamamlar (dataset'ler session'dan), yerleşimi düzeltir, doğrular."""
    s = ctx.session
    raw = copy.deepcopy(spec_raw)
    known = {d.id: d for d in s.datasets}
    if s.spec:
        for d in s.spec.datasets:
            known.setdefault(d.id, d)
    # Model dataset'leri tekrar yazmak zorunda değil: visual'larda kullanılanları session'dan ekle.
    visuals = [v for v in raw.get("visuals") or [] if isinstance(v, dict)]
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
    notes = _layout_visuals([v for v in raw.get("visuals") or [] if isinstance(v, dict)]) + _resolve_filters(ctx, raw)         + _fix_axes(raw)
    # para birimi belirtilmemiş tutar görsellerine kurum varsayılanını ver
    currency_fields = {f["name"] for d in raw["datasets"] for f in d.get("fields") or [] if f.get("format") == "currency"}
    for v in raw.get("visuals") or []:
        if not isinstance(v, dict):
            continue
        opts = v.setdefault("options", {}) if isinstance(v.get("options"), dict) or v.get("options") is None else {}
        enc = v.get("encoding") or {}
        used = {enc.get("value"), *(enc.get("y") or []), *(enc.get("columns") or [])}
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


def _filter_candidates(ctx: ToolContext, text: str) -> list[tuple[float, str, str, str]]:
    """Filtre etiketine/kolon adına en uygun BOYUT kolonları: (skor, tablo, kolon, iş adı)."""
    from app.dictionary.repository import _score, tokens

    dd, pol = ctx.services.dictionary, ctx.services.policy(ctx.session.user_role)
    qt = tokens(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text))
    kinds = dd._table_kinds()
    out = []
    for t in dd.tables.values():
        for c in t.columns:
            if c.role != "dimension" or (c.is_pii and not pol.allow_pii):
                continue
            sc = _score(qt, [(c.business_name, 3), (c.name, 2), (" ".join(c.synonyms), 2.5), (t.business_name, 1)])
            if sc <= 0:
                continue
            extra = len([w for w in tokens(c.business_name) if not any(w.startswith(q[:4]) or q.startswith(w[:4]) for q in qt)])
            sc = sc / (1 + 0.3 * extra) * (1.5 if kinds.get(t.name) == "dimension" else 0.5)  # filtre boyut tablosunda olmalı
            out.append((round(sc, 2), t.display_name or t.name, c.display_name or c.name, c.business_name))
    return sorted(out, reverse=True)[:4]


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


def _fix_axes(raw: dict[str, Any]) -> list[str]:
    """Ters verilmiş eksenleri ve anlamsız alan eşleşmelerini düzeltir (küçük modellerin tipik hataları)."""
    types = {d.get("id"): {f.get("name"): f.get("type") for f in d.get("fields") or []} for d in raw.get("datasets") or []}
    notes = []
    for v in raw.get("visuals") or []:
        if not isinstance(v, dict):
            continue
        t, enc = types.get(v.get("datasetId"), {}), v.get("encoding") or {}
        ys = enc.get("y") if isinstance(enc.get("y"), list) else ([enc["y"]] if enc.get("y") else [])
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
        if v.get("type") in ("pie", "donut", "funnel", "treemap") and enc.get("category") and enc.get("value")                 and t.get(enc["category"]) == "number" and t.get(enc["value"]) == "string":
            enc["category"], enc["value"] = enc["value"], enc["category"]
            notes.append(f"'{v.get('id')}': category ve value ters verilmişti, düzeltildi.")
        v["encoding"] = enc
    return notes


def _spec_ok(ctx: ToolContext, spec: ReportSpec, notes: list[str], what: str) -> ToolResult:
    ctx.session.set_spec(spec)
    return ToolResult(True, {"ok": True, "spec_version": ctx.session.spec_version,
                             "visuals": [v.id for v in spec.visuals], "notes": notes},
                      f"{what} (v{ctx.session.spec_version}, {len(spec.visuals)} görsel)", state_changed=True)


# --------------------------------------------------------------------------- handler'lar
def h_search_dictionary(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    q = str(a.get("query") or "")
    hits = ctx.services.dictionary.search(q, int(a.get("limit") or 6))
    metrics = ctx.services.dictionary.search_metrics(q, 3)
    return ToolResult(True, {"tables": hits, "governed_metrics": metrics},
                      f"'{q}' → {len(hits)} tablo, {len(metrics)} metrik")


def h_get_table_details(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    names = a.get("tables") or ([a["table"]] if a.get("table") else [])
    if isinstance(names, str):
        names = [names]
    pol = ctx.services.policy(ctx.session.user_role)
    out, missing = [], []
    for n in names[:5]:
        d = ctx.services.dictionary.table_details(str(n), pol.allow_pii)
        (out.append(d) if d else missing.append(n))
    if not out:
        return ToolResult(False, {"error": f"Tablo(lar) sözlükte yok: {missing}. search_dictionary kullanın."}, "Tablo bulunamadı")
    return ToolResult(True, {"tables": out, **({"not_found": missing} if missing else {})},
                      ", ".join(d["table"] for d in out))


def h_find_metrics(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    m = ctx.services.dictionary.search_metrics(str(a.get("query") or ""), 8)
    return ToolResult(True, {"metrics": m}, f"{len(m)} metrik tanımı")


def h_run_sql(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    sql = str(a.get("sql") or "")
    res, errors, _, warnings = _run_validated(ctx, sql, ctx.services.settings.preview_rows)
    if errors or res is None:
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
    s.title = req.report_title or s.title
    return ToolResult(True, {"ok": True}, f"Gereksinimler kaydedildi: {req.report_title}", state_changed=True,
                      next_phase="data",
                      kickoff="Gereksinimler kaydedildi. Şimdi veri keşfi fazındasın: veri sözlüğünü kullanarak gerekli tabloları bul, "
                              "SQL'leri test et ve dashboard için dataset'leri save_datasets ile kaydet. Kullanıcıya soru sormadan başla.")


def h_save_datasets(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
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


_DESIGN_KICKOFF = ("Dataset'ler kaydedildi ve tasarım fazına geçildi. Kullanıcıya (1) veriden öne çıkan 3-5 bulguyu "
                   "sayılarla kısaca özetle, (2) hangi dataset'lerin hazır olduğunu söyle, (3) nasıl bir tasarım "
                   "istediğini sor: tarif edebilir, örnek bir dashboard görseli yükleyebilir ya da 'varsayılan "
                   "tasarımla başla' diyebilir. Kullanıcı tasarımı zaten tarif ettiyse doğrudan create_report_spec ile oluştur.")


def h_add_dataset(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
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
    vid, changes = a.get("id"), a.get("changes") or {}
    base = ctx.session.spec.model_dump(exclude_none=True)
    idx = next((i for i, v in enumerate(base["visuals"]) if v["id"] == vid), None)
    if idx is None:
        return ToolResult(False, {"error": f"Visual '{vid}' yok. Mevcut: {[v['id'] for v in base['visuals']]}"}, "Visual yok")
    if "type" in changes and changes["type"] != base["visuals"][idx]["type"]:
        # tip değişince tipe özgü eski encoding'ler kafa karıştırmasın
        changes.setdefault("encoding", {})
    base["visuals"][idx] = _deep_merge(base["visuals"][idx], changes)
    spec, errs, notes = _validate_spec(ctx, base)
    if errs:
        return ToolResult(False, {"ok": False, "errors": errs}, "Güncelleme geçersiz")
    return _spec_ok(ctx, spec, notes, f"'{vid}' güncellendi")


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


def h_update_report(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    if (r := _require_spec(ctx)):
        return r
    base = ctx.session.spec.model_dump(exclude_none=True)
    for k in ("title", "subtitle", "filters", "layout"):
        if k in a:
            base[k] = _deep_merge(base.get(k) or {}, a[k]) if k == "layout" else a[k]
    if isinstance(a.get("theme"), dict):
        base["theme"] = _deep_merge(base.get("theme") or Theme().model_dump(), a["theme"])
    spec, errs, notes = _validate_spec(ctx, base)
    if errs:
        return ToolResult(False, {"ok": False, "errors": errs}, "Güncelleme geçersiz")
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
                                            "funnel", "gauge", "treemap", "combo", "text"]},
        "title": _STR, "subtitle": _STR, "datasetId": _STR,
        "encoding": {"type": "object", "description": "Alan adları dataset kolon adlarıyla birebir aynı olmalı.",
                     "properties": {"x": _STR, "y": _STRS, "series": _STR, "category": _STR, "value": _STR, "columns": _STRS}},
        "options": {"type": "object", "description": "stacked, horizontal, smooth, showLabels, showLegend, format(number|currency|percent|compact), "
                                                     "decimals, sort(asc|desc), limit, aggregate, deltaField (hazır değişim oranı kolonu), "
                                                     "compareField (kpi: önceki dönem değeri kolonu), deltaLabel, "
                                                     "sparklineDatasetId, sparklineField, target, text, color"},
        "position": _POSITION,
    },
}
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
         DATA_DESIGN, h_get_table_details, status="Tablo detayları okunuyor…"),
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
                            "visuals": {"type": "array", "items": _VISUAL}}}}},
         ("design",), h_create_report_spec, status="Dashboard tasarlanıyor…"),
    Tool("update_visual", "Tek bir görseli kısmi olarak günceller (tip, başlık, encoding, options, position). Sadece değişen alanları gönder; bir alanı silmek için null ver.",
         {"type": "object", "required": ["id", "changes"], "properties": {"id": _STR, "changes": {"type": "object"}}},
         ("design",), h_update_visual, status="Görsel güncelleniyor…"),
    Tool("add_visual", "Dashboard'a yeni görsel ekler.", {"type": "object", "required": ["visual"], "properties": {"visual": _VISUAL}},
         ("design",), h_add_visual, status="Görsel ekleniyor…"),
    Tool("remove_visual", "Görsel(ler)i siler.", {"type": "object", "required": ["ids"], "properties": {"ids": _STRS}},
         ("design",), h_remove_visual, status="Görsel siliniyor…"),
    Tool("update_report", "Başlık, alt başlık, filtreler, satır yüksekliği veya tema (kısmi) günceller.",
         {"type": "object", "properties": {"title": _STR, "subtitle": _STR, "filters": _FILTERS, "theme": _THEME,
                                           "layout": {"type": "object", "properties": {"rowHeight": {"type": "integer"}}}}},
         ("design",), h_update_report, status="Rapor ayarları güncelleniyor…"),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def tools_for(phase: Phase) -> list[Tool]:
    return [t for t in TOOLS if phase in t.phases]


def to_llm_content(content: Any, limit: int) -> str:
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
    if len(text) > limit:
        text = text[:limit] + f"\n…(kısaltıldı, toplam {len(text)} karakter)"
    return text
