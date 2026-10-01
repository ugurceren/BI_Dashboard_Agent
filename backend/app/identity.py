"""Kullanıcı kimliği: kim bağlandı, adı ne, rolü ne.

Sıra (ilk bulunan kazanır):
  1. Ters proxy başlığı (IIS / nginx + Windows Kimlik Doğrulaması / Kerberos): `X-Remote-User` — sunucu kurulumunda.
  2. Backend'i çalıştıran Windows oturumu (tek kullanıcılı masaüstü kurulumu): DOMAIN\\kullanıcı.
Ad / e-posta / departman / gruplar:
  a. LDAP (isteğe bağlı, LDAP_URL tanımlıysa; `pip install ldap3`): displayName, mail, department, title, memberOf.
  b. Windows API: etki alanındaki makinede GetUserNameExW(NameDisplay) adı AD'den getirir;
     yerel hesapta NetUserGetInfo tam adı.
Rol: config/policy.toml [identity] → kullanıcı eşlemesi, sonra grup eşlemesi, yoksa varsayılan rol.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

from app.config import Settings, load_toml

log = logging.getLogger(__name__)


@dataclass
class Identity:
    username: str                 # DOMAIN\kullanıcı
    display_name: str
    domain: str = ""
    email: str | None = None
    department: str | None = None
    title: str | None = None
    groups: list[str] = field(default_factory=list)
    role: str = "standart"
    source: str = "windows"       # header | windows | ldap
    domain_joined: bool = False

    def public(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- Windows
def _name_ex(fmt: int) -> str | None:
    if sys.platform != "win32":
        return None
    try:
        from ctypes import wintypes

        fn = ctypes.WinDLL("secur32").GetUserNameExW
        size = wintypes.ULONG(0)
        fn(fmt, None, ctypes.byref(size))
        if not size.value:
            return None
        buf = ctypes.create_unicode_buffer(size.value)
        return buf.value if fn(fmt, buf, ctypes.byref(size)) else None
    except Exception:  # noqa: BLE001
        return None


def _local_full_name(user: str) -> str | None:
    """Yerel hesabın tam adı (NetUserGetInfo, düzey 10)."""
    if sys.platform != "win32":
        return None
    try:
        from ctypes import wintypes

        class USER_INFO_10(ctypes.Structure):
            _fields_ = [("name", wintypes.LPWSTR), ("comment", wintypes.LPWSTR),
                        ("usr_comment", wintypes.LPWSTR), ("full_name", wintypes.LPWSTR)]

        netapi = ctypes.WinDLL("netapi32")
        ptr = ctypes.POINTER(USER_INFO_10)()
        if netapi.NetUserGetInfo(None, user, 10, ctypes.byref(ptr)) != 0:
            return None
        try:
            return (ptr.contents.full_name or "").strip() or None
        finally:
            netapi.NetApiBufferFree(ptr)
    except Exception:  # noqa: BLE001
        return None


@lru_cache
def _process_identity() -> tuple[str, str, str, str | None, bool]:
    """(DOMAIN\\kullanıcı, domain, kısa ad, görünen ad, domain'e bağlı mı)"""
    sam = _name_ex(2) or f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME') or os.environ.get('USER', 'kullanici')}"
    domain, _, short = sam.rpartition("\\")
    display = _name_ex(3)                      # yalnız etki alanında (AD) döner
    joined = display is not None or bool(os.environ.get("USERDNSDOMAIN"))
    if not display:
        display = _local_full_name(short)
    return sam, domain, short, display, joined


# ---------------------------------------------------------------- LDAP (isteğe bağlı)
def _ldap_lookup(settings: Settings, short: str) -> dict[str, Any] | None:
    if not settings.ldap_url:
        return None
    try:
        import ldap3  # type: ignore
    except ImportError:
        log.warning("LDAP_URL tanımlı ama ldap3 kurulu değil (pip install ldap3).")
        return None
    try:
        server = ldap3.Server(settings.ldap_url, get_info=ldap3.NONE, connect_timeout=5)
        auth = ldap3.NTLM if settings.ldap_bind_user and "\\" in settings.ldap_bind_user else ldap3.SIMPLE
        conn = ldap3.Connection(server, user=settings.ldap_bind_user, password=settings.ldap_bind_password,
                                authentication=auth if settings.ldap_bind_user else ldap3.ANONYMOUS, auto_bind=True,
                                receive_timeout=5)
        flt = settings.ldap_user_filter.format(user=ldap3.utils.conv.escape_filter_chars(short))
        conn.search(settings.ldap_base_dn, flt, attributes=["displayName", "mail", "department", "title", "memberOf"])
        if not conn.entries:
            return None
        e = conn.entries[0]
        groups = [str(g).split(",")[0].removeprefix("CN=") for g in (e.memberOf.values if "memberOf" in e else [])]
        val = lambda a: (str(e[a].value) if a in e and e[a].value else None)  # noqa: E731
        return {"display_name": val("displayName"), "email": val("mail"), "department": val("department"),
                "title": val("title"), "groups": groups}
    except Exception as ex:  # noqa: BLE001
        log.warning("LDAP sorgusu başarısız: %s", ex)
        return None


# ---------------------------------------------------------------- rol
def _resolve_role(settings: Settings, username: str, groups: list[str]) -> str:
    try:
        cfg = load_toml(settings.policy_config).get("identity", {})
    except (OSError, ValueError) as e:  # tomllib.TOMLDecodeError ⊂ ValueError
        log.warning("policy.toml [identity] okunamadı: %s", e)
        cfg = {}
    users = {k.lower(): v for k, v in (cfg.get("users") or {}).items()}
    short = username.rpartition("\\")[2].lower()
    if username.lower() in users:
        return users[username.lower()]
    if short in users:
        return users[short]
    gmap = {k.lower(): v for k, v in (cfg.get("groups") or {}).items()}
    for g in groups:
        if g.lower() in gmap:
            return _known_role(settings, gmap[g.lower()], cfg)
    return _known_role(settings, cfg.get("default_role") or settings.user_role, cfg)


def _known_role(settings: Settings, role: str, cfg: dict[str, Any]) -> str:
    """policy.toml'da tanımlı olmayan rol (ör. eski .env'deki USER_ROLE) varsayılan role düşer."""
    try:
        roles = set(load_toml(settings.policy_config).get("roles", {}))
    except (OSError, ValueError):
        return role
    if not roles or role in roles:
        return role
    default = cfg.get("default_role")
    return default if default in roles else ("standart" if "standart" in roles else sorted(roles)[0])


_ldap_cache: dict[str, dict[str, Any] | None] = {}


def current_identity(settings: Settings, headers: dict[str, str] | None = None) -> Identity:
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    remote = headers.get("x-remote-user") if settings.trust_remote_user_header else None
    if remote:
        username, source, joined = remote, "header", True
        domain, _, short = remote.rpartition("\\")
        display = None
    else:
        username, domain, short, display, joined = _process_identity()
        source = "windows"
    if short not in _ldap_cache:
        _ldap_cache[short] = _ldap_lookup(settings, short)
    ld = _ldap_cache[short] or {}
    if ld:
        source = "ldap"
    groups = ld.get("groups") or []
    return Identity(
        username=username, display_name=ld.get("display_name") or display or short, domain=domain,
        email=ld.get("email"), department=ld.get("department"), title=ld.get("title"), groups=groups,
        role=_resolve_role(settings, username, groups), source=source, domain_joined=joined,
    )
