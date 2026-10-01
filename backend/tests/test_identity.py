"""Kimlik: başlık / Windows kaynağı ve policy.toml [identity] rol eşlemesi."""

from app.config import Settings
from app.identity import _resolve_role, current_identity

POLICY = r"""
[identity]
default_role = "standart"
users = { 'KURUM\ali' = "admin" }
groups = { "BI_Admins" = "admin" }
"""


def _settings(tmp_path, **kw):
    pol = tmp_path / "policy.toml"
    pol.write_text(POLICY, encoding="utf-8")
    return Settings(_env_file=None, policy_config=pol, **kw)


def test_role_mapping(tmp_path):
    s = _settings(tmp_path)
    assert _resolve_role(s, "KURUM\\ali", []) == "admin"
    assert _resolve_role(s, "KURUM\\veli", ["Muhasebe", "BI_Admins"]) == "admin"
    assert _resolve_role(s, "KURUM\\veli", ["Muhasebe"]) == "standart"


def test_remote_user_header_and_windows_fallback(tmp_path):
    ident = current_identity(_settings(tmp_path, trust_remote_user_header=True), {"X-Remote-User": "KURUM\\ali"})
    assert ident.username == "KURUM\\ali" and ident.role == "admin" and ident.source == "header"
    local = current_identity(_settings(tmp_path), {"X-Remote-User": "KURUM\\ali"})
    assert local.source == "windows" and local.username != "KURUM\\ali", "başlığa varsayılan olarak güvenilmemeli"


def test_broken_policy_falls_back(tmp_path):
    pol = tmp_path / "policy.toml"
    pol.write_text('[identity]\nusers = { "A\\x" = "admin" }\n', encoding="utf-8")   # geçersiz TOML kaçışı
    assert _resolve_role(Settings(_env_file=None, policy_config=pol), "A\\x", []) == "standart"


def test_unknown_role_falls_back_to_default(settings, services):
    """Eski .env / kayıtlardaki tanımsız rol adı varsayılan 'standart' role düşer."""
    from app.identity import _resolve_role

    old = settings.model_copy(update={"user_role": "eski_rol"})
    assert _resolve_role(old, r"KURUM\ali", []) == "standart"
    assert services.policy("eski_rol").name == "standart"
    assert services.policy("admin").name == "admin"
