"""Sözlükteki tablo / view adlarını veritabanı kataloğundaki gerçek nesnelere eşler.

Sözlükler (özellikle Excel) adları farklı yazabilir; hepsi kataloğa göre `şema.nesne` biçimine çevrilir:
  * köşeli parantez / tırnak / fazladan boşluk:  [dbo].[vw_Satis], "dbo"."vw_Satis", ' dbo.vw_Satis '
  * veritabanı / sunucu adıyla:                  EDWDM.dbo.vw_Satis, SUNUCU.EDWDM.dbo.vw_Satis
  * şemasız:                                     vw_Satis  → katalogda bu adla tek nesne varsa onun şeması (yoksa dbo)
  * şema ayrı kolonda:                           TABLE_SCHEMA + TABLE_NAME (collect birleştirir)
  * yanlış şema:                                 rpt.vw_Satis yoksa ve katalogda vw_Satis adlı TEK nesne varsa ona eşlenir
Katalogda karşılığı olmayan adlar olduğu gibi kalır (in_db=False olarak raporlanır).
"""

from __future__ import annotations

import re
from typing import Iterable

_PART = re.compile(r'\[((?:[^\]]|\]\])*)\]|"((?:[^"]|"")*)"|`([^`]*)`|([^.]+)')
_SPACE = re.compile(r"[\s ​﻿]+")


def split_name(name: object) -> list[str]:
    """'[EDWDM].[dbo].[vw_Satis]' → ['EDWDM', 'dbo', 'vw_Satis'] (parantez / tırnak içindeki nokta korunur)."""
    s = _SPACE.sub(" ", str(name or "")).strip()
    parts: list[str] = []
    for m in _PART.finditer(s):
        if m.group(1) is not None:
            p = m.group(1).replace("]]", "]")
        elif m.group(2) is not None:
            p = m.group(2).replace('""', '"')
        else:
            p = m.group(3) if m.group(3) is not None else m.group(4)
        p = p.strip()
        if p:
            parts.append(p)
    return parts


def canon(name: object) -> str:
    """Parantezsiz `şema.nesne` (veritabanı / sunucu öneki atılır); şemasızsa yalnız nesne adı."""
    parts = split_name(name)
    return ".".join(parts[-2:]) if parts else str(name or "").strip()


class NameResolver:
    def __init__(self, objects: Iterable[tuple[str, str]]):
        self.full: dict[str, str] = {}
        self.by_name: dict[str, list[str]] = {}
        for sch, obj in objects:
            disp = f"{sch}.{obj}"
            self.full[disp.lower()] = disp
            self.by_name.setdefault(str(obj).lower(), []).append(disp)
        self.changed: dict[str, str] = {}     # sözlükteki yazım → katalogdaki ad
        self.missing: dict[str, str] = {}     # katalogda bulunamayanlar (küçük harf → yazım)

    def resolve(self, name: object) -> str:
        raw = str(name or "")
        c = canon(raw)
        hit = self.full.get(c.lower())
        if hit is None:
            obj = c.rsplit(".", 1)[-1].lower()
            cands = self.by_name.get(obj, [])
            if len(cands) == 1:
                hit = cands[0]
            elif "." not in c:
                hit = next((x for x in cands if x.lower() == f"dbo.{obj}"), None)
        if hit is None:
            out = c if "." in c else f"dbo.{c}"
            self.missing[out.lower()] = out
            return out
        if hit.lower() != raw.strip().lower():
            self.changed[raw.strip()] = hit
        return hit
