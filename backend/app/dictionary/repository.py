"""Veri sözlüğü: config/dictionary.toml'daki sorgularla yüklenir, bellekte aranır.

Sözlükler genelde birkaç bin satırdır; bu yüzden bellekte tutup Türkçe'ye duyarlı anahtar
kelime + eş anlamlı araması yapıyoruz. Vektör arama gerekirse `search` yerine takılabilir.
"""

from __future__ import annotations

import logging
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings, load_toml
from app.data.connector import Connector, create_connector

log = logging.getLogger(__name__)


@dataclass
class DDColumn:
    table: str
    name: str
    business_name: str
    description: str
    data_type: str
    role: str
    default_aggregation: str | None
    synonyms: list[str]
    is_pii: bool
    sample_values: str
    display_name: str = ""   # sözlükteki orijinal yazım (UI için)


@dataclass
class DDTable:
    name: str
    business_name: str
    description: str
    subject_area: str
    grain: str
    row_count: int | None
    columns: list[DDColumn] = field(default_factory=list)
    display_name: str = ""
    table_type: str = ""     # fact | dimension | bridge (sözlükte yoksa ilişkilerden çıkarılır)


@dataclass
class DDMetric:
    name: str
    business_name: str
    description: str
    expression_sql: str
    base_table: str
    value_format: str
    synonyms: list[str]


CARDINALITIES = ("N:1", "1:1", "1:N", "N:N")


@dataclass
class DDRelationship:
    """Bir ilişki = bir veya daha çok kolon çifti (bileşik anahtar). Yön her zaman 'çok' → 'tek' olacak şekilde normalize edilir."""

    id: str
    from_table: str
    to_table: str
    pairs: list[tuple[str, str]]          # [(from_column, to_column), ...]
    cardinality: str | None = None        # N:1 | 1:1 | N:N | None (bilinmiyor)
    role: str = ""                        # ör. "Sipariş tarihi" (aynı boyuta birden çok ilişki varsa)
    description: str = ""
    active: bool = True                   # Power BI'daki aktif ilişki: filtreler yalnız aktif ilişkilerden yayılır
    _active_explicit: bool = False

    @property
    def from_unique(self) -> bool | None:
        return None if self.cardinality is None else self.cardinality.startswith("1")

    @property
    def to_unique(self) -> bool | None:
        return None if self.cardinality is None else self.cardinality.endswith("1")

    def join_sql(self) -> str:
        return " AND ".join(f"{self.from_table}.{a} = {self.to_table}.{b}" for a, b in self.pairs)

    def describe(self) -> str:
        card = {"N:1": "çoktan bire", "1:1": "bire bir", "N:N": "çoktan çoka — doğrudan toplama yapma"}.get(
            self.cardinality or "", "kardinalite bilinmiyor")
        sides = ""
        if self.cardinality:
            sides = f" ({self.from_table.split('.')[-1]}: {'1' if self.from_unique else 'N'}, " \
                    f"{self.to_table.split('.')[-1]}: {'1' if self.to_unique else 'N'})"
        return (f"{self.join_sql()}  [{self.cardinality or '?'} {card}{sides}]" + (f" rol: {self.role}" if self.role else "")
                + ("" if self.active else " (pasif ilişki: filtre yaymaz)"))


_TR_MAP = str.maketrans({"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u", "â": "a", "î": "i", "û": "u"})
_STOP = {"ve", "ile", "icin", "bir", "bu", "su", "da", "de", "mi", "gibi", "olan", "gore", "bazinda", "bazli",
         "istiyorum", "rapor", "raporu", "dashboard", "goster", "analiz", "the", "of", "by", "and"}


def normalize(text: str) -> str:
    text = (text or "").replace("I", "ı").replace("İ", "i").lower()
    text = text.translate(_TR_MAP)
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", normalize(text)) if len(t) > 1 and t not in _STOP]


def _match(q: str, t: str) -> float:
    """Türkçe ekleri tolere eden eşleşme: 'satislari' ~ 'satis'."""
    if q == t:
        return 1.0
    short, long_ = (q, t) if len(q) <= len(t) else (t, q)
    if len(short) >= 4 and long_.startswith(short):
        return 0.8
    if len(short) >= 5 and long_.startswith(short[:-1]):
        return 0.6
    return 0.0


def _score(query_tokens: list[str], fields: list[tuple[str, float]]) -> float:
    total = 0.0
    for q in query_tokens:
        best = 0.0
        for text, weight in fields:
            for t in tokens(text):
                m = _match(q, t)
                if m:
                    best = max(best, m * weight)
        total += best
    return total


