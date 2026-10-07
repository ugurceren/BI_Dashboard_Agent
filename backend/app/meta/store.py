"""Platform meta deposu: SQL Server (META_ODBC / Bağlantı Ayarları → Platform Veritabanı) ya da yerel SQLite.

Tablolar (backend/meta/schema.sql ile aynı):
  role_assignments  kullanıcı / AD grubu → platform rolü (admin | builder | viewer) + veri rolü (policy.toml)
  published_reports Vitrin'deki rapor (tasarım oturumuna bağlı), güncel sürüm, durum (active | retired)
  report_versions   yayın anındaki değişmez spec + dataset'ler
  report_grants     raporu görebilecek kullanıcı / AD grubu (+ dışa aktarma izni)
  audit_events      kim, ne zaman, ne yaptı

İki sürücü de qmark (?) parametre biçimini kullanır; sorgular parametrelidir (kullanıcı girdisi SQL'e eklenmez).
Agent araçları bu depoya yazmaz: yalnız platform uç noktaları yazar.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)

PLATFORM_ROLES = ("viewer", "builder", "admin")
SCHEMA_VERSION = 1

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS meta_info (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS role_assignments (
  principal_type TEXT NOT NULL, principal TEXT NOT NULL, platform_role TEXT, data_role TEXT,
  granted_by TEXT NOT NULL, granted_at TEXT NOT NULL, PRIMARY KEY (principal_type, principal));
CREATE TABLE IF NOT EXISTS published_reports (
  report_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, owner TEXT NOT NULL, owner_name TEXT, title TEXT NOT NULL,
  description TEXT, domains TEXT, current_version INTEGER NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_published_session ON published_reports(session_id);
CREATE TABLE IF NOT EXISTS report_versions (
  report_id TEXT NOT NULL, version INTEGER NOT NULL, spec_json TEXT NOT NULL, datasets_json TEXT NOT NULL,
  notes TEXT, published_by TEXT NOT NULL, published_at TEXT NOT NULL, PRIMARY KEY (report_id, version));
CREATE TABLE IF NOT EXISTS report_grants (
  report_id TEXT NOT NULL, principal_type TEXT NOT NULL, principal TEXT NOT NULL, can_export INTEGER NOT NULL DEFAULT 0,
  granted_by TEXT NOT NULL, granted_at TEXT NOT NULL, PRIMARY KEY (report_id, principal_type, principal));
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, username TEXT NOT NULL, event TEXT NOT NULL,
  report_id TEXT, details_json TEXT);
"""

SQLSERVER_DDL_FILE = Path(__file__).resolve().parents[2] / "meta" / "schema.sql"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(principal: str) -> str:
    """Karşılaştırma için: küçük harf, boşluksuz (DOMAIN\\kullanici ya da grup adı)."""
    return " ".join((principal or "").split()).lower()


def short(username: str) -> str:
    return norm(username).rpartition("\\")[2]


@dataclass
class Assignment:
    principal_type: str
    principal: str
    platform_role: str | None
    data_role: str | None
    granted_by: str
    granted_at: str


