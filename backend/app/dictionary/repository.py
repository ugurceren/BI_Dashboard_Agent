"""Veri sözlüğü: config/dictionary.toml'daki sorgularla yüklenir, bellekte aranır.

Sözlükler genelde birkaç bin satırdır; bu yüzden bellekte tutup Türkçe'ye duyarlı anahtar
kelime + eş anlamlı araması yapıyoruz. Vektör arama gerekirse `search` yerine takılabilir.
"""

from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings, load_toml
from app.data.connector import Connector, create_connector


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


@dataclass
class DDTable:
    name: str
    business_name: str
    description: str
    subject_area: str
    grain: str
    row_count: int | None
    columns: list[DDColumn] = field(default_factory=list)


@dataclass
class DDMetric:
    name: str
    business_name: str
    description: str
    expression_sql: str
    base_table: str
    value_format: str
    synonyms: list[str]


@dataclass
class DDRelationship:
    from_table: str
    from_column: str
    to_table: str
    to_column: str


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
                                   r.get("subject_area") or "", r.get("grain") or "", r.get("row_count"))
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
            ))
        rels = [DDRelationship(str(r["from_table"]).lower(), str(r["from_column"]).lower(),
                               str(r["to_table"]).lower(), str(r["to_column"]).lower()) for r in rows(q["relationships"])]
        metrics = [DDMetric(r["metric_name"], r.get("business_name") or r["metric_name"], r.get("description") or "",
                            r.get("expression_sql") or "", (r.get("base_table") or "").lower(), r.get("value_format") or "number",
                            [s.strip() for s in (r.get("synonyms") or "").split(",") if s.strip()])
                   for r in rows(q.get("metrics"))]
        with self._lock:
            self.tables, self.relationships, self.metrics = tables, rels, metrics
        return self

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
            "joins": [f"{r.from_table}.{r.from_column} = {r.to_table}.{r.to_column}" for r in self.relationships_for(t.name)],
        }
