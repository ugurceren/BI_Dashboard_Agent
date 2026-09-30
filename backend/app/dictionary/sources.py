"""Sözlük kaynakları: her sözlük rolü (tablolar / kolonlar / ilişkiler / metrikler) bir ya da birden çok
tablodan (SQL Server, MySQL) ya da Excel sayfasından okunur.

  * her kaynakta var olan beklenen kolonlar alınır, eksik İSTEĞE BAĞLI kolonlar boş (None) gelir;
  * ZORUNLU kolon eksikse o tablo / sayfa kullanılmaz ve açık bir hata döner;
  * aynı role birden çok kaynak verilirse satırlar birleştirilir (ör. satış ve finans ekiplerinin ayrı kolon sözlükleri);
  * değerler normalize edilir: is_pii / is_active "Evet", "1", "x", True → True; row_count sayıya çevrilir.

Sözlük yalnızca meta veridir (tablo / kolon açıklamaları); rapor verisi her zaman SQL Server'dan okunur.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

ROLES: dict[str, dict[str, Any]] = {
    "tables": {
        "label": "Tablolar", "required": ["table_name"],
        "optional": ["business_name", "description", "subject_area", "grain", "row_count", "table_type"],
        "must": True,
    },
    "columns": {
        "label": "Kolonlar", "required": ["table_name", "column_name"],
        "optional": ["business_name", "description", "data_type", "column_role", "default_aggregation",
                     "synonyms", "is_pii", "sample_values"],
        "must": True,
    },
    "relationships": {
        "label": "İlişkiler", "required": ["from_table", "from_column", "to_table", "to_column"],
        "optional": ["relationship_id", "cardinality", "role", "is_active"],
        "must": False,
    },
    "metrics": {
        "label": "Metrikler", "required": ["metric_name", "expression_sql"],
        "optional": ["business_name", "description", "base_table", "value_format", "synonyms"],
        "must": False,
    },
}
# kaynak türüne göre varsayılan tablo / sayfa adları
DEFAULTS: dict[str, dict[str, list[str]]] = {
    "sqlserver": {"tables": ["meta.dd_tables"], "columns": ["meta.dd_columns"],
                  "relationships": ["meta.dd_relationships"], "metrics": ["meta.dd_metrics"]},
    "mysql": {"tables": ["dd_tables"], "columns": ["dd_columns"], "relationships": ["dd_relationships"], "metrics": ["dd_metrics"]},
    "excel": {"tables": ["Tablolar"], "columns": ["Kolonlar"], "relationships": ["İlişkiler"], "metrics": ["Metrikler"]},
}
DEFAULT_SOURCES = DEFAULTS["sqlserver"]
KINDS = tuple(DEFAULTS)

_BOOL_FIELDS = {"is_pii", "is_active"}
_INT_FIELDS = {"row_count"}
_TRUE = {"1", "true", "yes", "y", "evet", "e", "x", "✓", "doğru", "var"}


def default_sources(kind: str) -> dict[str, list[str]]:
    return {k: list(v) for k, v in DEFAULTS.get(kind, DEFAULT_SOURCES).items()}


def suggest_role(columns: set[str]) -> str | None:
    """Kolonlarına göre en uygun sözlük rolü (tüm zorunlu kolonları olanlar içinden en çok eşleşen)."""
    best, score = None, 0
    for role, spec in ROLES.items():
        if not all(c in columns for c in spec["required"]):
            continue
        s = len(spec["required"]) * 2 + sum(c in columns for c in spec["optional"])
        if s > score:
            best, score = role, s
    return best


def _norm_key(k: Any) -> str:
    return re.sub(r"\s+", "_", str(k or "").strip().lower())


def _norm_value(key: str, v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip()
        if v == "":
            return None
    if key in _BOOL_FIELDS:
        if v is None:
            return None if key == "is_active" else False
        return v if isinstance(v, bool) else str(v).strip().casefold() in _TRUE
    if key in _INT_FIELDS and v is not None:
        try:
            return int(float(str(v).replace(".", "").replace(",", "."))) if isinstance(v, str) else int(v)
        except (TypeError, ValueError):
            return None
    return v


# ------------------------------------------------------------------ okuyucular
class Reader(Protocol):
    kind: str

    def columns(self, names: list[str]) -> dict[str, set[str]]: ...        # küçük harf ad → kolonlar
    def read(self, name: str) -> list[dict[str, Any]]: ...                 # kolon adları küçük harf
    def list_tables(self) -> dict[str, list[str]]: ...                     # görünen ad → kolonlar


_SQL_NAME = re.compile(r"^[A-Za-z_][\w$#@]*\.[A-Za-z_][\w$#@]*$")
_MY_NAME = re.compile(r"^[\w$]+(\.[\w$]+)?$")


def _rows(columns: list[str], raw: list[list[Any]]) -> list[dict[str, Any]]:
    keys = [_norm_key(c) for c in columns]
    return [dict(zip(keys, r)) for r in raw]


class SqlServerReader:
    kind = "sqlserver"

    def __init__(self, con):
        self.con = con

    @staticmethod
    def valid(name: str) -> bool:
        return bool(_SQL_NAME.match(name or ""))

    def columns(self, names: list[str]) -> dict[str, set[str]]:
        wanted = [n for n in names if self.valid(n)]
        if not wanted:
            return {}
        cond = " OR ".join(f"(TABLE_SCHEMA = '{n.split('.', 1)[0]}' AND TABLE_NAME = '{n.split('.', 1)[1]}')" for n in wanted)
        r = self.con.execute(f"SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE {cond}", 50_000)
        out: dict[str, set[str]] = {}
        for schema, table, col in r.rows:
            out.setdefault(f"{schema}.{table}".lower(), set()).add(_norm_key(col))
        return out

    def read(self, name: str) -> list[dict[str, Any]]:
        schema, table = name.split(".", 1)
        r = self.con.execute(f"SELECT * FROM [{schema}].[{table}]", 200_000)
        return _rows(r.columns, r.rows)

    def list_tables(self) -> dict[str, list[str]]:
        r = self.con.execute(
            "SELECT c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS c "
            "JOIN INFORMATION_SCHEMA.TABLES t ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME "
            "WHERE c.TABLE_SCHEMA NOT IN ('sys', 'INFORMATION_SCHEMA') ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION", 50_000)
        out: dict[str, list[str]] = {}
        for schema, table, col in r.rows:
            out.setdefault(f"{schema}.{table}", []).append(str(col))
        return out


class MySQLReader:
    """MySQL / MariaDB (pymysql). Tablo adı: 'tablo' (seçili veritabanında) ya da 'veritabanı.tablo'."""
    kind = "mysql"

    def __init__(self, host: str, port: int, user: str, password: str, database: str, ssl: bool = False, timeout: int = 15):
        import pymysql

        self.database = database
        self.conn = pymysql.connect(host=host, port=int(port or 3306), user=user, password=password, database=database or None,
                                    charset="utf8mb4", connect_timeout=timeout, read_timeout=60, autocommit=True,
                                    ssl={"ssl": {}} if ssl else None)

    @staticmethod
    def valid(name: str) -> bool:
        return bool(_MY_NAME.match(name or ""))

    def _split(self, name: str) -> tuple[str, str]:
        db, _, t = name.rpartition(".")
        return (db or self.database), t

    def _query(self, sql: str, args: tuple = ()) -> tuple[list[str], list[list[Any]]]:
        with self.conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute(sql, args)
            cols = [d[0] for d in cur.description or []]
            return cols, [list(r) for r in cur.fetchall()]

    def probe(self) -> dict[str, Any]:
        _, rows = self._query("SELECT VERSION(), DATABASE(), CURRENT_USER()")
        ver, db, user = rows[0]
        return {"server_name": self.conn.host, "database": db, "version": f"MySQL {ver}", "login": user, "driver": "PyMySQL"}

    def databases(self) -> list[str]:
        _, rows = self._query("SHOW DATABASES")
        return [r[0] for r in rows if r[0] not in ("information_schema", "mysql", "performance_schema", "sys")]

    def columns(self, names: list[str]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for n in names:
            if not self.valid(n):
                continue
            db, t = self._split(n)
            _, rows = self._query("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s", (db, t))
            if rows:
                out[n.lower()] = {_norm_key(r[0]) for r in rows}
        return out

    def read(self, name: str) -> list[dict[str, Any]]:
        db, t = self._split(name)
        cols, rows = self._query(f"SELECT * FROM `{db}`.`{t}`")
        return _rows(cols, rows)

    def list_tables(self) -> dict[str, list[str]]:
        _, rows = self._query("SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s "
                              "ORDER BY TABLE_NAME, ORDINAL_POSITION", (self.database,))
        out: dict[str, list[str]] = {}
        for t, c in rows:
            out.setdefault(t, []).append(str(c))
        return out


class ExcelReader:
    """Excel çalışma kitabı (.xlsx / .xlsm): her sayfa bir tablo; ilk dolu satır başlıktır."""
    kind = "excel"

    def __init__(self, path: str | Path):
        import openpyxl

        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"Excel dosyası bulunamadı: {self.path}")
        wb = openpyxl.load_workbook(self.path, read_only=True, data_only=True)
        self._sheets: dict[str, tuple[list[str], list[list[Any]]]] = {}
        for ws in wb.worksheets:
            it = ws.iter_rows(values_only=True)
            header: list[str] = []
            for row in it:
                if any(v not in (None, "") for v in row):
                    header = [str(v).strip() if v is not None else "" for v in row]
                    break
            data = [list(r) for r in it if any(v not in (None, "") for v in r)]
            self._sheets[ws.title] = (header, data)
        wb.close()

    @staticmethod
    def valid(name: str) -> bool:
        return bool(name and name.strip())

    def _find(self, name: str) -> str | None:
        if name in self._sheets:
            return name
        key = name.strip().casefold()
        return next((s for s in self._sheets if s.strip().casefold() == key), None)

    def probe(self) -> dict[str, Any]:
        return {"server_name": self.path.name, "database": f"{len(self._sheets)} sayfa", "version": "Excel", "driver": "openpyxl"}

    def columns(self, names: list[str]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for n in names:
            s = self._find(n)
            if s is not None:
                out[n.lower()] = {_norm_key(c) for c in self._sheets[s][0] if c}
        return out

    def read(self, name: str) -> list[dict[str, Any]]:
        header, data = self._sheets[self._find(name)]  # type: ignore[index]
        keys = [_norm_key(c) for c in header]
        return [{k: v for k, v in zip(keys, r) if k} for r in data]

    def list_tables(self) -> dict[str, list[str]]:
        return {s: [c for c in h if c] for s, (h, _) in self._sheets.items()}


# ------------------------------------------------------------------ toplama
@dataclass
class Collected:
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return {r: len(v) for r, v in self.rows.items()}


def collect(reader: Reader, sources: dict[str, list[str]]) -> Collected:
    out = Collected()
    names = sorted({n for lst in sources.values() for n in lst or []})
    bad = [n for n in names if not reader.valid(n)]  # type: ignore[attr-defined]
    if bad:
        out.errors.append(f"Geçersiz ad: {', '.join(bad)}" + (" (şema.tablo biçiminde olmalı)" if reader.kind == "sqlserver" else ""))
    cols = reader.columns([n for n in names if n not in bad])
    what, its, in_it = ("sayfa", "sayfası", "sayfasında") if reader.kind == "excel" else ("tablo", "tablosu", "tablosunda")
    for role, spec in ROLES.items():
        rows: list[dict[str, Any]] = []
        used = False
        for name in sources.get(role) or []:
            if name in bad:
                continue
            have = cols.get(name.lower())
            if have is None:
                out.errors.append(f"{spec['label']}: '{name}' {its} bulunamadı" + ("" if reader.kind == "excel" else " ya da okuma yetkisi yok") + ".")
                continue
            missing = [c for c in spec["required"] if c not in have]
            if missing:
                out.errors.append(f"{spec['label']}: '{name}' {in_it} zorunlu kolon(lar) yok: {', '.join(missing)}.")
                continue
            absent = [c for c in spec["optional"] if c not in have]
            if absent:
                out.warnings.append(f"{spec['label']}: '{name}' {in_it} olmayan kolonlar boş sayıldı: {', '.join(absent)}.")
            used = True
            skipped = 0
            for r in reader.read(name):
                row = {k: _norm_value(k, r.get(k)) for k in spec["required"] + spec["optional"]}
                if any(row[k] in (None, "") for k in spec["required"]):
                    skipped += 1
                    continue
                rows.append(row)
            if skipped:
                out.warnings.append(f"{spec['label']}: '{name}' içinde zorunlu alanı boş {skipped} satır atlandı.")
        if used:
            out.rows[role] = rows
        elif spec["must"]:
            out.errors.append(f"{spec['label']} için en az bir geçerli {what} seçilmeli.")
    return out
