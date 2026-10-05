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
SQL_STANDARDS = "SQL_STANDARTLARI.md"   # nolock_databases'teki veritabanları için ortak (veri ambarı kılavuzu)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_PHASE_OF = [(re.compile(r"^(her|genel|tüm|tum)", re.I), "all"), (re.compile(r"^ihtiya", re.I), "requirements"),
             (re.compile(r"^veri", re.I), "data"), (re.compile(r"^tasar", re.I), "design")]


def rules_file(database: str | None, rules_dir: Path = RULES_DIR) -> Path | None:
    if not database or not rules_dir.is_dir():
        return None
    want = database.strip().lower()
    return next((p for p in sorted(rules_dir.glob("*.md")) if p.stem.lower() == want and p.name != SQL_STANDARDS), None)


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


def databases_block(dd) -> str:
    """Birden çok veritabanı seçiliyse: hangileri ve nesne adlarının nasıl yazılacağı."""
    extras = list(getattr(dd, "extra_databases", []) or [])
    if not extras:
        return ""
    primary = getattr(dd, "database", None) or "bağlı veritabanı"
    lines = [f"- {primary} (bağlı / birincil): nesneler şema.nesne (ör. dbo.Tablo)"]
    errors = getattr(dd, "extra_errors", {}) or {}
    for db in extras:
        lines.append(f"- {db}: nesneler {db}.şema.nesne (ör. {db}.dbo.Tablo)"
                     + (f" — ŞU AN OKUNAMIYOR ({errors[db][:120]})" if db in errors else ""))
    return ("## Veri kaynağı veritabanları\n" + "\n".join(lines)
            + "\nTablo / view adlarını search_dictionary ve get_table_details sonuçlarında yazdığı gibi kullan; farklı "
              "veritabanlarındaki nesneler aynı sorguda birleştirilebilir. Seçili olmayan bir veritabanını sorgulama.")


def phase_rules(services: Any, session: Any, rules_dir: Path = RULES_DIR) -> str:
    """Bu oturumun aşaması için: seçili veritabanları + her veritabanının kural dosyası + erişilebilir yetki seviyeleri."""
    dd = services.dictionary
    out: list[str] = []
    block = databases_block(dd)
    if block:
        out.append(block)
    dbs = dd.databases() if callable(getattr(dd, "databases", None)) else ([dd.database] if getattr(dd, "database", None) else [])
    for db in dbs:
        f = rules_file(db, rules_dir)
        if f is None:
            continue
        try:
            sections = parse_sections(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        parts = sections.get("all", []) + sections.get(session.phase, [])
        if parts:
            scope = "bu veritabanına özgü" if len(dbs) == 1 else f"yalnız {db} nesneleri için"
            out += [f"## Kurum veri kuralları — {db} ({scope}; genel kurallarla çelişirse BUNLAR geçerli)", *parts]
    # veri ambarı SQL standartları: seçili veritabanlarından biri nolock_databases listesindeyse (EDWDM, EDW …)
    std = rules_dir / SQL_STANDARDS
    nolock_db = getattr(dd, "nolock_db", None)
    in_std = [db for db in dbs if callable(nolock_db) and nolock_db(db)]
    if in_std and std.is_file():
        try:
            parts = (lambda sec: sec.get("all", []) + sec.get(session.phase, []))(parse_sections(std.read_text(encoding="utf-8")))
        except OSError:
            parts = []
        if parts:
            out += [f"## Veri ambarı SQL kullanım standartları (geçerli veritabanları: {', '.join(in_std)})", *parts]
    if not out:
        return ""
    if session.phase in ("requirements", "data") and any(dd.in_scope("view_variant_databases", d) for d in dbs or [None]):
        pol = services.policy(session.user_role)
        levels = access_levels(dd, lambda t: dd.usable(t) and not pol.denial_reason(t.name))
        if levels:
            out.append("### Erişilebilir yetki seviyeleri (bu kullanıcı)\n" + "\n".join(
                f"- {lvl}: {n} view" for lvl, n in levels)
                + "\nYalnız bu seviyelerdeki view'ları kullan; listede olmayan bir seviyenin view adını türetme.")
    return "\n\n".join(out)
