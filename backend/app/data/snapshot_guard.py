"""Günlük anlık görüntü (daily snapshot) koruması.

EDWDM gibi katmanlarda view'lar takvim tablosuyla joinlenir: her kayıt her gün için ayrı satır olarak tekrarlanır
(tarih kolonu: DataDate). Gün seçmeden yapılan toplamlar gün sayısıyla çarpılır (1 yıllık view'da ~365 kat) — sorgu
hatasız çalışır, sonuç makul görünür ama YANLIŞTIR. Bu modül her SELECT kapsamında (alt sorgular dahil) şunlara bakar:

  * SUM / COUNT kullanılıyorsa anlık görüntünün tarih kolonu sabitlenmiş olmalı:
      WHERE x.DataDate = ...  |  IN (...)  |  BETWEEN (tek gün değil, uyarı)  — ya da GROUP BY x.DataDate (günlük seri)
    Ay / yıl bazında gruplama (MONTH(DataDate), EOMONTH(DataDate) …) her ayın TÜM günlerini toplar: dönem başına
    tek gün seçilmeli (ör. WHERE x.DataDate = EOMONTH(x.DataDate)).
  * AVG / MIN / MAX / COUNT(DISTINCT …) gün seçilmeden kullanılırsa: tüm günler üzerinden hesaplanır — REDDEDİLİR
    (yalnız dönem bazında gruplanmış ortalama, ör. aylık AVG, uyarıyla geçer).
  * Toplama yoksa (detay satırları, TOP n *, DISTINCT / GROUP BY ile değer listesi) ve gün seçilmemişse: her gün için
    satır döner ve takvimle çoğaltılmış view tümüyle taranır — REDDEDİLİR.
  * Yalnız tarih kolonunu okuyan sorgu serbesttir (ör. SELECT MAX(DataDate) — son günü bulmak için).
  * İki anlık görüntü aynı kapsamda birleştiriliyorsa tarih kolonları eşlenmeli (a.DataDate = b.DataDate) ya da
    ikisi de ayrı ayrı sabitlenmeli; yoksa satırlar gün × gün çoğalır.
Sorgu konsolunda (kullanıcının kendi sorgusu, strict_joins=False) hatalar engellemez, uyarı olarak gösterilir.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlglot import exp

@dataclass
class SnapshotResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _scope_tables(select: exp.Select, dd=None) -> dict[str, str]:
    """Bu SELECT'in kendi FROM / JOIN'lerindeki tablolar: alias → şema.tablo (alt sorgular hariç)."""
    out: dict[str, str] = {}
    sources = []
    frm = select.args.get("from_") or select.args.get("from")   # sqlglot sürümüne göre anahtar adı
    if frm is not None:
        sources.append(frm.this)
    for j in select.args.get("joins") or []:
        sources.append(j.this)
    for s in sources:
        if isinstance(s, exp.Table) and s.db:
            from app.dictionary.repository import sql_table_key
            key = sql_table_key(s, dd) if dd is not None else f"{s.db}.{s.name}".lower()
            if key:
                out[s.alias_or_name.lower()] = key
    return out


def _own(node: exp.Expression, select: exp.Select) -> bool:
    """Düğüm bu SELECT'e mi ait (araya başka bir SELECT girmeden)?"""
    p = node.parent
    while p is not None and p is not select:
        if isinstance(p, exp.Select):
            return False
        p = p.parent
    return p is select


def _per_day(g: exp.Expression) -> bool:
    """Gruplama gün düzeyinde mi: çıplak kolon ya da CAST(... AS DATE). MONTH(), EOMONTH() … dönem düzeyidir."""
    if isinstance(g, exp.Column):
        return True
    return isinstance(g, exp.Cast) and isinstance(g.this, exp.Column) and g.to.this == exp.DataType.Type.DATE


def _is_date_col(col: exp.Column, alias: str, date_col: str, single: bool) -> bool:
    if col.name.lower() != date_col.lower():
        return False
    return col.table.lower() == alias if col.table else single


