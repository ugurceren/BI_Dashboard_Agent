"""Nesne keşfi (object discovery): bir tablo / view'ın SQL Server'daki teknik özellikleri, veritabanı kataloğundan (sys.*).

Sözlük (get_table_details) iş bilgisini verir: iş adı, rol, örnek değer, ilişki. Burası veritabanının kendisinin
bildiklerini verir: kolon tipi / uzunluk / hassasiyet, NULL, identity, hesaplanan kolon, varsayılan değer, birincil anahtar,
index'ler, yabancı anahtarlar (giden + gelen), satır sayısı, oluşturma / değişiklik tarihi, açıklama (MS_Description),
view'larda kaynak nesneler ve tanım.

Yalnız katalog görünümleri okunur (veri satırı okunmaz); nesne adı sözlükten / katalogdan gelen gerçek addır ve
T-SQL literal olarak kaçışlanır. Sonuçlar 10 dk önbellekte tutulur.
"""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

CACHE_TTL_S = 600
DEFINITION_CHARS = 2500
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _lit(schema: str, name: str) -> str:
    """OBJECT_ID için N'[şema].[nesne]' (köşeli parantez ve tırnak kaçışlı)."""
    ident = "[" + schema.replace("]", "]]") + "].[" + name.replace("]", "]]") + "]"
    return "N'" + ident.replace("'", "''") + "'"


def _type_text(t: str, max_len: Any, prec: Any, scale: Any) -> str:
    t = (t or "").lower()
    if t in ("varchar", "char", "varbinary", "binary"):
        return f"{t}({'max' if max_len == -1 else max_len})"
    if t in ("nvarchar", "nchar"):
        return f"{t}({'max' if max_len == -1 else int(max_len) // 2})"
    if t in ("decimal", "numeric"):
        return f"{t}({prec},{scale})"
    if t in ("datetime2", "time", "datetimeoffset") and scale not in (None, 7):
        return f"{t}({scale})"
    return t


