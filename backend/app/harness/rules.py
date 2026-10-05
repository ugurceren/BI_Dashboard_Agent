"""Veritabanına özgü kurallar (ör. EDWDM): yerel LLM'in sistem talimatına aşamaya göre eklenir.

Kaynak: backend/config/data_rules/<VERITABANI>.md — veri kaynağının veritabanı adıyla eşleşen dosya (büyük/küçük harf
duyarsız). "## " başlıklı bölümler aşamalara ayrılır:
    Her aşama / Genel → tüm aşamalar · İhtiyaç … → requirements · Veri … → data · Tasarım … → design
Ayrıca kullanıcının yetkisiyle görünen view yetki seviyeleri (Masked / PersonnelExcluded …) dinamik olarak eklenir;
LLM hangi seviyeyi kullanabileceğini bilir ve yetkisinin üstünde bir view adı türetmez.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.config import BACKEND_DIR

RULES_DIR = BACKEND_DIR / "config" / "data_rules"
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_PHASE_OF = [(re.compile(r"^(her|genel|tüm|tum)", re.I), "all"), (re.compile(r"^ihtiya", re.I), "requirements"),
             (re.compile(r"^veri", re.I), "data"), (re.compile(r"^tasar", re.I), "design")]


def rules_file(database: str | None, rules_dir: Path = RULES_DIR) -> Path | None:
    if not database or not rules_dir.is_dir():
        return None
    want = database.strip().lower()
    return next((p for p in sorted(rules_dir.glob("*.md")) if p.stem.lower() == want), None)


def parse_sections(text: str) -> dict[str, list[str]]:
    """Markdown → {aşama: [bölüm metinleri]}; ilk '## ' öncesi (başlık / açıklama) atılır."""
    text = _COMMENT.sub("", text)
    out: dict[str, list[str]] = {}
    for block in re.split(r"(?m)^## +", text)[1:]:
        title, _, body = block.partition("\n")
        phase = next((p for rx, p in _PHASE_OF if rx.search(title.strip())), None)
        if phase and body.strip():
            out.setdefault(phase, []).append(f"### {title.strip()}\n{body.strip()}")
    return out


def access_levels(dd, usable) -> list[tuple[str, int]]:
    """Kullanıcının görebildiği yetki seviyeleri: [(seviye, view sayısı)] — yalnız varyantı olan veri setleri."""
    variants = getattr(dd, "VIEW_VARIANTS", [])
    if not variants:
        return []
    bases_with_variants = {t.variant_of for t in dd.tables.values() if getattr(t, "variant_of", "")}
    counts: dict[str, int] = {}
    for t in dd.tables.values():
        if not usable(t):
            continue
        if getattr(t, "variant_of", ""):
            suffix = next((v[0] for v in variants if t.name.endswith(v[0].lower())), None)
            if suffix:
                counts[suffix] = counts.get(suffix, 0) + 1
        elif t.name in bases_with_variants:
            counts["(ek yok)"] = counts.get("(ek yok)", 0) + 1
    order = ["(ek yok)"] + [v[0] for v in variants]
    return [(k, counts[k]) for k in order if k in counts]


def phase_rules(services: Any, session: Any, rules_dir: Path = RULES_DIR) -> str:
    """Bu oturumun aşaması için veritabanı kuralları + erişilebilir yetki seviyeleri (yoksa boş metin)."""
    dd = services.dictionary
    f = rules_file(getattr(dd, "database", None), rules_dir)
    if f is None:
        return ""
    try:
        sections = parse_sections(f.read_text(encoding="utf-8"))
    except OSError:
        return ""
    parts = sections.get("all", []) + sections.get(session.phase, [])
    if not parts:
        return ""
    head = f"## Kurum veri kuralları — {dd.database} (bu veritabanına özgü; genel kurallarla çelişirse BUNLAR geçerli)"
    out = [head, *parts]
    if session.phase in ("requirements", "data") and dd.in_scope("view_variant_databases"):
        pol = services.policy(session.user_role)
        levels = access_levels(dd, lambda t: dd.usable(t) and not pol.denial_reason(t.name))
        if levels:
            out.append("### Erişilebilir yetki seviyeleri (bu kullanıcı)\n" + "\n".join(
                f"- {lvl}: {n} view" for lvl, n in levels)
                + "\nYalnız bu seviyelerdeki view'ları kullan; listede olmayan bir seviyenin view adını türetme.")
    return "\n\n".join(out)