def _group_relationships(rows: list[dict[str, Any]]) -> list[DDRelationship]:
    """Satırları ilişkilere çevirir. Aynı relationship_id'ye sahip satırlar tek bir bileşik ilişkidir."""
    groups: dict[str, DDRelationship] = {}
    for i, r in enumerate(rows):
        ft, tt = str(r["from_table"]).lower(), str(r["to_table"]).lower()
        rid = str(r.get("relationship_id") or f"rel_{i}")
        pair = (str(r["from_column"]).lower(), str(r["to_column"]).lower())
        g = groups.get(rid)
        if g is None:
            card = str(r.get("cardinality") or "").upper().replace("*", "N").replace("M", "N").strip() or None
            if card not in CARDINALITIES:
                card = None
            g = groups[rid] = DDRelationship(rid, ft, tt, [], card, str(r.get("role") or ""), str(r.get("description") or ""))
            if r.get("is_active") is not None and str(r.get("is_active")).strip() != "":
                g.active = str(r.get("is_active")).strip().lower() in ("1", "true", "yes", "evet")
                g._active_explicit = True
        if pair not in g.pairs:
            g.pairs.append(pair)
    rels = list(groups.values())
    # Aynı iki tablo arasında birden çok ilişki varsa ve hiçbiri açıkça işaretlenmemişse ilki aktif olsun
    by_pair: dict[frozenset[str], list[DDRelationship]] = {}
    for rel in rels:
        by_pair.setdefault(frozenset((rel.from_table, rel.to_table)), []).append(rel)
    for group in by_pair.values():
        if len(group) > 1 and not any(g._active_explicit for g in group):
            for i, g in enumerate(group):
                g.active = i == 0
    for rel in rels:
        if rel.cardinality == "1:N":  # 'çok' tarafı her zaman from olsun
            rel.from_table, rel.to_table = rel.to_table, rel.from_table
            rel.pairs = [(b, a) for a, b in rel.pairs]
            rel.cardinality = "N:1"
    return rels


