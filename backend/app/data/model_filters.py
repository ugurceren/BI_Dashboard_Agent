"""Model tabanlı filtreler (Power BI tarzı tek yönlü çapraz filtre).

Bir filtre bir model kolonuna bağlıdır (ör. dbo.DimSalesTerritory.SalesTerritoryGroup). Seçim yapılınca
her dataset SQL'inin her SELECT bloğuna ilişkiler üzerinden yayılan bir koşul eklenir:

  * Filtre tablosu blokta varsa  → o takma ada doğrudan  `t.kolon IN (...)`
  * Yoksa blokta, filtre tablosundan "tek → çok" yönünde (N:1 ilişkinin 1 tarafından N tarafına; 1:1 iki yönde)
    ulaşılabilen tablolar aranır; fact/köprü tabloları tercih edilir, yol üstündeki ara tablolar atlanır.
    Koşul iç içe EXISTS olarak yazılır:  EXISTS (SELECT 1 FROM Dim d WHERE d.Key = f.Key AND d.kolon IN (...))
  * Ulaşılamayan dataset'ler filtrelenmez (Power BI'da ilişkisi olmayan görsel gibi) ve "uygulanmadı" döner.

Yalnızca aktif ilişkiler kullanılır; aynı iki tablo arasında birden çok ilişki varsa (rol yapan tarih boyutu)
dataset'in kendi JOIN'i o rolü belirler, çünkü o durumda filtre tablosu zaten bloktadır.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp

from app.dictionary.repository import DataDictionary, DDRelationship


@dataclass
class ModelFilter:
    table: str                 # küçük harf şema.tablo
    column: str                # küçük harf kolon
    values: list[Any]

    @property
    def key(self) -> str:
        return f"{self.table}.{self.column}"


def _sources(sel: exp.Select) -> list[exp.Expression]:
    frm = sel.args.get("from_") or sel.args.get("from")
    out = [frm.this] if frm else []
    out += [j.this for j in sel.args.get("joins") or []]
    return out


def _base_tables(sel: exp.Select, dd: DataDictionary, ctes: set[str]) -> dict[str, str]:
    """alias → sözlükteki tablo (yalnız gerçek tablolar)."""
    out: dict[str, str] = {}
    for src in _sources(sel):
        if isinstance(src, exp.Table) and isinstance(src.this, exp.Identifier) and src.db:
            full = f"{src.db}.{src.name}".lower()
            if dd.has_table(full) and not (not src.db and src.name.lower() in ctes):
                out[src.alias_or_name.lower()] = full
    return out


class ModelFilterEngine:
    def __init__(self, dictionary: DataDictionary, dialect: str):
        self.dd = dictionary
        self.dialect = dialect

    # ------------------------------------------------------------------ yollar
    def _adjacent(self, table: str) -> list[tuple[str, DDRelationship]]:
        """table'dan filtre akış yönündeki komşular: (komşu, ilişki)."""
        out = []
        for r in self.dd.relationships:
            if not r.active or r.cardinality == "N:N" or r.from_table == r.to_table:
                continue
            if r.to_table == table:                      # 1 → N
                out.append((r.from_table, r))
            elif r.cardinality == "1:1" and r.from_table == table:
                out.append((r.to_table, r))
        return out

    def paths_from(self, start: str) -> dict[str, list[tuple[str, DDRelationship]]]:
        """start'tan ulaşılan her tabloya en kısa yol: [(sonraki_tablo, ilişki), ...]."""
        paths: dict[str, list[tuple[str, DDRelationship]]] = {start: []}
        q = deque([start])
        while q:
            u = q.popleft()
            for v, r in self._adjacent(u):
                if v not in paths:
                    paths[v] = paths[u] + [(v, r)]
                    q.append(v)
        return paths

    # ------------------------------------------------------------------ SQL üretimi
    def _t(self, table: str) -> exp.Table:
        t = self.dd.tables.get(table)
        name = (t.display_name if t and t.display_name else table)
        return exp.to_table(name, dialect=self.dialect)

    def _c(self, table: str, col: str) -> str:
        t = self.dd.tables.get(table)
        if t:
            for c in t.columns:
                if c.name == col and c.display_name:
                    return c.display_name
        return col

    def _in(self, alias: str, f: ModelFilter) -> exp.Expression:
        col = exp.column(self._c(f.table, f.column), table=alias)
        return exp.In(this=col, expressions=[self._lit(v) for v in f.values])

    def _lit(self, v: Any) -> exp.Expression:
        # T-SQL: nvarchar kolonlarda Türkçe karakter kaybolmasın diye N'...'
        if isinstance(v, str) and self.dialect == "tsql":
            return exp.National(this=v)
        return exp.convert(v)

    def _condition(self, target_alias: str, target_table: str, path: list[tuple[str, DDRelationship]],
                   f: ModelFilter, depth: int = 0) -> exp.Expression:
        """path: filtre tablosundan target'a. Target üzerinde iç içe EXISTS koşulu üretir."""
        if not path:
            return self._in(target_alias, f)
        *rest, (_, rel) = path
        prev_table = rest[-1][0] if rest else f.table
        a = f"_mf{depth}"
        # rel bir ucu prev_table (tek taraf), diğer ucu target_table
        if rel.from_table == target_table:
            pairs = [(tc, pc) for tc, pc in rel.pairs]           # (target kolonu, prev kolonu)
        else:
            pairs = [(pc, tc) for tc, pc in rel.pairs]
        join = [exp.EQ(this=exp.column(self._c(prev_table, pc), table=a), expression=exp.column(self._c(target_table, tc), table=target_alias))
                for tc, pc in pairs]
        inner = self._condition(a, prev_table, rest, f, depth + 1)
        where = exp.and_(*join, inner)
        sub = exp.select("1").from_(self._t(prev_table).as_(a)).where(where)
        return exp.Exists(this=sub)

    def apply(self, sql: str, filters: list[ModelFilter]) -> tuple[str, list[str]]:
        """Filtreleri SQL'e uygular. (yeni_sql, uygulanan filtre anahtarları)."""
        filters = [f for f in filters if f.values]
        if not filters:
            return sql, []
        tree = sqlglot.parse_one(sql, read=self.dialect)
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        kinds = self.dd._table_kinds()
        applied: set[str] = set()
        blocks = list(tree.find_all(exp.Select))
        paths_cache: dict[str, dict] = {}
        for sel in blocks:
            tables = _base_tables(sel, self.dd, ctes)
            if not tables:
                continue
            conds: list[exp.Expression] = []
            for f in filters:
                direct = [a for a, t in tables.items() if t == f.table]
                if direct:
                    conds += [self._in(a, f) for a in direct]
                    applied.add(f.key)
                    continue
                paths = paths_cache.setdefault(f.table, self.paths_from(f.table))
                cands = [(a, t, paths[t]) for a, t in tables.items() if t in paths]
                if not cands:
                    continue
                facts = [c for c in cands if kinds.get(c[1]) in ("fact", "bridge", "view")]
                if facts:
                    cands = facts
                on_paths = {t for _, _, p in cands for t, _ in p[:-1]}
                cands = [c for c in cands if c[1] not in on_paths] or cands
                for a, t, p in cands:
                    conds.append(self._condition(a, t, p, f))
                applied.add(f.key)
            for c in conds:
                sel.where(c, append=True, copy=False)
        return tree.sql(dialect=self.dialect), sorted(applied)

    # ------------------------------------------------------------------ veri tarihi (günlük anlık görüntü)
    def snapshot_tables(self, sql: str) -> list[str]:
        """SQL'in okuduğu günlük anlık görüntü tabloları (DataDate …), sık kullanılan önce."""
        try:
            tree = sqlglot.parse_one(sql, read=self.dialect)
        except sqlglot.errors.ParseError:
            return []
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        out: list[str] = []
        for sel in tree.find_all(exp.Select):
            for t in _base_tables(sel, self.dd, ctes).values():
                if getattr(self.dd.tables[t], "snapshot_date", ""):
                    out.append(t)
        return sorted(set(out), key=lambda t: -out.count(t))

    def as_of(self, sql: str, day: str) -> tuple[str, bool]:
        """'İtibarıyla' tarihi: anlık görüntü okuyan HER SELECT bloğuna  alias.DataDate <= 'gün'  eklenir.
        Böylece  (SELECT MAX(DataDate) FROM …)  seçilen günü (o gün yoksa öncesindeki son günü) verir, trendler o
        günde biter, 'geçen yıl aynı gün' karşılaştırmaları da seçilen güne göre kayar. (yeni_sql, uygulandı mı)"""
        tree = sqlglot.parse_one(sql, read=self.dialect)
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        lit = exp.Cast(this=exp.Literal.string(day), to=exp.DataType.build("DATE"))
        done = False
        for sel in list(tree.find_all(exp.Select)):
            for alias, t in _base_tables(sel, self.dd, ctes).items():
                dc = getattr(self.dd.tables[t], "snapshot_date", "")
                if dc:
                    sel.where(exp.LTE(this=exp.column(dc, table=alias), expression=lit.copy()), append=True, copy=False)
                    done = True
        return (tree.sql(dialect=self.dialect), True) if done else (sql, False)

    # ------------------------------------------------------------------ alan kökeni (lineage)
    def lineage(self, sql: str) -> dict[str, str]:
        """Dataset çıktı kolonu → 'şema.tablo.kolon' (yalnız doğrudan kolon referansları)."""
        try:
            tree = sqlglot.parse_one(sql, read=self.dialect)
        except sqlglot.errors.ParseError:
            return {}
        cte_map = {c.alias_or_name.lower(): c.this for c in tree.find_all(exp.CTE)}
        top = tree
        while isinstance(top, exp.SetOperation if hasattr(exp, "SetOperation") else exp.Union):
            top = top.left
        if not isinstance(top, exp.Select):
            return {}
        out = {}
        for proj in top.expressions:
            name = proj.alias_or_name
            src = self._resolve(proj.this if isinstance(proj, exp.Alias) else proj, top, cte_map, 0)
            if src:
                # view'dan okunan dataset: tıklama filtresi view'ın kaynağındaki model kolonuna bağlansın
                out[name] = getattr(self.dd, "view_lineage", {}).get(src, src)
        return out

    def _resolve(self, node: exp.Expression, sel: exp.Select, cte_map: dict, depth: int) -> str | None:
        if depth > 6 or not isinstance(node, exp.Column):
            return None
        col = node.name.lower()
        qual = node.table.lower() if node.table else None
        for src in _sources(sel):
            alias = src.alias_or_name.lower()
            if qual and alias != qual:
                continue
            if isinstance(src, exp.Table) and isinstance(src.this, exp.Identifier):
                if not src.db and src.name.lower() in cte_map:
                    inner = cte_map[src.name.lower()]
                elif src.db:
                    full = f"{src.db}.{src.name}".lower()
                    t = self.dd.tables.get(full)
                    if t and any(c.name == col for c in t.columns):
                        return f"{full}.{col}"
                    continue
                else:
                    continue
            elif isinstance(src, exp.Subquery):
                inner = src.this
            else:
                continue
            while not isinstance(inner, exp.Select) and hasattr(inner, "left"):
                inner = inner.left
            if isinstance(inner, exp.Select):
                for proj in inner.expressions:
                    if proj.alias_or_name.lower() == col:
                        r = self._resolve(proj.this if isinstance(proj, exp.Alias) else proj, inner, cte_map, depth + 1)
                        if r:
                            return r
        return None
