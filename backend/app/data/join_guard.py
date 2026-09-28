"""JOIN doğrulayıcı: birleştirmeleri sözlükteki ilişkilerle eşler, satır çoğalmasını (fan-out) yakalar.

Her SELECT bloğu ayrı incelenir:
  1. FROM/JOIN'deki gerçek tablolar ve JOIN koşulları (ON, USING, WHERE'deki a.x = b.y) çıkarılır.
  2. Her koşul sözlükteki bir ilişkiyle eşlenir → her tarafın tekil (1) mi çok (N) mu olduğu bilinir.
     Bileşik anahtarın yalnızca bir kısmıyla yapılan birleştirme hatadır.
  3. Blokta toplanan (SUM/AVG/COUNT(kolon)) her ölçünün tablosundan JOIN grafiği gezilir;
     'çok' tarafa geçilen her kenar o ölçünün satırlarını çoğaltır → hata.
     (İki fact tablosunun ortak boyut üzerinden birleştirilmesi — chasm trap — da böyle yakalanır.)
Alt sorgu / CTE kaynakları "önceden toplanmış" kabul edilir ve incelenmez: önerilen çözüm de budur.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlglot import exp

from app.dictionary.repository import DataDictionary, DDRelationship


@dataclass
class _Edge:
    a: str                       # alias
    b: str
    pairs: set[tuple[str, str]]  # (a kolonu, b kolonu)
    a_unique: bool | None = None
    b_unique: bool | None = None
    rel: DDRelationship | None = None


@dataclass
class JoinReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_SAFE_AGGS = (exp.Min, exp.Max)


def _short(t: str) -> str:
    return t.split(".")[-1]


class JoinGuard:
    def __init__(self, dictionary: DataDictionary):
        self.dd = dictionary

    def check(self, tree: exp.Expression) -> JoinReport:
        rep = JoinReport()
        if not self.dd.relationships:
            return rep
        for sel in tree.find_all(exp.Select):
            self._check_block(sel, rep)
        rep.errors = list(dict.fromkeys(rep.errors))
        rep.warnings = list(dict.fromkeys(rep.warnings))
        return rep

    # ------------------------------------------------------------------ blok
    def _check_block(self, sel: exp.Select, rep: JoinReport) -> None:
        frm = sel.args.get("from_") or sel.args.get("from")
        joins = sel.args.get("joins") or []
        if not frm or not joins:  # tek kaynak: birleştirme yok ("FROM a, b" de joins içinde gelir)
            return

        aliases: dict[str, str | None] = {}   # alias → tablo (None = alt sorgu/CTE)
        order: list[str] = []

        def add_source(node: exp.Expression) -> str | None:
            if isinstance(node, exp.Table) and isinstance(node.this, exp.Identifier):
                alias = node.alias_or_name.lower()
                full = f"{node.db}.{node.name}".lower() if node.db else None
                aliases[alias] = full if full and self.dd.has_table(full) else None
            elif isinstance(node, (exp.Subquery, exp.Table)):
                alias = (node.alias_or_name or f"_q{len(order)}").lower()
                aliases[alias] = None
            else:
                return None
            order.append(alias)
            return alias

        add_source(frm.this)
        conds: list[exp.Expression] = []
        using: list[tuple[str, list[str]]] = []
        for j in joins:
            alias = add_source(j.this)
            if j.args.get("on") is not None:
                conds.append(j.args["on"])
            if j.args.get("using") and alias:
                using.append((alias, [u.name.lower() for u in j.args["using"]]))
        if sel.args.get("where") is not None:
            conds.append(sel.args["where"].this)

        # --- kenarlar
        edges: dict[frozenset[str], _Edge] = {}

        def add_pair(a: str, ca: str, b: str, cb: str) -> None:
            if a == b:
                return
            key = frozenset((a, b))
            e = edges.get(key)
            if e is None:
                e = edges[key] = _Edge(a, b, set())
            e.pairs.add((ca, cb) if e.a == a else (cb, ca))

        for cond in conds:
            for eq in _conjuncts(cond):
                if isinstance(eq, exp.EQ) and isinstance(eq.left, exp.Column) and isinstance(eq.right, exp.Column):
                    la, ra = self._alias_of(eq.left, aliases), self._alias_of(eq.right, aliases)
                    if la and ra:
                        add_pair(la, eq.left.name.lower(), ra, eq.right.name.lower())
        for alias, cols in using:
            for c in cols:
                left = next((a for a in order[:order.index(alias)] if self._has_col(aliases.get(a), c)), None)
                if left:
                    add_pair(left, c, alias, c)

        for e in edges.values():
            ta, tb = aliases.get(e.a), aliases.get(e.b)
            if not ta or not tb:
                continue  # alt sorgu/CTE: önceden toplanmış kabul
            self._match(e, ta, tb, rep)

        # --- ölçüler ve çoğalma
        measure_aliases: dict[str, str] = {}
        for agg in sel.find_all(exp.AggFunc):
            if agg.find_ancestor(exp.Select) is not sel or isinstance(agg, _SAFE_AGGS):
                continue
            if isinstance(agg, exp.Count) and isinstance(agg.this, (exp.Distinct, exp.Star)):
                continue
            for col in agg.find_all(exp.Column):
                a = self._alias_of(col, aliases)
                if a and aliases.get(a):
                    measure_aliases.setdefault(a, agg.sql())
        for m, agg_sql in measure_aliases.items():
            hit = self._fanout_from(m, edges, aliases)
            if hit:
                u, v, e = hit
                tu, tv = aliases[u], aliases[v]
                why = "çoktan çoka" if e.rel and e.rel.cardinality == "N:N" else "bire çok"
                rep.errors.append(
                    f"Satır çoğalması (fan-out): {agg_sql} ölçüsü {_short(aliases[m])} tablosundan geliyor, ama "
                    f"{_short(tu)} → {_short(tv)} birleştirmesi {why} ({_short(tv)} tarafında aynı anahtardan birden çok satır var). "
                    f"Toplam şişer. Çözüm: {_short(tv)} tarafını önce bir alt sorguda/CTE'de birleştirme anahtarına göre toplayıp "
                    f"sonra birleştirin, ya da iki ölçüyü ayrı sorgularda hesaplayın.")

    # ------------------------------------------------------------------ yardımcılar
    def _has_col(self, table: str | None, col: str) -> bool:
        t = self.dd.tables.get(table or "")
        return bool(t) and any(c.name == col for c in t.columns)

    def _alias_of(self, col: exp.Column, aliases: dict[str, str | None]) -> str | None:
        if col.table:
            a = col.table.lower()
            return a if a in aliases else None
        owners = [a for a, t in aliases.items() if self._has_col(t, col.name.lower())]
        return owners[0] if len(owners) == 1 else None

    def _match(self, e: _Edge, ta: str, tb: str, rep: JoinReport) -> None:
        pairs = e.pairs
        partial: DDRelationship | None = None
        for r in self.dd.relationships:
            if {r.from_table, r.to_table} != {ta, tb} or ta == tb:
                continue
            oriented = set(r.pairs) if r.from_table == ta else {(b, a) for a, b in r.pairs}
            if oriented <= pairs:
                e.rel = r
                e.a_unique = r.from_unique if r.from_table == ta else r.to_unique
                e.b_unique = r.to_unique if r.from_table == ta else r.from_unique
                return
            if pairs & oriented:
                partial = r
        if partial:
            have = {a for a, _ in pairs} | {b for _, b in pairs}
            missing = [f"{a}={b}" for a, b in partial.pairs if a not in have and b not in have]
            rep.errors.append(
                f"Bileşik anahtar eksik: {_short(ta)} ile {_short(tb)} ilişkisi birden çok kolonla kurulur "
                f"({partial.join_sql()}). Eksik koşul(lar): {', '.join(missing) or '?'}. Eksik anahtarla birleştirme satırları çoğaltır.")
            return
        rep.warnings.append(
            f"{_short(ta)} ↔ {_short(tb)} birleştirmesi ({', '.join(f'{a}={b}' for a, b in sorted(pairs))}) sözlükte tanımlı değil; "
            f"satır çoğalması kontrol edilemedi. Mümkünse sözlükteki JOIN ilişkilerini kullanın.")

    def _fanout_from(self, start: str, edges: dict[frozenset[str], _Edge],
                     aliases: dict[str, str | None]) -> tuple[str, str, _Edge] | None:
        seen, stack = {start}, [start]
        while stack:
            u = stack.pop()
            for e in edges.values():
                if u not in (e.a, e.b):
                    continue
                v = e.b if u == e.a else e.a
                if v in seen:
                    continue
                v_unique = e.b_unique if v == e.b else e.a_unique
                if v_unique is False and aliases.get(u) and aliases.get(v):
                    return u, v, e
                seen.add(v)
                stack.append(v)
        return None


def _conjuncts(node: exp.Expression) -> list[exp.Expression]:
    if isinstance(node, exp.And):
        return _conjuncts(node.left) + _conjuncts(node.right)
    if isinstance(node, exp.Paren):
        return _conjuncts(node.this)
    return [node]