def discover(dd, connector, key: str, *, include_pii: bool) -> dict[str, Any]:
    """dd: DataDictionary; key: sözlük anahtarı (şema.nesne ya da db.şema.nesne)."""
    t = dd.tables[key]
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return _mark_pii(hit[1], t, include_pii)
    parts = (t.display_name or t.name).split(".")
    schema, name = parts[-2], parts[-1]
    db = dd.db_of(key)
    con = connector if not db or not dd.database or db.lower() == dd.database.lower() else dd._connector_for(db)
    obj = f"OBJECT_ID({_lit(schema, name)})"

    head = con.execute(
        "SELECT o.type_desc, CONVERT(varchar(19), o.create_date, 126), CONVERT(varchar(19), o.modify_date, 126), "
        "CAST(ep.value AS nvarchar(2000)), "
        "(SELECT SUM(p.rows) FROM sys.partitions p WHERE p.object_id = o.object_id AND p.index_id IN (0, 1)) "
        "FROM sys.objects o LEFT JOIN sys.extended_properties ep ON ep.class = 1 AND ep.major_id = o.object_id "
        f"AND ep.minor_id = 0 AND ep.name = 'MS_Description' WHERE o.object_id = {obj}", 1).rows
    if not head:
        raise LookupError(f"{t.display_name or t.name} veritabanında bulunamadı (silinmiş ya da yetki yok).")
    type_desc, created, modified, descr, rows = head[0]
    is_view = "VIEW" in str(type_desc).upper()

    cols = con.execute(
        "SELECT c.name, TYPE_NAME(c.user_type_id), c.max_length, c.precision, c.scale, c.is_nullable, c.is_identity, "
        "c.is_computed, OBJECT_DEFINITION(c.default_object_id), CAST(ep.value AS nvarchar(1000)) "
        "FROM sys.columns c LEFT JOIN sys.extended_properties ep ON ep.class = 1 AND ep.major_id = c.object_id "
        f"AND ep.minor_id = c.column_id AND ep.name = 'MS_Description' WHERE c.object_id = {obj} ORDER BY c.column_id",
        2000).rows

    idx_rows = con.execute(
        "SELECT i.name, i.type_desc, i.is_primary_key, i.is_unique, ic.is_included_column, c.name "
        "FROM sys.indexes i JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id "
        "JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id "
        f"WHERE i.object_id = {obj} AND i.type > 0 ORDER BY i.index_id, ic.is_included_column, ic.key_ordinal, ic.index_column_id",
        2000).rows
    indexes: dict[str, dict[str, Any]] = {}
    for iname, itype, is_pk, is_unique, included, cname in idx_rows:
        ix = indexes.setdefault(iname or "?", {"name": iname, "type": str(itype).lower().replace("_", " "),
                                               **({"primary_key": True} if is_pk else {}), **({"unique": True} if is_unique else {}),
                                               "columns": [], "include": []})
        ix["include" if included else "columns"].append(cname)
    pk_cols = {c for ix in indexes.values() if ix.get("primary_key") for c in ix["columns"]}

    fk_rows = con.execute(
        "SELECT fk.name, OBJECT_SCHEMA_NAME(fk.parent_object_id) + '.' + OBJECT_NAME(fk.parent_object_id), cp.name, "
        "OBJECT_SCHEMA_NAME(fk.referenced_object_id) + '.' + OBJECT_NAME(fk.referenced_object_id), cr.name "
        "FROM sys.foreign_keys fk JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id "
        "JOIN sys.columns cp ON cp.object_id = fkc.parent_object_id AND cp.column_id = fkc.parent_column_id "
        "JOIN sys.columns cr ON cr.object_id = fkc.referenced_object_id AND cr.column_id = fkc.referenced_column_id "
        f"WHERE fk.parent_object_id = {obj} OR fk.referenced_object_id = {obj} ORDER BY fk.name, fkc.constraint_column_id",
        2000).rows
    me = f"{schema}.{name}".lower()
    fks_out: dict[str, dict[str, Any]] = {}
    fks_in: dict[str, dict[str, Any]] = {}
    for fname, parent, pcol, ref, rcol in fk_rows:
        if str(parent).lower() == me:
            f = fks_out.setdefault(fname, {"to": ref, "columns": [], "ref_columns": []})
        else:
            f = fks_in.setdefault(fname, {"from": parent, "columns": [], "ref_columns": []})
        f["columns"].append(pcol)
        f["ref_columns"].append(rcol)

    out: dict[str, Any] = {
        "object": t.display_name or t.name, "database": db, "type": "view" if is_view else "table",
        "row_count": rows, "created": created, "modified": modified,
        **({"description": descr} if descr else {}),
        "columns": [{
            "name": c[0], "type": _type_text(c[1], c[2], c[3], c[4]), "nullable": bool(c[5]),
            **({"primary_key": True} if c[0] in pk_cols else {}), **({"identity": True} if c[6] else {}),
            **({"computed": True} if c[7] else {}), **({"default": c[8]} if c[8] else {}),
            **({"description": c[9]} if c[9] else {}),
        } for c in cols],
        "indexes": [{k: v for k, v in ix.items() if v} for ix in indexes.values()],
        "foreign_keys": [{"name": n, **f} for n, f in fks_out.items()],
        "referenced_by": [{"name": n, **f} for n, f in fks_in.items()],
    }
    if is_view:
        deps = con.execute(
            "SELECT DISTINCT d.referenced_database_name, COALESCE(d.referenced_schema_name, 'dbo'), d.referenced_entity_name "
            f"FROM sys.sql_expression_dependencies d WHERE d.referencing_id = {obj} AND d.referenced_entity_name IS NOT NULL",
            500).rows
        out["sources"] = [".".join(x for x in r if x) for r in deps]
        definition = con.execute(f"SELECT OBJECT_DEFINITION({obj})", 1).rows
        text = (definition[0][0] if definition else None) or ""
        if text:
            out["definition"] = text[:DEFINITION_CHARS] + (f"\n…(kısaltıldı, {len(text)} karakter)" if len(text) > DEFINITION_CHARS else "")
        else:
            out["definition_note"] = "Tanım okunamadı (VIEW DEFINITION yetkisi yok ya da şifreli view)."
    _cache[key] = (time.time(), out)
    return _mark_pii(out, t, include_pii)


def _mark_pii(out: dict[str, Any], t, include_pii: bool) -> dict[str, Any]:
    """Sözlükte kişisel veri işaretli kolonlar: tipi görünür, ama sorgulanamayacağı belirtilir."""
    pii = {c.name.lower() for c in t.columns if c.is_pii}
    if not pii:
        return out
    res = dict(out)
    res["columns"] = [{**c, "pii": True, **({} if include_pii else {"note": "Kişisel veri — sorgulanamaz"})}
                      if c["name"].lower() in pii else c for c in out["columns"]]
    return res


def compact(info: dict[str, Any]) -> dict[str, Any]:
    """Modele giden kısa biçim: kolonlar tek satır metin (geniş tablolarda bağlam penceresini doldurmasın)."""
    def col(c: dict[str, Any]) -> str:
        flags = [x for x, on in (("PK", c.get("primary_key")), ("identity", c.get("identity")), ("computed", c.get("computed")),
                                 ("PII-sorgulanamaz" if c.get("note") else "PII", c.get("pii"))) if on]
        s = f"{c['name']} {c['type']} {'NULL' if c['nullable'] else 'NOT NULL'}"
        if flags:
            s += " [" + ", ".join(flags) + "]"
        if c.get("default"):
            s += f" default {c['default']}"
        if c.get("description"):
            s += f" — {c['description']}"
        return s
    return {**info, "columns": [col(c) for c in info["columns"]]}
