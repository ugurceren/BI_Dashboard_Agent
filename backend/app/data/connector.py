"""Veri kaynağı bağlantıları. Agent'ın SQL'i buraya ancak validator'dan geçtikten sonra gelir."""

from __future__ import annotations

import datetime as dt
import decimal
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import Settings


class QueryError(Exception):
    pass


@dataclass
class QueryResult:
    columns: list[str]
    types: list[str]  # "number" | "string" | "date"
    rows: list[list[Any]]
    truncated: bool = False
    elapsed_ms: int = 0
    extra: dict = field(default_factory=dict)


def _json_value(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, dt.time):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return None
    return v


def _field_type(py_values: list[Any], type_hint: str = "") -> str:
    hint = type_hint.upper()
    if any(k in hint for k in ("INT", "DECIMAL", "NUMERIC", "DOUBLE", "FLOAT", "REAL", "HUGEINT", "MONEY")):
        return "number"
    if any(k in hint for k in ("DATE", "TIMESTAMP")):
        return "date"
    for v in py_values:
        if v is None:
            continue
        if isinstance(v, bool):
            return "string"
        if isinstance(v, (int, float, decimal.Decimal)):
            return "number"
        if isinstance(v, (dt.date, dt.datetime)):
            return "date"
        return "string"
    return "string"


class Connector(Protocol):
    dialect: str

    def execute(self, sql: str, max_rows: int) -> QueryResult: ...
    def ping(self) -> None: ...


class DuckDBConnector:
    dialect = "duckdb"

    def __init__(self, path: str, timeout_s: float):
        import duckdb

        # Salt-okunur + dış erişim kapalı: agent SQL'i dosya okuyamaz, eklenti yükleyemez.
        self._con = duckdb.connect(path, read_only=True, config={"enable_external_access": False})
        self._timeout_s = timeout_s
        self._lock = threading.Lock()

    def execute(self, sql: str, max_rows: int) -> QueryResult:
        with self._lock:
            cur = self._con.cursor()
        timer = threading.Timer(self._timeout_s, cur.interrupt)
        t0 = time.perf_counter()
        timer.start()
        try:
            cur.execute(sql)
            desc = cur.description or []
            raw = cur.fetchmany(max_rows + 1)
        except Exception as e:  # duckdb.Error alt sınıfları
            msg = str(e)
            if "INTERRUPT" in msg.upper():
                msg = f"Sorgu {self._timeout_s:.0f} sn zaman aşımına uğradı."
            raise QueryError(msg) from e
        finally:
            timer.cancel()
            cur.close()
        truncated = len(raw) > max_rows
        raw = raw[:max_rows]
        cols = [d[0] for d in desc]
        types = [_field_type([r[i] for r in raw[:50]], str(d[1])) for i, d in enumerate(desc)]
        rows = [[_json_value(v) for v in r] for r in raw]
        return QueryResult(cols, types, rows, truncated, int((time.perf_counter() - t0) * 1000))

    def ping(self) -> None:
        self.execute("SELECT 1", 1)


class SqlServerConnector:
    """SQL Server (pyodbc). Bağlantı kullanıcısı mutlaka salt-okunur (db_datareader) olmalı."""

    dialect = "tsql"

    def __init__(self, odbc: str, timeout_s: float):
        try:
            import pyodbc  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("SQL Server için `pip install pyodbc` ve ODBC Driver 18 gerekli.") from e
        self._odbc = odbc
        self._timeout_s = timeout_s

    def execute(self, sql: str, max_rows: int) -> QueryResult:
        import pyodbc

        t0 = time.perf_counter()
        try:
            with pyodbc.connect(self._odbc, timeout=10, readonly=True) as con:
                con.timeout = int(self._timeout_s)
                cur = con.cursor()
                cur.execute(sql)
                desc = cur.description or []
                raw = cur.fetchmany(max_rows + 1)
        except pyodbc.Error as e:
            raise QueryError(str(e)) from e
        truncated = len(raw) > max_rows
        raw = raw[:max_rows]
        cols = [d[0] for d in desc]
        types = [_field_type([r[i] for r in raw[:50]], getattr(d[1], "__name__", "")) for i, d in enumerate(desc)]
        rows = [[_json_value(v) for v in r] for r in raw]
        return QueryResult(cols, types, rows, truncated, int((time.perf_counter() - t0) * 1000))

    def ping(self) -> None:
        self.execute("SELECT 1", 1)


def create_connector(settings: Settings) -> Connector:
    if settings.data_dialect == "tsql":
        if not settings.sqlserver_odbc:
            raise RuntimeError("DATA_DIALECT=tsql için SQLSERVER_ODBC tanımlanmalı.")
        return SqlServerConnector(settings.sqlserver_odbc, settings.query_timeout_s)
    return DuckDBConnector(str(settings.duckdb_path), settings.query_timeout_s)
