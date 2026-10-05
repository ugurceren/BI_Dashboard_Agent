"""Sözlükteki tablo / view adlarını veritabanı kataloğundaki gerçek nesnelere eşler.

Sözlükler (özellikle Excel) adları farklı yazabilir; hepsi kataloğa göre `şema.nesne` biçimine çevrilir:
  * köşeli parantez / tırnak / fazladan boşluk:  [dbo].[vw_Satis], "dbo"."vw_Satis", ' dbo.vw_Satis '
  * veritabanı / sunucu adıyla:                  EDWDM.dbo.vw_Satis, SUNUCU.EDWDM.dbo.vw_Satis
    (seçili EK veritabanının adıyla yazılmışsa öneki korunur: EDW.dbo.X → edw.dbo.x; birincil veritabanında atılır)
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


def canon(name: object, extra_databases: tuple[str, ...] | list[str] = ()) -> str:
    """Parantezsiz `şema.nesne` (veritabanı / sunucu öneki atılır); şemasızsa yalnız nesne adı.
    Önek seçili bir EK veritabanıysa korunur: `EDW.şema.nesne`."""
    parts = split_name(name)
    if not parts:
        return str(name or "").strip()
    if len(parts) >= 3 and parts[-3].lower() in {d.lower() for d in extra_databases}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


class NameResolver:
    def __init__(self, objects: Iterable[tuple[str, str]], extra_databases: tuple[str, ...] | list[str] = ()):
        """objects: (şema, nesne) — ek veritabanı nesnelerinde şema 'EDW.dbo' biçimindedir."""
        self.extra = tuple(extra_databases)
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
        c = canon(raw, self.extra)
        hit = self.full.get(c.lower())
        if hit is None:
            obj = c.rsplit(".", 1)[-1].lower()
            cands = self.by_name.get(obj, [])
            db = c.split(".")[0].lower() if c.count(".") >= 2 else ""
            if db:   # veritabanı açıkça yazılmış: yalnız o veritabanında ara (başka veritabanına atlama)
                cands = [x for x in cands if x.count(".") >= 2 and x.split(".")[0].lower() == db]
            else:    # veritabanı yok: önce birincil veritabanı, orada yoksa tek eşleşme
                cands = [x for x in cands if x.count(".") < 2] or cands
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