class MetaStore:
    def __init__(self, connect, kind: str):
        self._connect = connect      # () -> DB-API bağlantısı
        self.kind = kind             # sqlite | sqlserver
        self._lock = threading.Lock()
        self._conn = None

    # ------------------------------------------------------------ kuruluş
    @classmethod
    def sqlite(cls, path: Path | str) -> "MetaStore":
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        st = cls(lambda: sqlite3.connect(str(path), check_same_thread=False), "sqlite")
        st.ensure_schema()
        return st

    @classmethod
    def sqlserver(cls, odbc: str) -> "MetaStore":
        import pyodbc  # type: ignore
        st = cls(lambda: pyodbc.connect(odbc, autocommit=False, timeout=10), "sqlserver")
        st.ensure_schema()
        return st

    def ensure_schema(self) -> None:
        with self._cur() as cur:
            if self.kind == "sqlite":
                cur.connection.executescript(_SQLITE_DDL)
            else:
                cur.execute("SELECT OBJECT_ID('dbo.audit_events')")
                if cur.fetchone()[0] is None:
                    for stmt in _split_go(SQLSERVER_DDL_FILE.read_text(encoding="utf-8")):
                        cur.execute(stmt)
            self._set_info(cur, "schema_version", str(SCHEMA_VERSION))

    def _set_info(self, cur, k: str, v: str) -> None:
        cur.execute("DELETE FROM meta_info WHERE k = ?", (k,))
        cur.execute("INSERT INTO meta_info (k, v) VALUES (?, ?)", (k, v))

    @contextmanager
    def _cur(self) -> Iterator[Any]:
        with self._lock:
            if self._conn is None:
                self._conn = self._connect()
            conn = self._conn
            cur = conn.cursor()
            try:
                yield cur
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                if self.kind == "sqlserver":   # kopmuş bağlantı bir sonraki istekte yenilensin
                    self._conn = None
                raise
            finally:
                try:
                    cur.close()
                except Exception:  # noqa: BLE001
                    pass

    def _rows(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._cur() as cur:
            cur.execute(sql, args)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def _exec(self, sql: str, args: tuple = ()) -> None:
        with self._cur() as cur:
            cur.execute(sql, args)

    # ------------------------------------------------------------ rol atamaları
    def assignments(self) -> list[Assignment]:
        return [Assignment(**r) for r in self._rows(
            "SELECT principal_type, principal, platform_role, data_role, granted_by, granted_at FROM role_assignments "
            "ORDER BY principal_type, principal")]

    def set_assignment(self, principal_type: str, principal: str, platform_role: str | None, data_role: str | None,
                       by: str) -> None:
        p = norm(principal)
        with self._cur() as cur:
            cur.execute("DELETE FROM role_assignments WHERE principal_type = ? AND principal = ?", (principal_type, p))
            cur.execute("INSERT INTO role_assignments (principal_type, principal, platform_role, data_role, granted_by, "
                        "granted_at) VALUES (?, ?, ?, ?, ?, ?)", (principal_type, p, platform_role, data_role, by, now_iso()))

    def delete_assignment(self, principal_type: str, principal: str) -> bool:
        with self._cur() as cur:
            cur.execute("DELETE FROM role_assignments WHERE principal_type = ? AND principal = ?",
                        (principal_type, norm(principal)))
            return (cur.rowcount or 0) > 0

    # ------------------------------------------------------------ yayınlar
    def report_by_session(self, session_id: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM published_reports WHERE session_id = ?", (session_id,))
        return _report(rows[0]) if rows else None

    def report(self, report_id: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM published_reports WHERE report_id = ?", (report_id,))
        return _report(rows[0]) if rows else None

    def reports(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self._rows("SELECT * FROM published_reports WHERE status = ? ORDER BY updated_at DESC", (status,))
        else:
            rows = self._rows("SELECT * FROM published_reports ORDER BY updated_at DESC")
        return [_report(r) for r in rows]

    def publish(self, *, session_id: str, owner: str, owner_name: str | None, title: str, description: str | None,
                domains: list[str], spec: dict[str, Any], datasets: list[dict[str, Any]], notes: str | None,
                by: str) -> dict[str, Any]:
        """Yeni sürüm: ilk yayında rapor oluşur, sonrakilerde sürüm artar (eski sürümler saklanır)."""
        ts = now_iso()
        cur_rep = self.report_by_session(session_id)
        with self._cur() as cur:
            if cur_rep is None:
                rid, ver = uuid.uuid4().hex[:12], 1
                cur.execute("INSERT INTO published_reports (report_id, session_id, owner, owner_name, title, description, "
                            "domains, current_version, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (rid, session_id, owner, owner_name, title, description, json.dumps(domains, ensure_ascii=False),
                             ver, "active", ts, ts))
            else:
                rid, ver = cur_rep["report_id"], cur_rep["current_version"] + 1
                cur.execute("UPDATE published_reports SET title = ?, description = ?, domains = ?, current_version = ?, "
                            "status = 'active', updated_at = ? WHERE report_id = ?",
                            (title, description, json.dumps(domains, ensure_ascii=False), ver, ts, rid))
            cur.execute("INSERT INTO report_versions (report_id, version, spec_json, datasets_json, notes, published_by, "
                        "published_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (rid, ver, json.dumps(spec, ensure_ascii=False, default=str),
                         json.dumps(datasets, ensure_ascii=False, default=str), notes, by, ts))
        return self.report(rid)  # type: ignore[return-value]

    def version(self, report_id: str, version: int | None = None) -> dict[str, Any] | None:
        rep = self.report(report_id)
        if not rep:
            return None
        rows = self._rows("SELECT * FROM report_versions WHERE report_id = ? AND version = ?",
                          (report_id, version or rep["current_version"]))
        if not rows:
            return None
        r = rows[0]
        return {**r, "spec": json.loads(r.pop("spec_json")), "datasets": json.loads(r.pop("datasets_json"))}

    def versions(self, report_id: str) -> list[dict[str, Any]]:
        return self._rows("SELECT report_id, version, notes, published_by, published_at FROM report_versions "
                          "WHERE report_id = ? ORDER BY version DESC", (report_id,))

    def set_status(self, report_id: str, status: str) -> None:
        self._exec("UPDATE published_reports SET status = ?, updated_at = ? WHERE report_id = ?", (status, now_iso(), report_id))

    def set_owner(self, report_id: str, owner: str, owner_name: str | None) -> None:
        self._exec("UPDATE published_reports SET owner = ?, owner_name = ?, updated_at = ? WHERE report_id = ?",
                   (owner, owner_name, now_iso(), report_id))

    def delete_report(self, report_id: str) -> None:
        with self._cur() as cur:
            for t in ("report_grants", "report_versions", "published_reports"):
                cur.execute(f"DELETE FROM {t} WHERE report_id = ?", (report_id,))

    # ------------------------------------------------------------ paylaşım
    def grants(self, report_id: str) -> list[dict[str, Any]]:
        return [{**g, "can_export": bool(g["can_export"])} for g in self._rows(
            "SELECT principal_type, principal, can_export, granted_by, granted_at FROM report_grants WHERE report_id = ? "
            "ORDER BY principal_type, principal", (report_id,))]

    def all_grants(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for g in self._rows("SELECT report_id, principal_type, principal, can_export FROM report_grants"):
            out.setdefault(g["report_id"], []).append({**g, "can_export": bool(g["can_export"])})
        return out

    def set_grants(self, report_id: str, grants: list[dict[str, Any]], by: str) -> None:
        ts = now_iso()
        with self._cur() as cur:
            cur.execute("DELETE FROM report_grants WHERE report_id = ?", (report_id,))
            seen: set[tuple[str, str]] = set()
            for g in grants:
                key = (g["principal_type"], norm(g["principal"]))
                if not key[1] or key in seen:
                    continue
                seen.add(key)
                cur.execute("INSERT INTO report_grants (report_id, principal_type, principal, can_export, granted_by, "
                            "granted_at) VALUES (?, ?, ?, ?, ?, ?)", (report_id, *key, 1 if g.get("can_export") else 0, by, ts))

    # ------------------------------------------------------------ denetim
    def audit(self, username: str, event: str, report_id: str | None = None, **details: Any) -> None:
        try:
            self._exec("INSERT INTO audit_events (ts, username, event, report_id, details_json) VALUES (?, ?, ?, ?, ?)",
                       (now_iso(), username, event, report_id,
                        json.dumps(details, ensure_ascii=False, default=str) if details else None))
        except Exception as e:  # noqa: BLE001 — denetim kaydı yazılamasa da işlem sürsün (günlüğe düşer)
            log.warning("Denetim kaydı yazılamadı (%s %s): %s", username, event, e)

    def audit_events(self, limit: int = 200, username: str | None = None, event: str | None = None,
                     report_id: str | None = None) -> list[dict[str, Any]]:
        where, args = [], []
        for col, val in (("username", username), ("event", event), ("report_id", report_id)):
            if val:
                where.append(f"{col} = ?")
                args.append(val)
        w = f" WHERE {' AND '.join(where)}" if where else ""
        limit = max(1, min(int(limit), 2000))
        sql = (f"SELECT TOP ({limit}) * FROM audit_events{w} ORDER BY id DESC" if self.kind == "sqlserver"
               else f"SELECT * FROM audit_events{w} ORDER BY id DESC LIMIT {limit}")
        rows = self._rows(sql, tuple(args))
        for r in rows:
            r["details"] = json.loads(r.pop("details_json") or "null")
        return rows


def _report(r: dict[str, Any]) -> dict[str, Any]:
    r = dict(r)
    try:
        r["domains"] = json.loads(r.get("domains") or "[]")
    except ValueError:
        r["domains"] = []
    return r


def _split_go(script: str) -> list[str]:
    out, buf = [], []
    for line in script.splitlines():
        if line.strip().upper() == "GO":
            if "".join(buf).strip():
                out.append("\n".join(buf))
            buf = []
        else:
            buf.append(line)
    if "".join(buf).strip():
        out.append("\n".join(buf))
    return out