class SnapshotGuard:
    def __init__(self, dictionary):
        self.dictionary = dictionary

    def _snap(self, full: str) -> str:
        t = self.dictionary.tables.get(full)
        return getattr(t, "snapshot_date", "") if t is not None else ""

    def check(self, tree: exp.Expression) -> SnapshotResult:
        res = SnapshotResult()
        for select in tree.find_all(exp.Select):
            self._check_select(select, res)
        res.errors = list(dict.fromkeys(res.errors))
        res.warnings = list(dict.fromkeys(res.warnings))
        return res

    def _check_select(self, select: exp.Select, res: SnapshotResult) -> None:
        scope = _scope_tables(select, self.dictionary)
        snaps = {a: (t, self._snap(t)) for a, t in scope.items() if self._snap(t)}
        if not snaps:
            return
        single = len(scope) == 1
        conds = [select.args["where"].this] if select.args.get("where") else []
        conds += [j.args["on"] for j in select.args.get("joins") or [] if j.args.get("on") is not None]

        pinned: dict[str, str] = {}   # alias → "day" | "range"
        linked: set[frozenset[str]] = set()
        for cond in conds:
            for node in cond.find_all(exp.EQ, exp.In, exp.Between, exp.GTE, exp.GT, exp.LTE, exp.LT):
                if not _own(node, select):
                    continue
                cols = [c for c in (node.this, node.args.get("expression")) if isinstance(c, exp.Column)] \
                    if not isinstance(node, (exp.In, exp.Between)) else ([node.this] if isinstance(node.this, exp.Column) else [])
                hits = [a for a, (_t, dc) in snaps.items() for c in cols if _is_date_col(c, a, dc, single)]
                if isinstance(node, exp.EQ) and len(hits) == 2 and hits[0] != hits[1]:
                    linked.add(frozenset(hits))
                    continue
                for a in hits:
                    kind = "day" if isinstance(node, (exp.EQ, exp.In)) else "range"
                    if pinned.get(a) != "day":
                        pinned[a] = kind

        # a.DataDate = b.DataDate ve a tek güne sabitse b de o güne sabittir (eşleşme zinciri boyunca yayılır)
        changed = True
        while changed:
            changed = False
            for pair in linked:
                a, b = tuple(pair)
                for x, y in ((a, b), (b, a)):
                    if pinned.get(x) == "day" and pinned.get(y) != "day":
                        pinned[y] = "day"
                        changed = True

        group = select.args.get("group")
        grouped_raw: set[str] = set()
        grouped_period: set[str] = set()
        for g in (group.expressions if group else []):
            for a, (_t, dc) in snaps.items():
                for c in g.find_all(exp.Column):
                    if _is_date_col(c, a, dc, single):
                        (grouped_raw if _per_day(g) else grouped_period).add(a)

        # bu kapsamın toplama fonksiyonları (alt sorgular hariç)
        aggs = [f for e in select.expressions for f in e.find_all(exp.AggFunc) if _own(f, select)]
        if select.args.get("having"):
            aggs += [f for f in select.args["having"].find_all(exp.AggFunc) if _own(f, select)]

        def agg_alias(f: exp.AggFunc) -> set[str]:
            cols = list(f.find_all(exp.Column))
            if not cols:                                      # COUNT(*): kapsamdaki tüm anlık görüntüler
                return set(snaps)
            out = set()
            for c in cols:
                a = c.table.lower() if c.table else (next(iter(scope)) if single else "")
                if a in snaps and c.name.lower() != snaps[a][1].lower():
                    out.add(a)
            return out

        additive: dict[str, list[str]] = {}
        other: dict[str, list[str]] = {}
        for f in aggs:
            name = f.key.lower()
            distinct = isinstance(f.this, exp.Distinct)
            target = additive if name in ("sum", "count") and not distinct else other
            for a in agg_alias(f):
                target.setdefault(a, []).append(f.sql()[:60])

        def date_only(a: str, dc: str) -> bool:
            """Bu kapsam anlık görüntüden yalnız tarih kolonunu okuyor mu (SELECT MAX(DataDate), DISTINCT DataDate …)?"""
            if any(isinstance(e, exp.Star) or e.find(exp.Star) for e in select.expressions):
                return False
            refs = [c for e in select.expressions for c in e.find_all(exp.Column) if _own(c, select)
                    and (c.table.lower() == a if c.table else single)]
            if any(isinstance(f, exp.Count) and not list(f.find_all(exp.Column)) for f in aggs):   # COUNT(*)
                return False
            return bool(refs) and all(c.name.lower() == dc.lower() for c in refs)

        for a, (t, dc) in snaps.items():
            disp = self.dictionary.tables[t].display_name or t
            fix = f"WHERE {a}.{dc} = (SELECT MAX({dc}) FROM {disp})"
            day_ok = pinned.get(a) == "day" or a in grouped_raw
            if a in additive:
                if pinned.get(a) == "day" or a in grouped_raw:
                    pass
                elif a in grouped_period:
                    res.errors.append(
                        f"'{disp}' günlük anlık görüntüdür ({dc}): her kayıt her gün için tekrarlanır. Dönem (ay / yıl) bazında "
                        f"gruplarken {', '.join(additive[a][:2])} ayın TÜM günlerini toplar. Her dönemden tek gün seçin, ör. "
                        f"WHERE {a}.{dc} = EOMONTH({a}.{dc}) (ay sonu) ya da her ayın son {dc} değeri; ortalama için AVG kullanın.")
                elif pinned.get(a) == "range":
                    res.errors.append(
                        f"'{disp}' günlük anlık görüntüdür ({dc}): tarih aralığı birden çok gün içerir, {', '.join(additive[a][:2])} "
                        f"günleri tekrar tekrar toplar. Tek gün seçin ({a}.{dc} = ...) ya da {dc} kolonuna göre gruplayın.")
                else:
                    res.errors.append(
                        f"'{disp}' günlük anlık görüntüdür ({dc}): her kayıt her gün için tekrarlanır; gün seçilmeden "
                        f"{', '.join(additive[a][:2])} gün sayısıyla çarpılmış sonuç verir. Tek gün seçin: {fix} (son gün) "
                        f"ya da günlük seri için GROUP BY {a}.{dc}.")
            elif day_ok or date_only(a, dc):
                pass
            elif a in other and a in grouped_period:   # dönem ortalaması (ör. aylık AVG bakiye): anlamlı, uyarı
                res.warnings.append(
                    f"'{disp}' günlük anlık görüntüdür ({dc}): {', '.join(other[a][:2])} dönemin tüm günleri üzerinden hesaplanır "
                    f"(dönem ortalaması). Dönem sonu değeri isteniyorsa her dönemden tek gün seçin.")
            elif a in other:
                res.errors.append(
                    f"'{disp}' günlük anlık görüntüdür ({dc}): takvimle çoğaltıldığı için gün seçilmeden "
                    f"{', '.join(other[a][:2])} tüm günler üzerinden hesaplanır ve view'ın tamamı taranır. Tek gün seçin: "
                    f"{fix} (son gün) ya da günlük seri için GROUP BY {a}.{dc}.")
            else:
                res.errors.append(
                    f"'{disp}' günlük anlık görüntüdür ({dc}): gün seçilmeden her kayıt her gün için ayrı satır döner ve "
                    f"takvimle çoğaltılmış view'ın tamamı taranır (TOP / DISTINCT / GROUP BY da bunu önlemez). Keşif ya da "
                    f"örnek veri için bile tek gün seçin: {fix}. Hangi günlerin olduğunu görmek için yalnız "
                    f"SELECT MIN({dc}), MAX({dc}) FROM {disp} sorgulanabilir.")

        # iki anlık görüntünün birleştirilmesi: tarih eşlenmeli ya da ikisi de sabitlenmeli
        aliases = sorted(snaps)
        for i, a in enumerate(aliases):
            for b in aliases[i + 1:]:
                if frozenset((a, b)) in linked or (pinned.get(a) == "day" and pinned.get(b) == "day"):
                    continue
                da, db_ = snaps[a][1], snaps[b][1]
                res.errors.append(
                    f"İki günlük anlık görüntü ({self.dictionary.tables[snaps[a][0]].display_name or snaps[a][0]}, "
                    f"{self.dictionary.tables[snaps[b][0]].display_name or snaps[b][0]}) tarih eşlenmeden birleştiriliyor: "
                    f"satırlar gün × gün çoğalır. JOIN koşuluna {a}.{da} = {b}.{db_} ekleyin.")
