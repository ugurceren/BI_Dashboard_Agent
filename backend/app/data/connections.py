"""Arayüzden seçilen SQL Server bağlantıları (veri kaynağı + veri sözlüğü).

config/connections.json varsa .env'deki SQLSERVER_ODBC ve dictionary.toml'daki odbc'nin yerine geçer.
Yoksa eski ayarlar aynen kullanılır (geriye uyumlu).

  data:        { server, database, auth: windows|sql, username, password_enc, encrypt, trust_server_certificate }
  dictionary:  aynı alanlar + same_as_data (true → veri kaynağının sunucusu / kimlik bilgileri, yalnız veritabanı ayrı;
               varsayılan veritabanı BI_Meta)

SQL şifresi Windows DPAPI ile (yalnız bu Windows kullanıcısı çözebilir) şifrelenip saklanır, API'den geri dönmez.
Sürücü her zaman otomatik seçilir (odbc.best_sql_server_driver: 18 → 17 …).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import socket
import sys
import threading
from pathlib import Path
from typing import Any

from app.config import BACKEND_DIR, Settings, get_settings, load_toml
from app.data.odbc import best_sql_server_driver, installed_drivers

log = logging.getLogger(__name__)

# BI_CONNECTIONS_FILE: başka bir dosya (ör. uçtan uca testler kendi geçici ayar dosyasını kullanır; bu PC'nin ayarına dokunmaz)
CONNECTIONS_FILE = Path(os.environ["BI_CONNECTIONS_FILE"]) if os.environ.get("BI_CONNECTIONS_FILE") else BACKEND_DIR / "config" / "connections.json"
DEFAULT_DICTIONARY_DB = "BI_Meta"
_lock = threading.Lock()


# ------------------------------------------------------------------ şifre (DPAPI)
def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src, out = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(src), None if not protect else "BI Lens", None, None, None, 0x1, ctypes.byref(out))  # UI_FORBIDDEN
    if not ok:
        raise OSError("DPAPI işlemi başarısız")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def protect(password: str) -> str:
    if not password:
        return ""
    raw = password.encode("utf-8")
    if sys.platform == "win32":
        return "dpapi:" + base64.b64encode(_dpapi(raw, True)).decode()
    log.warning("DPAPI yok (Windows dışı); SQL şifresi yalnızca base64 ile saklanıyor.")
    return "b64:" + base64.b64encode(raw).decode()


def unprotect(token: str) -> str:
    if not token:
        return ""
    kind, _, body = token.partition(":")
    raw = base64.b64decode(body)
    return (_dpapi(raw, False) if kind == "dpapi" else raw).decode("utf-8")


# ------------------------------------------------------------------ bağlantı cümlesi
def _braced(v: str) -> str:
    return "{" + v.replace("}", "}}") + "}" if re.search(r"[;{}=\s]", v) else v


def build_odbc(c: dict[str, Any], database: str | None = None) -> str:
    """Alanlardan ODBC bağlantı cümlesi (sürücü otomatik)."""
    driver = best_sql_server_driver(installed_drivers()) or "ODBC Driver 18 for SQL Server"
    parts = [f"DRIVER={{{driver}}}", f"SERVER={c.get('server') or 'localhost'}"]
    db = database if database is not None else c.get("database")
    if db:
        parts.append(f"DATABASE={_braced(db)}")
    if (c.get("auth") or "windows") == "sql":
        parts.append(f"UID={_braced(c.get('username') or '')}")
        pwd = c.get("password")
        if pwd is None:
            pwd = unprotect(c.get("password_enc") or "")
        parts.append("PWD={" + pwd.replace("}", "}}") + "}")
    else:
        parts.append("Trusted_Connection=yes")
    parts.append(f"Encrypt={'yes' if c.get('encrypt', True) else 'no'}")
    if c.get("trust_server_certificate", True):
        parts.append("TrustServerCertificate=yes")
    parts.append("APP=BI Lens")
    return ";".join(parts) + ";"


def parse_odbc(conn: str) -> dict[str, Any]:
    """Mevcut .env / toml bağlantı cümlesini form alanlarına çevirir (şifre dönmez)."""
    kv: dict[str, str] = {}
    for m in re.finditer(r"\s*([^=;]+?)\s*=\s*(\{(?:[^}]|\}\})*\}|[^;]*)\s*;?", conn or ""):
        v = m.group(2)
        kv[m.group(1).strip().lower()] = v[1:-1].replace("}}", "}") if v.startswith("{") else v
    sql = "uid" in kv or "user id" in kv
    yes = lambda k, d: (kv.get(k, d) or d).lower() in ("yes", "true", "mandatory", "strict")  # noqa: E731
    return {"server": kv.get("server", kv.get("data source", "localhost")),
            "database": kv.get("database", kv.get("initial catalog", "")),
            "auth": "sql" if sql else "windows", "username": kv.get("uid", kv.get("user id", "")),
            "has_password": bool(kv.get("pwd") or kv.get("password")),
            "encrypt": yes("encrypt", "yes" if "18" in kv.get("driver", "") else "no"),
            "trust_server_certificate": yes("trustservercertificate", "no")}


# ------------------------------------------------------------------ dosya
def load_connections() -> dict[str, Any] | None:
    try:
        return json.loads(CONNECTIONS_FILE.read_text(encoding="utf-8")) if CONNECTIONS_FILE.exists() else None
    except (OSError, json.JSONDecodeError) as e:
        log.warning("connections.json okunamadı: %s", e)
        return None


def save_connections(cfg: dict[str, Any]) -> None:
    """Bölüm bazlı kayıt: verilen bölümler (data / dictionary / llm) güncellenir, diğerleri korunur.
    Değeri None olan bölüm silinir; dosyada bölüm kalmazsa dosya silinir."""
    with _lock:
        cur = load_connections() or {}
        for k, v in cfg.items():
            if v is None:
                cur.pop(k, None)
            else:
                cur[k] = v
        if not cur:
            CONNECTIONS_FILE.unlink(missing_ok=True)
            return
        CONNECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONNECTIONS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CONNECTIONS_FILE)


# ------------------------------------------------------------------ dil modeli (LLM) bağlantısı
def llm_overrides() -> dict[str, Any]:
    """connections.json 'llm' bölümü → Settings alanları (.env'deki LLM_* / VISION_* yerine geçer)."""
    cfg = (load_connections() or {}).get("llm")
    if not cfg:
        return {}
    out: dict[str, Any] = {"llm_base_url": cfg.get("base_url") or "", "llm_model": cfg.get("model") or "",
                           "llm_api_key": unprotect(cfg.get("api_key_enc") or "") or "EMPTY",
                           "llm_tool_mode": cfg.get("tool_mode") or "auto", "llm_extra_body": cfg.get("extra_body") or None}
    if cfg.get("max_tokens"):                       # arayüzden verilen yanıt token sınırı (.env'dekinin yerine)
        out["llm_max_tokens"] = int(cfg["max_tokens"])
    v = cfg.get("vision") or {}
    if not v.get("enabled", True) or not v.get("model"):
        out.update(vision_model=None, vision_base_url=None, vision_api_key=None)
    elif v.get("same_as_main", True):
        out.update(vision_model=v["model"], vision_base_url=None, vision_api_key=None)
    else:
        out.update(vision_model=v["model"], vision_base_url=v.get("base_url") or None,
                   vision_api_key=unprotect(v.get("api_key_enc") or "") or None)
    return out


def llm_settings(settings: Settings) -> Settings:
    """Dil modeli için geçerli ayarlar: .env + arayüzden kaydedilen LLM bağlantısı."""
    ov = llm_overrides()
    return settings.model_copy(update=ov) if ov else settings


def llm_effective(settings: Settings) -> dict[str, Any]:
    """Arayüz formu için geçerli LLM ayarları (anahtarlar hariç)."""
    cfg = (load_connections() or {}).get("llm")
    s = llm_settings(settings)
    vision_same = not s.vision_base_url and not s.vision_api_key
    return {
        "source": "ui" if cfg else "env",
        "base_url": s.llm_base_url, "model": s.llm_model, "tool_mode": s.llm_tool_mode,
        "extra_body": s.llm_extra_body, "has_api_key": bool(s.llm_api_key and s.llm_api_key != "EMPTY"),
        "max_tokens": s.llm_max_tokens or None,     # None / 0 = otomatik
        "vision": {"enabled": bool(s.vision_model), "model": s.vision_model or "", "same_as_main": vision_same,
                   "base_url": s.vision_base_url or "", "has_api_key": bool(s.vision_api_key)},
    }


DICTIONARY_FILES_DIR = BACKEND_DIR / "config" / "dictionary_files"


def _replace_db(odbc: str, database: str) -> str:
    """ODBC cümlesinde DATABASE değerini değiştirir (yoksa ekler)."""
    if re.search(r"(?i)\b(DATABASE|Initial Catalog)\s*=", odbc or ""):
        return re.sub(r"(?i)\b(DATABASE|Initial Catalog)\s*=\s*(\{[^}]*\}|[^;]*)", f"DATABASE={database}", odbc, count=1)
    return (odbc or "").rstrip(";") + f";DATABASE={database};"


def dictionary_conn_fields(cfg: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    """Sözlük bağlantı alanları. "Veri kaynağıyla aynı" (SQL Server) ise veri kaynağının GEÇERLİ ayarı kullanılır:
    arayüzden kaydedilmişse o, değilse .env (SQLSERVER_ODBC) — veri kaynağı ve sözlük ayrı ayrı kaydedilebilir."""
    d = dict(cfg.get("dictionary") or {})
    if (d.get("kind") or "sqlserver") == "sqlserver" and d.get("same_as_data", True):
        db = d.get("database") or DEFAULT_DICTIONARY_DB
        if cfg.get("data"):
            return {**cfg["data"], "database": db}
        env = (settings or get_settings()).sqlserver_odbc
        return {**parse_odbc(env), "database": db, "odbc": _replace_db(env, db)}
    return d


def dictionary_sources() -> dict[str, list[str]] | None:
    """Arayüzden seçilen sözlük tabloları (rol → tablolar); yoksa dictionary.toml sorguları kullanılır."""
    sd = saved_dictionary()
    return sd[1] if sd else None


def data_odbc(settings: Settings) -> str:
    cfg = load_connections()
    return build_odbc(cfg["data"]) if cfg and cfg.get("data") else settings.sqlserver_odbc


def _db_list(v: Any) -> list[str]:
    items = v if isinstance(v, list) else str(v or "").replace(";", ",").split(",")
    out: list[str] = []
    for x in items:
        x = str(x).strip().strip("[]")
        if x and x.lower() not in {o.lower() for o in out}:
            out.append(x)
    return out


def extra_databases(settings: Settings) -> list[str]:
    """Veri kaynağının ek veritabanları (aynı sunucu): arayüz (connections.json data.extra_databases) ya da .env
    SQLSERVER_EXTRA_DATABASES. Birincil veritabanı (DATABASE=) listede olsa bile çıkarılır."""
    cfg = load_connections()
    if cfg and cfg.get("data"):
        extras, primary = _db_list(cfg["data"].get("extra_databases")), cfg["data"].get("database") or ""
    else:
        extras, primary = _db_list(settings.sqlserver_extra_databases), parse_odbc(settings.sqlserver_odbc).get("database") or ""
    return [d for d in extras if d.lower() != str(primary).lower()]


def database_odbc(settings: Settings, database: str) -> str:
    """Veri bağlantısının aynı sunucu / kimlik bilgileriyle başka bir veritabanına bağlantı cümlesi."""
    return _replace_db(data_odbc(settings), database)


def open_dictionary_reader(fields: dict[str, Any]):
    """Sözlük kaynağına göre okuyucu + bağlantı bilgisi: SQL Server, MySQL, PostgreSQL ya da Excel."""
    from app.dictionary.sources import ExcelReader, MySQLReader, PostgresReader, SqlServerReader

    kind = fields.get("kind") or "sqlserver"
    if kind == "none":
        return None, {"server_name": "—", "database": "Sözlük kullanılmıyor", "version": "yalnız veritabanı kataloğu", "driver": ""}
    pwd = fields.get("password")
    if pwd is None:
        pwd = unprotect(fields.get("password_enc") or "")
    if kind == "excel":
        r = ExcelReader(fields.get("excel_path") or "")
        return r, r.probe()
    if kind == "mysql":
        r = MySQLReader(fields.get("server") or "localhost", fields.get("port") or 3306, fields.get("username") or "",
                        pwd, fields.get("database") or "", ssl=bool(fields.get("encrypt")))
        return r, r.probe()
    if kind == "postgres":
        r = PostgresReader(fields.get("server") or "localhost", fields.get("port") or 5432, fields.get("username") or "",
                           pwd, fields.get("database") or "", ssl=bool(fields.get("encrypt")))
        return r, r.probe()
    from app.data.connector import SqlServerConnector
    odbc = fields.get("odbc") or build_odbc({**fields, "password": pwd})   # .env'deki veri bağlantısından türetilmişse olduğu gibi
    con = SqlServerConnector(odbc, 30)
    rows = con.execute("SELECT @@SERVERNAME, DB_NAME(), CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(64)), SUSER_SNAME()", 1).rows
    srv, db, ver, login = rows[0]
    return SqlServerReader(con), {"server_name": srv, "database": db, "version": f"SQL Server {ver}", "login": login,
                                  "driver": con._odbc.split(";")[0].replace("DRIVER=", "").strip("{}")}


def saved_dictionary() -> tuple[dict[str, Any], dict[str, list[str]]] | None:
    """Arayüzden kaydedilmiş sözlük kaynağı: (bağlantı alanları, rol → tablolar / sayfalar). Yoksa dictionary.toml."""
    cfg = load_connections()
    if not (cfg and cfg.get("dictionary")):
        return None
    from app.dictionary.sources import default_sources
    d = cfg["dictionary"]
    fields = dictionary_conn_fields(cfg)
    fields["kind"] = d.get("kind") or "sqlserver"
    fields["mappings"] = d.get("mappings") or {}   # kaynak → alan → başlık (elle seçilen / içerikten bulunan)
    return fields, d.get("sources") or default_sources(fields["kind"])


def dictionary_odbc(settings: Settings) -> str | None:
    """Sözlük bağlantısı: connections.json → dictionary.toml odbc → (None: veri bağlantısı)."""
    cfg = load_connections()
    if cfg and cfg.get("dictionary"):
        if (cfg["dictionary"].get("kind") or "sqlserver") != "sqlserver":
            return None
        f = dictionary_conn_fields(cfg, settings)
        return f.get("odbc") or build_odbc(f)
    toml = load_toml(settings.dictionary_config)
    return toml.get("odbc") if toml.get("source", "data") == "odbc" else None


def _public(d: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in d.items() if k not in ("password", "password_enc", "odbc")}
    out["has_password"] = bool(d.get("password_enc"))
    return out


def effective(settings: Settings) -> dict[str, Any]:
    """Arayüz formu için şu an geçerli ayarlar (şifre hariç). Veri kaynağı ve sözlük ayrı ayrı 'ui' ya da 'env' olabilir."""
    cfg = load_connections() or {}
    data_src = "ui" if cfg.get("data") else "env"
    data = _public(cfg["data"]) if cfg.get("data") else parse_odbc(settings.sqlserver_odbc)
    data["extra_databases"] = extra_databases(settings)
    if cfg.get("dictionary"):
        dic = _public(cfg["dictionary"])
        dic.setdefault("kind", "sqlserver")
        dic.setdefault("same_as_data", True)
        dic.setdefault("database", DEFAULT_DICTIONARY_DB)
        dic["sources"] = dic.get("sources") or _default_sources(dic["kind"])
        if dic["kind"] == "sqlserver" and dic["same_as_data"]:   # formda veri kaynağının alanları görünsün
            dic = {**{k: data.get(k) for k in ("server", "auth", "username", "encrypt", "trust_server_certificate")}, **dic}
        dic_src = "ui"
    else:
        toml_odbc = None
        toml = load_toml(settings.dictionary_config)
        if toml.get("source", "data") == "odbc":
            toml_odbc = toml.get("odbc")
        d = parse_odbc(toml_odbc) if toml_odbc else {**data, "database": data.get("database")}
        same = all(d.get(k) == data.get(k) for k in ("server", "auth", "username"))
        dic = {**d, "kind": "sqlserver", "same_as_data": same, "database": d.get("database") or DEFAULT_DICTIONARY_DB,
               "sources": _default_sources("sqlserver")}
        dic_src = "env"
    return {"source": "ui" if "ui" in (data_src, dic_src) else "env", "data_source": data_src, "dictionary_source": dic_src,
            "data": data, "dictionary": dic}


def _default_sources(kind: str = "sqlserver") -> dict[str, list[str]]:
    from app.dictionary.sources import default_sources
    return default_sources(kind)


# ------------------------------------------------------------------ keşif
def local_instances() -> list[str]:
    """Bu bilgisayarda kurulu SQL Server instance'ları (kayıt defteri)."""
    out: list[str] = []
    if sys.platform != "win32":
        return out
    import winreg

    host = socket.gethostname()
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Microsoft SQL Server\Instance Names\SQL",
                                0, winreg.KEY_READ | view) as k:
                i = 0
                while True:
                    try:
                        name, _, _ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    out.append("localhost" if name.upper() == "MSSQLSERVER" else f"localhost\\{name}")
                    i += 1
        except OSError:
            continue
    seen: list[str] = []
    for x in out or []:
        if x not in seen:
            seen.append(x)
    return seen or []


def network_instances(timeout: float = 1.5) -> list[str]:
    """Ağdaki SQL Server Browser servislerine UDP 1434 yayını (güvenlik duvarı engelleyebilir)."""
    found: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(timeout)
        s.sendto(b"\x02", ("255.255.255.255", 1434))
        while True:
            try:
                data, _ = s.recvfrom(65535)
            except (socket.timeout, OSError):
                break
            text = data[3:].decode("ascii", "ignore")
            for chunk in text.split(";;"):
                f = chunk.split(";")
                kv = {f[i].lower(): f[i + 1] for i in range(0, len(f) - 1, 2)}
                if kv.get("servername"):
                    inst = kv.get("instancename", "")
                    name = kv["servername"] if inst.upper() in ("", "MSSQLSERVER") else f"{kv['servername']}\\{inst}"
                    if name not in found:
                        found.append(name)
        s.close()
    except OSError as e:
        log.info("Ağ taraması yapılamadı: %s", e)
    return found


# ------------------------------------------------------------------ platform (Vitrin) meta veritabanı
def meta_fields() -> dict[str, Any] | None:
    """Bağlantı Ayarları → Platform Veritabanı (connections.json 'meta' bölümü)."""
    return (load_connections() or {}).get("meta") or None


def meta_odbc(settings: Settings) -> str | None:
    """Arayüzden kaydedilen meta veritabanı, yoksa .env META_ODBC; ikisi de yoksa None (yerel SQLite)."""
    m = meta_fields()
    if m:
        return build_odbc(m)
    return settings.meta_odbc or None


def meta_public(settings: Settings) -> dict[str, Any]:
    m = meta_fields()
    if m:
        return {"source": "ui", **_public(m)}
    if settings.meta_odbc:
        return {"source": "env", **parse_odbc(settings.meta_odbc)}
    return {"source": "default", "server": "", "database": "BI_Lens_Meta", "auth": "windows"}
