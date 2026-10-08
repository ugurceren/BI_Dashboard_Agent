"""Model ilişkileri: sözlükte olmayan, agent'ın önerip kullanıcının onayladığı tablo ilişkileri (ortak model).

Kurum sözlüğü (BI_Meta) bizim değil; onaylanan ilişkiler yerel kayıtta tutulur (config/model_relationships.json) ve her
açılışta sözlüğe eklenir. Filtreler (ModelFilterEngine) ilişkiler üzerinden yayıldığı için, örneğin EDWDM view'ları
arasında bir kez kurulan ilişki sonraki tüm raporların filtrelerini çalışır / hızlı kılar.

Aday çıkarımı (propose):
  1. veritabanı yabancı anahtarları (sys.foreign_keys),
  2. iki tabloda aynı adlı anahtar kolonu (…Key, …Id, CustomerPartyId …) — hangi tarafın tekil olduğu PK / unique index'ten,
     yoksa (makul boyuttaysa) COUNT(*) ile COUNT(DISTINCT) karşılaştırmasıyla ölçülür,
  3. iki günlük anlık görüntü (DataDate) arasındaki ilişkiye tarih kolonu çifti eklenir (gün × gün çoğalmasın).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlglot import exp

log = logging.getLogger(__name__)

_lock = threading.Lock()
MEASURE_MAX_ROWS = 5_000_000      # tekillik ölçümü bundan büyük (anlık görüntü değilse) tablolarda yapılmaz
_KEY_NAME = re.compile(r"(key|id|no|number|num|kod|code|nr)$", re.I)
_NOT_KEY = re.compile(r"^(datadate|createdate|updatedate|insertdate|loaddate|etldate|rowid)$", re.I)


class RelationshipRegistry:
    def __init__(self, path: Path):
        self.path = path

    def all(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _write(self, items: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, entry: dict[str, Any]) -> None:
        with _lock:
            items = [e for e in self.all() if e["id"] != entry["id"]] + [entry]
            self._write(items)

    def remove(self, rel_id: str) -> bool:
        with _lock:
            items = self.all()
            keep = [e for e in items if e["id"] != rel_id]
            if len(keep) == len(items):
                return False
            self._write(keep)
            return True


def rel_id(from_table: str, to_table: str, pairs: list[tuple[str, str]]) -> str:
    return "model:" + from_table + "→" + to_table + ":" + ",".join(f"{a}={b}" for a, b in pairs)


def to_relationship(entry: dict[str, Any]):
    from app.dictionary.repository import DDRelationship
    return DDRelationship(entry["id"], entry["from_table"], entry["to_table"], [tuple(p) for p in entry["pairs"]],
                          entry.get("cardinality") or "N:1", entry.get("role") or "", entry.get("description") or "")


def report_relationships(dd, entries: list[dict[str, Any]] | None) -> list:
    """Rapora özel ilişkiler (oturum / yayın sürümü): tablolar ve kolonlar hâlâ varsa."""
    out = []
    for e in entries or []:
        try:
            r = to_relationship(e)
        except (KeyError, TypeError):
            continue
        ft, tt = dd.tables.get(r.from_table), dd.tables.get(r.to_table)
        if ft and tt and all(a in {c.name for c in ft.columns} and b in {c.name for c in tt.columns} for a, b in r.pairs):
            out.append(r)
    return out


def apply_registry(dd) -> int:
    """Kayıttaki ilişkileri sözlüğe ekler (tabloları ve kolonları hâlâ var olanlar; aynısı zaten varsa atlanır)."""
    existing = {(r.from_table, r.to_table, tuple(r.pairs)) for r in dd.relationships}
    n = 0
    for e in RelationshipRegistry(dd.settings.model_relationships).all():
        try:
            r = to_relationship(e)
        except (KeyError, TypeError):
            continue
        ft, tt = dd.tables.get(r.from_table), dd.tables.get(r.to_table)
        if not ft or not tt or (r.from_table, r.to_table, tuple(r.pairs)) in existing:
            continue
        fc, tc = {c.name for c in ft.columns}, {c.name for c in tt.columns}
        if all(a in fc and b in tc for a, b in r.pairs):
            dd.relationships.append(r)
            n += 1
    if n:
        log.info("Model ilişkileri (onaylı): %d ilişki eklendi", n)
    return n


# ------------------------------------------------------------------ aday çıkarımı
def _unique_sets(info: dict[str, Any]) -> list[set[str]]:
    return [{c.lower() for c in ix.get("columns", [])} for ix in info.get("indexes", []) if ix.get("primary_key") or ix.get("unique")]


def _is_key_col(col) -> bool:
    return (col.role == "key" or bool(_KEY_NAME.search(col.name))) and not _NOT_KEY.match(col.name)


def _measure_unique(dd, connector, key: str, cols: list[str]) -> tuple[bool | None, str]:
    """COUNT(*) = COUNT(DISTINCT kolonlar) mı? Anlık görüntüde son gün üzerinden. (sonuç, kanıt metni)"""
    t = dd.tables[key]
    if not t.snapshot_date and t.row_count is not None and t.row_count > MEASURE_MAX_ROWS:
        return None, f"tekillik ölçülmedi ({t.row_count:,} satır)"
    from app.data.model_filters import with_nolock
    name = exp.to_table(t.display_name or key, dialect="tsql")
    cdisp = {c.name: (c.display_name or c.name) for c in t.columns}
    distinct = exp.Count(this=exp.Distinct(expressions=[exp.column(cdisp.get(c, c)) for c in cols])) if len(cols) == 1 else \
        exp.Count(this=exp.Distinct(expressions=[exp.func("CONCAT", *[exp.column(cdisp.get(c, c)) for c in cols])]))
    q = exp.select(exp.Count(this=exp.Star()), distinct).from_(with_nolock(name, dd))
    if t.snapshot_date:
        d = exp.column(t.snapshot_date)
        from app.data.snapshot_guard import recent_bound
        last = exp.select(exp.Max(this=d.copy())).from_(with_nolock(name.copy(), dd)).where(recent_bound(d.copy()))
        q = q.where(exp.EQ(this=d.copy(), expression=exp.Subquery(this=last)))
    db = dd.db_of(key)
    con = connector if not db or not dd.database or db.lower() == dd.database.lower() else dd._connector_for(db)
    try:
        total, dist = con.execute(q.sql(dialect="tsql"), 1).rows[0]
    except Exception as e:  # noqa: BLE001
        return None, f"tekillik ölçülemedi ({str(e)[:80]})"
    day = " (son gün)" if t.snapshot_date else ""
    return (int(total or 0) == int(dist or 0)), f"{int(total or 0):,} satır / {int(dist or 0):,} farklı{day}"


def propose(dd, connector, keys: list[str], report_entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Seçilen tablolar arasında ilişki adayları + tabloların özeti (kullanıcıya sorulmak üzere)."""
    from app.dictionary.discovery import discover

    kinds = dd._table_kinds()
    infos: dict[str, dict[str, Any]] = {}
    for k in keys:
        try:
            infos[k] = discover(dd, connector, k, include_pii=True) if connector.dialect == "tsql" else {}
        except Exception as e:  # noqa: BLE001 — katalog okunamazsa ad eşlemesiyle devam
            log.warning("Katalog okunamadı (%s): %s", k, e)
            infos[k] = {}
    # aynı tablo çifti + aynı kolon çiftleri (yönden bağımsız) modelde varsa yeniden önerilmez
    existing = {frozenset((r.from_table, r.to_table)) | {frozenset(r.pairs)}: r
                for r in [*dd.relationships, *report_relationships(dd, report_entries)]}
    tables = []
    for k in keys:
        t = dd.tables[k]
        tables.append({"table": t.display_name or k, "business_name": t.business_name, "description": t.description,
                       "kind": "view" if dd.is_view(k, kinds) else kinds.get(k, "?"), "role_hint": kinds.get(k),
                       "row_count": t.row_count, **({"snapshot_date": t.snapshot_date} if t.snapshot_date else {}),
                       "primary_key": next((ix.get("columns") for ix in infos[k].get("indexes", []) if ix.get("primary_key")), None)})

    cands: list[dict[str, Any]] = []
    seen: set[tuple[str, str, tuple]] = set()

    def add(frm: str, to: str, pairs: list[tuple[str, str]], card: str, evidence: str) -> None:
        a, b = dd.tables[frm], dd.tables[to]
        if a.snapshot_date and b.snapshot_date and not any(x == a.snapshot_date.lower() for x, _ in pairs):
            pairs = pairs + [(a.snapshot_date.lower(), b.snapshot_date.lower())]
            evidence += f"; iki anlık görüntü: {a.snapshot_date} çifti eklendi"
        sig = (frm, to, tuple(pairs))
        if sig in seen:
            return
        seen.add(sig)
        ex = existing.get(frozenset((frm, to)) | {frozenset(pairs)}) or             existing.get(frozenset((frm, to)) | {frozenset((b_, a_) for a_, b_ in pairs)})
        cands.append({"id": f"c{len(cands) + 1}", "from_table": a.display_name or frm, "to_table": b.display_name or to,
                      "columns": [[p, q] for p, q in pairs], "cardinality": card, "evidence": evidence,
                      **({"already_in_model": ex.id} if ex else {}),
                      **({"note": "çoktan çoka: filtre yaymaz; köprü tablo ya da DISTINCT gerekir"} if card == "N:N" else {})})

    # 1. veritabanı yabancı anahtarları
    by_disp = {(dd.tables[k].display_name or k).lower(): k for k in keys}
    for k in keys:
        for fk in infos[k].get("foreign_keys", []):
            to = by_disp.get(str(fk["to"]).lower())
            if to and to != k:
                add(k, to, [(c.lower(), r.lower()) for c, r in zip(fk["columns"], fk["ref_columns"])], "N:1", "veritabanı yabancı anahtarı")

    # 2. aynı adlı anahtar kolonları
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            ta, tb = dd.tables[a], dd.tables[b]
            common = sorted({c.name for c in ta.columns if _is_key_col(c)} & {c.name for c in tb.columns if _is_key_col(c)})
            for col in common:
                ua, ub = _side_unique(dd, connector, a, col, infos[a]), _side_unique(dd, connector, b, col, infos[b])
                ev = f"aynı ad '{col}'; {ta.display_name or a}: {ua[1]}; {tb.display_name or b}: {ub[1]}"
                if ub[0] and ua[0]:
                    add(a, b, [(col, col)], "1:1", ev)
                elif ub[0]:
                    add(a, b, [(col, col)], "N:1", ev)
                elif ua[0]:
                    add(b, a, [(col, col)], "N:1", ev)
                elif ua[0] is False and ub[0] is False:
                    add(a, b, [(col, col)], "N:N", ev)
                else:
                    add(a, b, [(col, col)], "N:1?", ev + " — yön doğrulanamadı, kullanıcıya sor")
    return {"tables": tables, "candidates": cands}