class DataDictionary:
    def __init__(self, settings: Settings, data_connector: Connector | None = None):
        self.settings = settings
        self._data_connector = data_connector
        self._lock = threading.Lock()
        self.tables: dict[str, DDTable] = {}
        self.metrics: list[DDMetric] = []
        self.relationships: list[DDRelationship] = []

    # ------------------------------------------------------------------ yükleme
    def _connector(self, cfg: dict) -> Connector:
        if cfg.get("source", "data") == "odbc":
            from app.data.connector import SqlServerConnector

            return SqlServerConnector(cfg["odbc"], self.settings.query_timeout_s)
        return self._data_connector or create_connector(self.settings)

    def load(self) -> "DataDictionary":
        cfg = load_toml(self.settings.dictionary_config)
        con = self._connector(cfg)
        q = cfg["queries"]

        def rows(sql: str | None) -> list[dict[str, Any]]:
            if not sql or not sql.strip():
                return []
            r = con.execute(sql, 100_000)
            return [dict(zip([c.lower() for c in r.columns], row)) for row in r.rows]

        tables: dict[str, DDTable] = {}
        for r in rows(q["tables"]):
            name = str(r["table_name"]).lower()
            tables[name] = DDTable(name, r.get("business_name") or name, r.get("description") or "",
                                   r.get("subject_area") or "", r.get("grain") or "", r.get("row_count"),
                                   display_name=str(r["table_name"]),
                                   table_type=str(r.get("table_type") or "").lower())
        for r in rows(q["columns"]):
            t = str(r["table_name"]).lower()
            if t not in tables:
                continue
            tables[t].columns.append(DDColumn(
                table=t, name=str(r["column_name"]).lower(), business_name=r.get("business_name") or r["column_name"],
                description=r.get("description") or "", data_type=r.get("data_type") or "",
                role=(r.get("column_role") or "attribute").lower(), default_aggregation=r.get("default_aggregation"),
                synonyms=[s.strip() for s in (r.get("synonyms") or "").split(",") if s.strip()],
                is_pii=bool(r.get("is_pii")), sample_values=r.get("sample_values") or "",
                display_name=str(r["column_name"]),
            ))
        rels = _group_relationships(rows(q.get("relationships")))
        if cfg.get("infer_cardinality", True) and any(r.cardinality is None for r in rels):
            self._infer_cardinality(rels, tables)
        metrics = [DDMetric(r["metric_name"], r.get("business_name") or r["metric_name"], r.get("description") or "",
                            r.get("expression_sql") or "", (r.get("base_table") or "").lower(), r.get("value_format") or "number",
                            [s.strip() for s in (r.get("synonyms") or "").split(",") if s.strip()])
                   for r in rows(q.get("metrics"))]
        with self._lock:
            self.tables, self.relationships, self.metrics = tables, rels, metrics
        return self

    def _infer_cardinality(self, rels: list[DDRelationship], tables: dict[str, DDTable]) -> None:
        """Sözlükte kardinalite yoksa veriden çıkarır: bir taraf, join kolonları o tabloda tekilse '1'dir.

        SQL Server: önce PK/unique index metadatasına bakılır (ucuz). Index yoksa ve tablo küçükse
        (≤ 2M satır) COUNT(DISTINCT) ile ölçülür; büyük tablolar 'N' (çok) kabul edilir.
        """
        con = self._data_connector or create_connector(self.settings)
        tsql = con.dialect == "tsql"
        q = (lambda n: "[" + n.replace("]", "]]") + "]") if tsql else (lambda n: '"' + n.replace('"', '""') + '"')
        unique_sets: dict[str, list[set[str]]] = {}
        if tsql:
            try:
                r = con.execute("""
                    SELECT LOWER(SCHEMA_NAME(t.schema_id) + '.' + t.name), i.index_id, LOWER(c.name)
                    FROM sys.indexes i
                    JOIN sys.tables t ON t.object_id = i.object_id
                    JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.is_included_column = 0
                    JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
                    WHERE i.is_unique = 1""", 200_000)
                idx: dict[tuple[str, int], set[str]] = {}
                for t, i, c in r.rows:
                    idx.setdefault((t, i), set()).add(c)
                for (t, _), cols in idx.items():
                    unique_sets.setdefault(t, []).append(cols)
            except Exception as e:  # noqa: BLE001
                log.warning("Unique index metadatası okunamadı: %s", e)
        cache: dict[tuple[str, frozenset[str]], bool | None] = {}

        def unique(table: str, cols: list[str]) -> bool | None:
            key = (table, frozenset(cols))
            if key in cache:
                return cache[key]
            res: bool | None = None
            if any(s <= set(cols) for s in unique_sets.get(table, [])):
                res = True
            else:
                rc = tables.get(table).row_count if tables.get(table) else None
                if rc is not None and rc > 2_000_000:
                    res = False
                else:
                    schema, _, name = table.partition(".")
                    t_sql = f"{q(schema)}.{q(name)}"
                    col_sql = ", ".join(q(c) for c in cols)
                    try:
                        n, d = con.execute(f"SELECT (SELECT COUNT(*) FROM {t_sql}), "
                                           f"(SELECT COUNT(*) FROM (SELECT DISTINCT {col_sql} FROM {t_sql}) x)", 1).rows[0]
                        res = n == d
                    except Exception as e:  # noqa: BLE001
                        log.warning("Kardinalite ölçülemedi (%s %s): %s", table, cols, e)
            cache[key] = res
            return res

        for rel in rels:
            if rel.cardinality is not None:
                continue
            fu = unique(rel.from_table, [a for a, _ in rel.pairs])
            tu = unique(rel.to_table, [b for _, b in rel.pairs])
            if fu is None or tu is None:
                continue
            if fu and not tu:  # 1:N → N:1 yönüne çevir
                rel.from_table, rel.to_table = rel.to_table, rel.from_table
                rel.pairs = [(b, a) for a, b in rel.pairs]
                fu, tu = tu, fu
            rel.cardinality = f"{'1' if fu else 'N'}:{'1' if tu else 'N'}"

    # ------------------------------------------------------------------ sorgular
    def has_table(self, name: str) -> bool:
        return name.lower() in self.tables

    def pii_columns(self, table: str) -> set[str]:
        t = self.tables.get(table.lower())
        return {c.name for c in t.columns if c.is_pii} if t else set()

    def relationships_for(self, table: str) -> list[DDRelationship]:
        t = table.lower()
        return [r for r in self.relationships if t in (r.from_table, r.to_table)]

    def search(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        qt = tokens(query)
        if not qt:
            return []
        results = []
        for t in self.tables.values():
            t_score = _score(qt, [(t.business_name, 3), (t.name, 2), (t.description, 1.2), (t.subject_area, 1)])
            col_hits = []
            for c in t.columns:
                s = _score(qt, [(c.business_name, 3), (c.name, 2), (" ".join(c.synonyms), 2.5), (c.description, 1)])
                if s > 0:
                    col_hits.append((s, c))
            col_hits.sort(key=lambda x: -x[0])
            score = t_score + sum(s for s, _ in col_hits[:4]) * 0.7
            # olgu (fact) tablolarını hafif öne al: ölçüler oradadır
            if score > 0 and any(c.role == "measure" for c in t.columns):
                score *= 1.15
            if score > 0:
                results.append({
                    "table": t.name, "business_name": t.business_name, "description": t.description,
                    "subject_area": t.subject_area, "score": round(score, 2),
                    "columns": [{"name": c.name, "business_name": c.business_name, "role": c.role,
                                 **({"pii": True} if c.is_pii else {})} for _, c in col_hits[:8]],
                })
        results.sort(key=lambda r: -r["score"])
        return results[:limit]

    def search_metrics(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        qt = tokens(query)
        scored = []
        for m in self.metrics:
            s = _score(qt, [(m.business_name, 3), (m.name, 2), (" ".join(m.synonyms), 2.5), (m.description, 1)]) if qt else 1
            if s > 0:
                scored.append((s, m))
        scored.sort(key=lambda x: -x[0])
        return [{"metric": m.name, "business_name": m.business_name, "description": m.description,
                 "expression_sql": m.expression_sql, "base_table": m.base_table, "format": m.value_format}
                for _, m in scored[:limit]]

    def table_details(self, name: str, include_pii: bool) -> dict[str, Any] | None:
        t = self.tables.get(name.lower())
        if not t:
            return None
        cols = []
        for c in t.columns:
            d: dict[str, Any] = {"name": c.name, "business_name": c.business_name, "type": c.data_type, "role": c.role}
            if c.description:
                d["description"] = c.description
            if c.default_aggregation:
                d["default_aggregation"] = c.default_aggregation
            if c.sample_values:
                d["sample_values"] = c.sample_values
            if c.is_pii:
                d["pii"] = True
                if not include_pii:
                    d["note"] = "Kişisel veri — sorgulanamaz"
            cols.append(d)
        return {
            "table": t.name, "business_name": t.business_name, "description": t.description,
            "grain": t.grain, "row_count": t.row_count, "columns": cols,
            "joins": [r.describe() for r in self.relationships_for(t.name)],
        }

    def _table_kinds(self) -> dict[str, str]:
        """fact / dimension / bridge. Sözlükte table_type varsa o; yoksa ilişki grafiğinden:
        köprü = hiçbir tablonun işaret etmediği ve başka bir 'merkez' tabloya (≥2 dış ilişkisi olan) işaret eden tablo;
        fact = köprüler dışında kimsenin işaret etmediği, kendisi en az bir tabloya işaret eden tablo; kalanlar boyut."""
        out: dict[str, set[str]] = {t: set() for t in self.tables}
        inc: dict[str, set[str]] = {t: set() for t in self.tables}
        for r in self.relationships:
            if r.from_table in out and r.to_table in inc:
                out[r.from_table].add(r.to_table)
                inc[r.to_table].add(r.from_table)
        bridges = {t for t in self.tables if len(out[t]) >= 2 and not inc[t] and any(len(out[x]) >= 2 for x in out[t])}
        kinds = {}
        for name, t in self.tables.items():
            if t.table_type in ("fact", "dimension", "bridge"):
                kinds[name] = t.table_type
            elif name in bridges:
                kinds[name] = "bridge"
            elif out[name] and not (inc[name] - bridges):
                kinds[name] = "fact"
            else:
                kinds[name] = "dimension"
        return kinds

    # ------------------------------------------------------------------ ilişkisel model (UI)
    def model(self) -> dict[str, Any]:
        """Tablolar, kolonlar ve ilişkiler: arayüzdeki model diyagramı için."""
        rels = self.relationships
        key_cols: dict[str, set[str]] = {}
        for r in rels:
            key_cols.setdefault(r.from_table, set()).update(a for a, _ in r.pairs)
            key_cols.setdefault(r.to_table, set()).update(b for _, b in r.pairs)
        kinds = self._table_kinds()
        out_tables = []
        for t in self.tables.values():
            kind = kinds[t.name]
            schema, _, short = (t.display_name or t.name).partition(".")
            out_tables.append({
                "id": t.name, "name": t.display_name or t.name, "schema": schema, "short_name": short or schema,
                "business_name": t.business_name, "description": t.description, "subject_area": t.subject_area,
                "grain": t.grain, "row_count": t.row_count, "kind": kind,
                "columns": [{"name": c.display_name or c.name, "id": c.name, "business_name": c.business_name,
                             "data_type": c.data_type, "role": c.role, "is_pii": c.is_pii,
                             "is_key": c.name in key_cols.get(t.name, set()), "description": c.description}
                            for c in t.columns],
            })

        def col_display(table: str, col: str) -> str:
            tt = self.tables.get(table)
            return next((c.display_name for c in tt.columns if c.name == col and c.display_name), col) if tt else col

        return {
            "tables": out_tables,
            "relationships": [{
                "id": r.id, "from_table": r.from_table, "to_table": r.to_table,
                "pairs": [[a, b] for a, b in r.pairs],
                "pairs_display": [[col_display(r.from_table, a), col_display(r.to_table, b)] for a, b in r.pairs],
                "cardinality": r.cardinality, "role": r.role, "active": r.active,
            } for r in rels],
        }

