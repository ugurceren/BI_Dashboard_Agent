"""Platform yetkileri (Vitrin): kim ne yapabilir.

İki eksen:
  platform rolü  admin | builder | viewer — uygulamada neye erişir (ayarlar, rapor tasarımı, yalnız Vitrin)
  veri rolü      policy.toml [roles.*]     — hangi veriyi görür (şema/tablo, kişisel veri, satır limiti)

Çözüm sırası (ilk bulunan kazanır):
  1. PLATFORM_ADMINS (.env) → admin (kurulumda kilitlenmemek için)
  2. meta DB rol ataması: kullanıcı, sonra AD grupları (en yetkili platform rolü)
  3. policy.toml [platform] users / groups
  4. policy.toml [platform] default_role (yoksa viewer)
Masaüstü modunda (PLATFORM_MODE=desktop) tek kullanıcı vardır ve admin sayılır — bugünkü davranış.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from app.config import Settings, load_toml
from app.identity import Identity
from app.meta.store import PLATFORM_ROLES, MetaStore, norm, short

log = logging.getLogger(__name__)

RANK = {r: i for i, r in enumerate(PLATFORM_ROLES)}   # viewer < builder < admin


def at_least(ident: Identity, role: str) -> bool:
    return RANK.get(ident.platform_role, -1) >= RANK[role]


def _matches_user(principal: str, username: str) -> bool:
    p = norm(principal)
    return p == norm(username) or ("\\" not in p and p == short(username))


def _admins(settings: Settings) -> list[str]:
    return [x for x in (a.strip() for a in settings.platform_admins.split(",")) if x]


def _platform_cfg(settings: Settings) -> dict[str, Any]:
    try:
        return load_toml(settings.policy_config).get("platform", {}) or {}
    except (OSError, ValueError):
        return {}


def _known_data_roles(settings: Settings) -> set[str]:
    try:
        return set(load_toml(settings.policy_config).get("roles", {}))
    except (OSError, ValueError):
        return set()


def resolve(settings: Settings, meta: MetaStore | None, ident: Identity) -> Identity:
    """Kimliğe platform rolünü ve (atama varsa) veri rolünü ekler."""
    if settings.platform_mode != "server":
        data_role = ident.role
        if meta is not None:
            a = next((a for a in _safe_assignments(meta) if a.principal_type == "user"
                      and _matches_user(a.principal, ident.username) and a.data_role), None)
            data_role = a.data_role if a and a.data_role in _known_data_roles(settings) else data_role
        return replace(ident, platform_role="admin", platform_mode="desktop", role=data_role)

    groups = {norm(g) for g in ident.groups}
    assigns = _safe_assignments(meta) if meta is not None else []
    user_a = next((a for a in assigns if a.principal_type == "user" and _matches_user(a.principal, ident.username)), None)
    group_a = sorted((a for a in assigns if a.principal_type == "group" and norm(a.principal) in groups),
                     key=lambda a: (-RANK.get(a.platform_role or "", -1), a.principal))
    cfg = _platform_cfg(settings)

    platform: str | None = None
    if any(_matches_user(x, ident.username) for x in _admins(settings)):
        platform = "admin"
    elif user_a and user_a.platform_role in RANK:
        platform = user_a.platform_role
    elif group_a and group_a[0].platform_role in RANK:
        platform = group_a[0].platform_role
    else:
        users = {norm(k): v for k, v in (cfg.get("users") or {}).items()}
        hit = next((v for k, v in users.items() if _matches_user(k, ident.username)), None)
        if hit is None:
            gmap = {norm(k): v for k, v in (cfg.get("groups") or {}).items()}
            hits = [gmap[g] for g in groups if g in gmap]
            hit = max(hits, key=lambda r: RANK.get(r, -1)) if hits else None
        platform = hit if hit in RANK else None
    platform = platform or (cfg.get("default_role") if cfg.get("default_role") in RANK else "viewer")

    known = _known_data_roles(settings)
    data_role = ident.role
    for a in ([user_a] if user_a else []) + group_a:
        if a.data_role and a.data_role in known:
            data_role = a.data_role
            break
    return replace(ident, platform_role=platform, platform_mode="server", role=data_role)


def _safe_assignments(meta: MetaStore):
    try:
        return meta.assignments()
    except Exception as e:  # noqa: BLE001 — meta DB erişilemezse varsayılan roller
        log.warning("Rol atamaları okunamadı: %s", e)
        return []


# ---------------------------------------------------------------- rapor erişimi
def can_view_report(ident: Identity, report: dict[str, Any], grants: list[dict[str, Any]]) -> bool:
    if at_least(ident, "admin") or _matches_user(report.get("owner") or "", ident.username):
        return True
    if report.get("status") != "active":
        return False
    return _grant_for(ident, grants) is not None


def can_export_report(ident: Identity, report: dict[str, Any], grants: list[dict[str, Any]]) -> bool:
    if at_least(ident, "admin") or _matches_user(report.get("owner") or "", ident.username):
        return True
    g = _grant_for(ident, grants)
    return bool(g and g.get("can_export")) and report.get("status") == "active"


def _grant_for(ident: Identity, grants: list[dict[str, Any]]) -> dict[str, Any] | None:
    groups = {norm(g) for g in ident.groups}
    best = None
    for g in grants:
        hit = (g["principal_type"] == "user" and _matches_user(g["principal"], ident.username)) or \
              (g["principal_type"] == "group" and norm(g["principal"]) in groups)
        if hit and (best is None or (g.get("can_export") and not best.get("can_export"))):
            best = g
    return best


def owns(ident: Identity, owner: str | None) -> bool:
    return bool(owner) and _matches_user(owner or "", ident.username)