def _side_unique(dd, connector, key: str, col: str, info: dict[str, Any]) -> tuple[bool | None, str]:
    t = dd.tables[key]
    need = {col} | ({t.snapshot_date.lower()} if t.snapshot_date else set())
    if any(s <= need and col in s for s in _unique_sets(info)):
        return True, "PK / unique index"
    if connector.dialect != "tsql":
        return None, "doğrulanmadı"
    return _measure_unique(dd, connector, key, [col])


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ dataset SQL'indeki JOIN'lerden (rapora özel)
def derive_from_sql(dd, connector, join_guard, datasets: list, report_entries: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Kaydedilmiş dataset'lerin SQL'indeki eşitlik JOIN'lerinden rapora özel ilişkiler.

    Sözlükte / modelde ilişki olmasa da (ör. EDWDM view'ları) dashboard'un birleştirdiği tablolar Model sekmesinde
    görünür ve filtreler bu ilişkilerden yayılır. Yalnız bir tarafı tekil olan (N:1 / 1:1) birleşimler eklenir;
    tekillik PK / unique index'ten, yoksa (anlık görüntüde son gün) COUNT ile COUNT(DISTINCT) karşılaştırılarak bulunur.
    SQL zaten doğrulanıp çalıştırıldığı için kullanıcı onayı istenmez."""
    import sqlglot

    from app.dictionary.discovery import discover

    def same(r_from: str, r_to: str, pairs) -> frozenset:
        return frozenset((r_from, r_to)) | {frozenset(pairs)}

    known = {same(r.from_table, r.to_table, r.pairs) for r in [*dd.relationships, *report_relationships(dd, report_entries)]}
    known |= {same(r.to_table, r.from_table, [(b, a) for a, b in r.pairs])
              for r in [*dd.relationships, *report_relationships(dd, report_entries)]}
    infos: dict[str, dict[str, Any]] = {}
    uniq: dict[tuple[str, tuple[str, ...]], bool | None] = {}

    def unique(key: str, cols: list[str]) -> bool | None:
        k = (key, tuple(sorted(cols)))
        if k in uniq:
            return uniq[k]
        t = dd.tables[key]
        if key not in infos:
            try:
                infos[key] = discover(dd, connector, key, include_pii=True) if connector.dialect == "tsql" else {}
            except Exception:  # noqa: BLE001
                infos[key] = {}
        need = set(cols) | ({t.snapshot_date.lower()} if t.snapshot_date else set())
        res: bool | None = True if any(s and s <= need for s in _unique_sets(infos[key])) else None
        if res is None and connector.dialect == "tsql":
            res = _measure_unique(dd, connector, key, [c for c in cols if not (t.snapshot_date and c == t.snapshot_date.lower())])[0]
        uniq[k] = res
        return res

    out: list[dict[str, Any]] = []
    for d in datasets:
        for sql in {getattr(d, "sql", None), getattr(d, "original_sql", None)} - {None, ""}:
            try:
                tree = sqlglot.parse_one(sql, read="tsql")
            except Exception:  # noqa: BLE001
                continue
            for ta, tb, pairs in join_guard.join_pairs(tree):
                if ta not in dd.tables or tb not in dd.tables:
                    continue
                a, b = dd.tables[ta], dd.tables[tb]
                if a.snapshot_date and b.snapshot_date and (a.snapshot_date.lower(), b.snapshot_date.lower()) not in pairs:
                    pairs = [*pairs, (a.snapshot_date.lower(), b.snapshot_date.lower())]
                if same(ta, tb, pairs) in known:
                    continue
                key_a = [x for x, _ in pairs if not (a.snapshot_date and x == a.snapshot_date.lower())]
                key_b = [y for _, y in pairs if not (b.snapshot_date and y == b.snapshot_date.lower())]
                ua, ub = unique(ta, key_a), unique(tb, key_b)
                if ub:
                    frm, to, prs, card = ta, tb, pairs, "1:1" if ua else "N:1"
                elif ua:
                    frm, to, prs, card = tb, ta, [(y, x) for x, y in pairs], "N:1"
                else:
                    continue   # iki taraf da tekil değil / bilinmiyor: filtre yaymaz, modele eklenmez
                entry = {"id": rel_id(frm, to, prs).replace("model:", "join:", 1), "from_table": frm, "to_table": to,
                         "pairs": [list(p) for p in prs], "cardinality": card,
                         "description": f"SQL JOIN'den ('{getattr(d, 'id', '')}' dataset'i)", "source": "sql", "added_at": now_iso()}
                out.append(entry)
                known.add(same(frm, to, prs))
                known.add(same(to, frm, [(y, x) for x, y in prs]))
    return out
