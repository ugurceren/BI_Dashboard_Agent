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
import contextlib
import contextvars
import hashlib
import json
import logging
import os
import re
import socket
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

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
# Arşivlenmiş bir veri kaynağının servisleri kurulurken load_connections() o kaynağın ayarlarını döndürür
# (build_services / DataDictionary.load değişmeden başka kaynak için çalışır). Yalnız bu bağlamda geçerlidir.
_override: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("bi_connections_override", default=None)


@contextlib.contextmanager
def override_connections(cfg: dict[str, Any]) -> Iterator[None]:
    token = _override.set(cfg)
    try:
        yield
    finally:
        _override.reset(token)


def load_connections() -> dict[str, Any] | None:
    ov = _override.get()
    if ov is not None:
        return ov
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
        if "data" in cfg or "dictionary" in cfg:
            _archive_active(cur)        # raporlar bu kaynağa bağlı kalabilsin: değişmeden önceki hali saklanır
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
    if cfg.get("timeout_s"):                        # arayüzden verilen yanıt bekleme süresi (.env LLM_TIMEOUT_S yerine)
        out["llm_timeout_s"] = float(cfg["timeout_s"])
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
        "timeout_s": s.llm_timeout_s,               # geçerli değer (arayüz > .env > 180)
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
        if cfg.get("data") and cfg["data"].get("odbc_enc"):     # arşivlenmiş .env kaynağı
            env = unprotect(cfg["data"]["odbc_enc"])
            return {**parse_odbc(env), "database": db, "odbc": _replace_db(env, db)}
        if cfg.get("data"):
            return {**cfg["data"], "database": db}
        env = (settings or get_settings()).sqlserver_odbc
        return {**parse_odbc(env), "database": db, "odbc": _replace_db(env, db)}
    return d


def dictionary_sources() -> dict[str, list[str]] | None:
    """Arayüzden seçilen sözlük tabloları (rol → tablolar); yoksa dictionary.toml sorguları kullanılır."""
    sd = saved_dictionary()
    return sd[1] if sd else None


def _data_section_odbc(d: dict[str, Any]) -> str:
    """Veri bölümü: alanlardan ya da (arşivlenmiş .env kaynağı) şifreli tam bağlantı cümlesinden."""
    return unprotect(d["odbc_enc"]) if d.get("odbc_enc") else build_odbc(d)


def data_odbc(settings: Settings) -> str:
    cfg = load_connections()
    return _data_section_odbc(cfg["data"]) if cfg and cfg.get("data") else settings.sqlserver_odbc


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
    if cfg and cfg.get("data") and cfg["data"].get("odbc_enc"):
        extras = _db_list(cfg["data"].get("extra_databases"))
        primary = parse_odbc(unprotect(cfg["data"]["odbc_enc"])).get("database") or ""
    elif cfg and cfg.get("data"):
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
    out = {k: v for k, v in d.items() if k not in ("password", "password_enc", "odbc", "odbc_enc")}
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


# ------------------------------------------------------------------ veri kaynağı kimliği ve arşiv
# Her rapor, veri kümeleri kaydedildiğinde etkin veri kaynağına "damgalanır" (Session.data_source). Bağlantı ayarı
# sonradan başka sunucu / veritabanına çevrilse de rapor kendi kaynağından okur: değişiklikten önceki ayar burada saklanır.
def _source_parts(data: dict[str, Any] | None, settings: Settings) -> tuple[str, str, list[str]]:
    if data and data.get("odbc_enc"):
        p = parse_odbc(unprotect(data["odbc_enc"]))
        return p.get("server") or "", p.get("database") or "", _db_list(data.get("extra_databases"))
    if data:
        return data.get("server") or "", data.get("database") or "", _db_list(data.get("extra_databases"))
    p = parse_odbc(settings.sqlserver_odbc)
    return p.get("server") or "", p.get("database") or "", _db_list(settings.sqlserver_extra_databases)


