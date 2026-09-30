"""Sözlük tabloları: her sözlük rolü (tablolar / kolonlar / ilişkiler / metrikler) bir ya da birden çok tablodan okunur.

Bağlantı Ayarları'nda seçilen tablolar için sorgular burada üretilir (dictionary.toml'daki elle yazılmış sorguların yerine):
  * her tabloda var olan beklenen kolonlar seçilir, eksik İSTEĞE BAĞLI kolonlar NULL gelir;
  * ZORUNLU kolon eksikse tablo kullanılmaz ve açık bir hata döner;
  * aynı role birden çok tablo verilirse UNION ALL ile birleştirilir (ör. satış ve finans ekiplerinin ayrı kolon sözlükleri).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

ROLES: dict[str, dict[str, Any]] = {
    "tables": {
        "label": "Tablolar", "required": ["table_name"],
        "optional": ["business_name", "description", "subject_area", "grain", "row_count", "table_type"],
        "must": True, "default": "meta.dd_tables",
    },
    "columns": {
        "label": "Kolonlar", "required": ["table_name", "column_name"],
        "optional": ["business_name", "description", "data_type", "column_role", "default_aggregation",
                     "synonyms", "is_pii", "sample_values"],
        "must": True, "default": "meta.dd_columns",
    },
    "relationships": {
        "label": "İlişkiler", "required": ["from_table", "from_column", "to_table", "to_column"],
        "optional": ["relationship_id", "cardinality", "role", "is_active"],
        "must": False, "default": "meta.dd_relationships",
    },
    "metrics": {
        "label": "Metrikler", "required": ["metric_name", "expression_sql"],
        "optional": ["business_name", "description", "base_table", "value_format", "synonyms"],
        "must": False, "default": "meta.dd_metrics",
    },
}
DEFAULT_SOURCES: dict[str, list[str]] = {r: [spec["default"]] for r, spec in ROLES.items()}
_NAME = re.compile(r"^[A-Za-z_][\w$#@]*\.[A-Za-z_][\w$#@]*$")


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name or ""))


def _q(name: str) -> str:
    schema, table = name.split(".", 1)
    return f"[{schema}].[{table}]"


def suggest_role(columns: set[str]) -> str | None:
    """Kolonlarına göre tablonun en uygun sözlük rolü (tüm zorunlu kolonları olanlar içinden en çok eşleşen)."""
    best, score = None, 0
    for role, spec in ROLES.items():
        if not all(c in columns for c in spec["required"]):
            continue
        s = len(spec["required"]) * 2 + sum(c in columns for c in spec["optional"])
        if s > score:
            best, score = role, s
    return best


@dataclass
class BuiltQueries:
    queries: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def table_columns(con, names: list[str]) -> dict[str, set[str]]:
    """Seçilen tabloların kolonları (INFORMATION_SCHEMA)."""
    wanted = [n for n in names if valid_name(n)]
    if not wanted:
        return {}
    cond = " OR ".join(f"(TABLE_SCHEMA = '{n.split('.', 1)[0]}' AND TABLE_NAME = '{n.split('.', 1)[1]}')" for n in wanted)
    r = con.execute(f"SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE {cond}", 50_000)
    out: dict[str, set[str]] = {}
    for schema, table, col in r.rows:
        out.setdefault(f"{schema}.{table}".lower(), set()).add(str(col).lower())
    return out


def build_queries(con, sources: dict[str, list[str]]) -> BuiltQueries:
    out = BuiltQueries()
    names = sorted({n for lst in sources.values() for n in lst or []})
    bad = [n for n in names if not valid_name(n)]
    if bad:
        out.errors.append(f"Geçersiz tablo adı: {', '.join(bad)} (şema.tablo biçiminde olmalı)")
    cols = table_columns(con, names)
    for role, spec in ROLES.items():
        parts: list[str] = []
        for name in sources.get(role) or []:
            if not valid_name(name):
                continue
            have = cols.get(name.lower())
            if have is None:
                out.errors.append(f"{spec['label']}: '{name}' tablosu bulunamadı ya da okuma yetkisi yok.")
                continue
            missing = [c for c in spec["required"] if c not in have]
            if missing:
                out.errors.append(f"{spec['label']}: '{name}' tablosunda zorunlu kolon(lar) yok: {', '.join(missing)}.")
                continue
            absent = [c for c in spec["optional"] if c not in have]
            if absent:
                out.warnings.append(f"{spec['label']}: '{name}' tablosunda olmayan kolonlar boş sayıldı: {', '.join(absent)}.")
            sel = [f"[{c}]" if c in have else f"NULL AS [{c}]" for c in spec["required"] + spec["optional"]]
            parts.append(f"SELECT {', '.join(sel)} FROM {_q(name)}")
        if parts:
            out.queries[role] = "\nUNION ALL\n".join(parts)
        elif spec["must"]:
            out.errors.append(f"{spec['label']} için en az bir geçerli tablo seçilmeli.")
    return out