def source_id_of(data: dict[str, Any] | None, settings: Settings | None = None) -> str:
    """Kaynak kimliği: sunucu + veritabanı + ek veritabanları (kimlik bilgileri dahil değil; aynı yere dönülürse aynı)."""
    srv, db, extras = _source_parts(data, settings or get_settings())
    key = "|".join([srv.strip().lower(), db.strip().lower(), ",".join(sorted(x.lower() for x in extras))])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def source_label_of(data: dict[str, Any] | None, settings: Settings | None = None) -> str:
    srv, db, _ = _source_parts(data, settings or get_settings())
    return f"{srv or '?'} · {db or '?'}"


def active_source_id(settings: Settings | None = None) -> str:
    cfg = load_connections() or {}
    return source_id_of(cfg.get("data"), settings)


def _archive_active(cur: dict[str, Any]) -> None:
    """Etkin veri kaynağının (veri + sözlük ayarı) kopyası sources'a yazılır. .env kaynağında bağlantı cümlesi şifreli saklanır."""
    settings = get_settings()
    data = cur.get("data")
    if not data:
        if not settings.sqlserver_odbc:
            return
        data = {"odbc_enc": protect(settings.sqlserver_odbc), "extra_databases": _db_list(settings.sqlserver_extra_databases)}
    sid = source_id_of(data, settings)
    sources = dict(cur.get("sources") or {})
    sources[sid] = {"label": source_label_of(data, settings), "data": data, "dictionary": cur.get("dictionary"),
                    "archived_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    cur["sources"] = sources


def archived_source(sid: str) -> dict[str, Any] | None:
    return ((load_connections() or {}).get("sources") or {}).get(sid)


def source_config(sid: str) -> dict[str, Any] | None:
    """override_connections için: geçerli ayarlar + o kaynağın veri ve sözlük bölümleri."""
    arch = archived_source(sid)
    if not arch:
        return None
    cur = {k: v for k, v in (load_connections() or {}).items() if k not in ("data", "dictionary", "sources")}
    cur["data"] = arch["data"]
    if arch.get("dictionary"):
        cur["dictionary"] = arch["dictionary"]
    return cur


def list_sources(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Kayıtlı kaynaklar (etkin olan dahil), şifresiz."""
    settings = settings or get_settings()
    cfg = load_connections() or {}
    active = source_id_of(cfg.get("data"), settings)
    out: dict[str, dict[str, Any]] = {}
    for sid, a in (cfg.get("sources") or {}).items():
        d = a.get("data") or {}
        out[sid] = {"id": sid, "label": a.get("label") or source_label_of(d, settings), "active": sid == active,
                    "archived_at": a.get("archived_at"), "auth": "env" if d.get("odbc_enc") else (d.get("auth") or "windows"),
                    "username": d.get("username") or "", "has_password": bool(d.get("password_enc") or d.get("odbc_enc"))}
    if active not in out:
        out[active] = {"id": active, "label": source_label_of(cfg.get("data"), settings), "active": True, "archived_at": None,
                       "auth": (cfg.get("data") or {}).get("auth") or ("env" if not cfg.get("data") else "windows"),
                       "username": (cfg.get("data") or {}).get("username") or "", "has_password": True}
    for sid, x in out.items():
        x["publish"] = publish_account_public(sid)
    return sorted(out.values(), key=lambda x: (not x["active"], x["label"].lower()))


def update_source_credentials(sid: str, username: str | None, password: str | None) -> bool:
    """Arşivlenmiş kaynağın SQL kullanıcı / şifresini güncelle (şifre değişince raporlar okumaya devam etsin)."""
    with _lock:
        cur = load_connections() or {}
        arch = (cur.get("sources") or {}).get(sid)
        if not arch or arch["data"].get("odbc_enc"):
            return False
        d = dict(arch["data"])
        if username is not None:
            d["username"] = username
            d["auth"] = "sql" if username else "windows"      # kullanıcı adı boş: Windows oturumu
            if not username:
                d.pop("password_enc", None)
        if password and d["auth"] == "sql":
            d["password_enc"] = protect(password)
        arch["data"] = d
        tmp = CONNECTIONS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CONNECTIONS_FILE)
        return True


def delete_source(sid: str) -> bool:
    with _lock:
        cur = load_connections() or {}
        if sid not in (cur.get("sources") or {}):
            return False
        cur["sources"].pop(sid)
        if not cur["sources"]:
            cur.pop("sources")
        (cur.get("publish_accounts") or {}).pop(sid, None)
        if "publish_accounts" in cur and not cur["publish_accounts"]:
            cur.pop("publish_accounts")
        tmp = CONNECTIONS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CONNECTIONS_FILE)
        return True


# ------------------------------------------------------------------ yayın (Vitrin) hesabı
# Yayınlanmış raporların verisi, kaynağına tanımlanmışsa geniş okuma yetkili ayrı bir servis hesabıyla okunur (tasarım,
# sohbet, Sorgu Çalıştır her zaman normal bağlantıyla). İzleyicinin ne göreceğini yine BI Lens'in rol katmanı belirler.
# Kayıt kaynak kimliğine göre tutulur (connections.json → publish_accounts): veri kartı kaydedilince kaybolmaz,
# .env kaynağında ve arşivlenmiş kaynaklarda da çalışır.
_CRED_KEYS = re.compile(r"(?i)(?:^|;)\s*(?:uid|user id|pwd|password|trusted_connection|integrated security)\s*=\s*(?:\{(?:[^}]|\}\})*\}|[^;]*)")


def with_credentials(odbc: str, acc: dict[str, Any]) -> str:
    """Bağlantı cümlesinin kimlik bilgisini değiştirir (sunucu / veritabanı / şifreleme aynı kalır)."""
    base = _CRED_KEYS.sub("", odbc or "").strip(";")
    if acc.get("auth") == "sql":
        pwd = acc.get("password")
        if pwd is None:
            pwd = unprotect(acc.get("password_enc") or "")
        cred = f"UID={_braced(acc.get('username') or '')};PWD={{{pwd.replace('}', '}}')}}}"
    else:
        cred = "Trusted_Connection=yes"
    return f"{base};{cred};"


def process_account() -> str:
    """Backend'in çalıştığı Windows hesabı (Windows kimlik doğrulamasında SQL Server'a bu hesap bağlanır)."""
    dom, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{dom}\\{user}" if dom else user


def publish_account(sid: str | None) -> dict[str, Any] | None:
    acc = ((load_connections() or {}).get("publish_accounts") or {}).get(sid or "")
    return acc if acc and acc.get("auth") in ("sql", "windows") else None


def publish_account_public(sid: str | None) -> dict[str, Any]:
    acc = publish_account(sid)
    if not acc:
        return {"auth": "none"}
    return {"auth": acc["auth"], "username": acc.get("username") or "", "has_password": bool(acc.get("password_enc")),
            "updated_at": acc.get("updated_at"), "updated_by": acc.get("updated_by")}


def publish_odbc(sid: str | None, settings: Settings) -> str | None:
    """Kaynağın yayın hesabıyla bağlantı cümlesi; tanımlı değilse None (normal bağlantı kullanılır)."""
    acc = publish_account(sid)
    return with_credentials(data_odbc(settings), acc) if acc else None


def save_publish_account(sid: str, acc: dict[str, Any] | None, by: str = "") -> None:
    """acc None ya da auth 'none' → kaldırılır. SQL şifresi boş gelirse kayıtlı şifre korunur."""
    with _lock:
        cur = load_connections() or {}
        accounts = dict(cur.get("publish_accounts") or {})
        if not acc or acc.get("auth") not in ("sql", "windows"):
            accounts.pop(sid, None)
        else:
            old = accounts.get(sid) or {}
            new: dict[str, Any] = {"auth": acc["auth"], "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                   "updated_by": by}
            if acc["auth"] == "sql":
                new["username"] = acc.get("username") or ""
                if acc.get("password"):
                    new["password_enc"] = protect(acc["password"])
                elif old.get("auth") == "sql" and old.get("username") == new["username"] and old.get("password_enc"):
                    new["password_enc"] = old["password_enc"]
            accounts[sid] = new
        if accounts:
            cur["publish_accounts"] = accounts
        else:
            cur.pop("publish_accounts", None)
        if not cur:
            CONNECTIONS_FILE.unlink(missing_ok=True)
            return
        CONNECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONNECTIONS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CONNECTIONS_FILE)
