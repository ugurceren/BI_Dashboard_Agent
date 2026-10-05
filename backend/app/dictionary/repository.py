"""Veri sözlüğü: config/dictionary.toml'daki sorgularla yüklenir, bellekte aranır.

Sözlükler genelde birkaç bin satırdır; bu yüzden bellekte tutup Türkçe'ye duyarlı anahtar
kelime + eş anlamlı araması yapıyoruz. Vektör arama gerekirse `search` yerine takılabilir.
"""

from __future__ import annotations

import logging
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable

from app.config import Settings, load_toml
from app.data.connector import Connector, create_connector

log = logging.getLogger(__name__)


@dataclass
class DDColumn:
    table: str
    name: str
    business_name: str
    description: str
    data_type: str
    role: str
    default_aggregation: str | None
    synonyms: list[str]
    is_pii: bool
    sample_values: str
    display_name: str = ""   # sözlükteki orijinal yazım (UI için)


@dataclass
class DDTable:
    name: str
    business_name: str
    description: str
    subject_area: str
    grain: str
    row_count: int | None
    columns: list[DDColumn] = field(default_factory=list)
    display_name: str = ""
    table_type: str = ""     # fact | dimension | bridge (sözlükte yoksa ilişkilerden çıkarılır)
    documented: bool = True        # False: veritabanında var, sözlükte tanımsız (katalogdan eklendi)
    object_type: str = ""          # table | view — veritabanı kataloğundan (sys.objects.type U / V); "" bilinmiyor
    variant_of: str = ""           # yetki varyantı ise ana view (ör. clt.vx ← clt.vxMasked); tanımı ondan devralır
    snapshot_date: str = ""        # günlük anlık görüntü: her gün için satır çoklanır; tarih kolonu (ör. DataDate)
    in_db: bool | None = None      # veritabanı kataloğunda bulundu mu (None: bilinmiyor / katalog okunamadı)
    can_select: bool | None = None # bağlanan hesabın SELECT yetkisi (HAS_PERMS_BY_NAME)


@dataclass
class DDMetric:
    name: str
    business_name: str
    description: str
    expression_sql: str
    base_table: str
    value_format: str
    synonyms: list[str]


CARDINALITIES = ("N:1", "1:1", "1:N", "N:N")


@dataclass
class DDRelationship:
    """Bir ilişki = bir veya daha çok kolon çifti (bileşik anahtar). Yön her zaman 'çok' → 'tek' olacak şekilde normalize edilir."""

    id: str
    from_table: str
    to_table: str
    pairs: list[tuple[str, str]]          # [(from_column, to_column), ...]
    cardinality: str | None = None        # N:1 | 1:1 | N:N | None (bilinmiyor)
    role: str = ""                        # ör. "Sipariş tarihi" (aynı boyuta birden çok ilişki varsa)
    description: str = ""
    active: bool = True                   # Power BI'daki aktif ilişki: filtreler yalnız aktif ilişkilerden yayılır
    _active_explicit: bool = False

    @property
    def from_unique(self) -> bool | None:
        return None if self.cardinality is None else self.cardinality.startswith("1")

    @property
    def to_unique(self) -> bool | None:
        return None if self.cardinality is None else self.cardinality.endswith("1")

    def join_sql(self) -> str:
        return " AND ".join(f"{self.from_table}.{a} = {self.to_table}.{b}" for a, b in self.pairs)

    def describe(self) -> str:
        card = {"N:1": "çoktan bire", "1:1": "bire bir", "N:N": "çoktan çoka — doğrudan toplama yapma"}.get(
            self.cardinality or "", "kardinalite bilinmiyor")
        sides = ""
        if self.cardinality:
            sides = f" ({self.from_table.split('.')[-1]}: {'1' if self.from_unique else 'N'}, " \
                    f"{self.to_table.split('.')[-1]}: {'1' if self.to_unique else 'N'})"
        return (f"{self.join_sql()}  [{self.cardinality or '?'} {card}{sides}]" + (f" rol: {self.role}" if self.role else "")
                + ("" if self.active else " (pasif ilişki: filtre yaymaz)"))


_TR_MAP = str.maketrans({"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u", "â": "a", "î": "i", "û": "u"})
_STOP = {"ve", "ile", "icin", "bir", "bu", "su", "da", "de", "mi", "gibi", "olan", "gore", "bazinda", "bazli",
         "istiyorum", "rapor", "raporu", "dashboard", "goster", "analiz", "the", "of", "by", "and"}


def normalize(text: str) -> str:
    text = (text or "").replace("I", "ı").replace("İ", "i").lower()
    text = text.translate(_TR_MAP)
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", normalize(text)) if len(t) > 1 and t not in _STOP]


def _match(q: str, t: str) -> float:
    """Türkçe ekleri tolere eden eşleşme: 'satislari' ~ 'satis'."""
    if q == t:
        return 1.0
    short, long_ = (q, t) if len(q) <= len(t) else (t, q)
    if len(short) >= 4 and long_.startswith(short):
        return 0.8
    if len(short) >= 5 and long_.startswith(short[:-1]):
        return 0.6
    return 0.0


def _score(query_tokens: list[str], fields: list[tuple[str, float]]) -> float:
    total = 0.0
    for q in query_tokens:
        best = 0.0
        for text, weight in fields:
            for t in tokens(text):
                m = _match(q, t)
                if m:
                    best = max(best, m * weight)
        total += best
    return total


def _rel_sig(r: "DDRelationship", flipped: bool = False) -> str:
    """Önbellek imzası: sözlükteki ilişki tanımı değişirse önbellek geçersiz olur (sözlükteki orijinal yön)."""
    if flipped:
        return f"{r.to_table}|{r.from_table}|" + ",".join(f"{b}={a}" for a, b in r.pairs)
    return f"{r.from_table}|{r.to_table}|" + ",".join(f"{a}={b}" for a, b in r.pairs)


def _group_relationships(rows: list[dict[str, Any]]) -> list[DDRelationship]:
    """Satırları ilişkilere çevirir. Aynı relationship_id'ye sahip satırlar tek bir bileşik ilişkidir."""
    groups: dict[str, DDRelationship] = {}
    for i, r in enumerate(rows):
        ft, tt = str(r["from_table"]).lower(), str(r["to_table"]).lower()
        rid = str(r.get("relationship_id") or f"rel_{i}")
        pair = (str(r["from_column"]).lower(), str(r["to_column"]).lower())
        g = groups.get(rid)
        if g is None:
            card = str(r.get("cardinality") or "").upper().replace("*", "N").replace("M", "N").strip() or None
            if card not in CARDINALITIES:
                card = None
            g = groups[rid] = DDRelationship(rid, ft, tt, [], card, str(r.get("role") or ""), str(r.get("description") or ""))
            if r.get("is_active") is not None and str(r.get("is_active")).strip() != "":
                g.active = str(r.get("is_active")).strip().lower() in ("1", "true", "yes", "evet")
                g._active_explicit = True
        if pair not in g.pairs:
            g.pairs.append(pair)
    rels = list(groups.values())
    # Aynı iki tablo arasında birden çok ilişki varsa ve hiçbiri açıkça işaretlenmemişse ilki aktif olsun
    by_pair: dict[frozenset[str], list[DDRelationship]] = {}
    for rel in rels:
        by_pair.setdefault(frozenset((rel.from_table, rel.to_table)), []).append(rel)
    for group in by_pair.values():
        if len(group) > 1 and not any(g._active_explicit for g in group):
            for i, g in enumerate(group):
                g.active = i == 0
    for rel in rels:
        if rel.cardinality == "1:N":  # 'çok' tarafı her zaman from olsun
            rel.from_table, rel.to_table = rel.to_table, rel.from_table
            rel.pairs = [(b, a) for a, b in rel.pairs]
            rel.cardinality = "N:1"
    return rels


class DataDictionary:
    def __init__(self, settings: Settings, data_connector: Connector | None = None):
        self.settings = settings
        self._data_connector = data_connector
        self._lock = threading.Lock()
        self.tables: dict[str, DDTable] = {}
        self.metrics: list[DDMetric] = []
        self.relationships: list[DDRelationship] = []
        self.view_lineage: dict[str, str] = {}   # "rpt.v_x.kolon" → view'ın kaynak model kolonu
        self.relationship_source = "dictionary"   # dictionary | foreign_keys | name_match | none
        self.catalog_error: str | None = None    # veritabanı kataloğu (yetkiler) okunamadıysa
        self.name_changes: dict[str, str] = {}   # sözlükteki yazım → katalogdaki ad (ör. vw_Satis → dbo.vw_Satis)
        self.missing_in_db: list[str] = []       # sözlükte olup veritabanında bulunamayan nesneler

    # ------------------------------------------------------------------ yükleme
    def _connector(self, cfg: dict) -> Connector:
        from app.data.connections import dictionary_odbc

        odbc = dictionary_odbc(self.settings)  # Bağlantı Ayarları → dictionary.toml odbc → veri bağlantısı
        if odbc:
            from app.data.connector import SqlServerConnector

            return SqlServerConnector(odbc, self.settings.query_timeout_s)
        return self._data_connector or create_connector(self.settings)

    # ------------------------------------------------------------------ otomatik ilişkiler
    _PREFIX = re.compile(r"^(dim|fact|fct|tbl|lkp|lookup|ref|d|f|t)_?", re.IGNORECASE)

    def _auto_relationship_rows(self, tables: dict[str, "DDTable"]) -> tuple[list[dict[str, Any]], str]:
        """İlişki sözlüğü yoksa: 1) veri veritabanındaki foreign key'ler, 2) yoksa anahtar kolon adı eşleşmesi
        (DimProduct.ProductKey ← FactSales.ProductKey gibi). Kardinalite sonra veriden otomatik bulunur."""
        rows = self._foreign_key_rows(tables)
        if rows:
            log.info("İlişki sözlüğü yok: %d ilişki foreign key'lerden alındı.", len({r['relationship_id'] for r in rows}))
            return rows, "foreign_keys"
        rows = self._name_match_rows(tables)
        if rows:
            log.info("İlişki sözlüğü / foreign key yok: %d ilişki anahtar kolon adlarından çıkarıldı.", len(rows))
            return rows, "name_match"
        log.warning("İlişki bulunamadı: JOIN'ler sözlükle doğrulanamaz, filtreler görseller arasında yayılmaz.")
        return [], "none"

    def preview_auto_relationships(self, table_rows: list[dict[str, Any]], column_rows: list[dict[str, Any]]) -> tuple[int, str]:
        """Ayarlar sayfası: ilişki sözlüğü seçilmediyse kaç ilişkinin otomatik bulunacağı (sayı, kaynak)."""
        tabs: dict[str, DDTable] = {}
        for r in table_rows:
            n = str(r["table_name"]).lower()
            tabs[n] = DDTable(n, n, "", "", "", None, display_name=str(r["table_name"]))
        for r in column_rows:
            n = str(r["table_name"]).lower()
            if n in tabs:
                c = str(r["column_name"])
                tabs[n].columns.append(DDColumn(n, c.lower(), c, "", "", "attribute", None, [], False, "", display_name=c))
        rows, src = self._auto_relationship_rows(tabs)
        return len({r["relationship_id"] for r in rows}), src

    def _foreign_key_rows(self, tables: dict[str, "DDTable"]) -> list[dict[str, Any]]:
        con = self._data_connector
        if con is None or getattr(con, "dialect", "") != "tsql":
            return []
        try:
            r = con.execute(
                "SELECT fk.name, SCHEMA_NAME(tp.schema_id) + '.' + tp.name, cp.name, SCHEMA_NAME(tr.schema_id) + '.' + tr.name, cr.name "
                "FROM sys.foreign_keys fk JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id "
                "JOIN sys.tables tp ON tp.object_id = fkc.parent_object_id "
                "JOIN sys.columns cp ON cp.object_id = tp.object_id AND cp.column_id = fkc.parent_column_id "
                "JOIN sys.tables tr ON tr.object_id = fkc.referenced_object_id "
                "JOIN sys.columns cr ON cr.object_id = tr.object_id AND cr.column_id = fkc.referenced_column_id "
                "WHERE fk.is_disabled = 0", 50_000)
        except Exception as e:  # noqa: BLE001
            log.info("Foreign key'ler okunamadı: %s", e)
            return []
        return [{"relationship_id": f"fk:{name}", "from_table": ft, "from_column": fc, "to_table": tt, "to_column": tc}
                for name, ft, fc, tt, tc in r.rows if ft.lower() in tables and tt.lower() in tables]

    def _name_match_rows(self, tables: dict[str, "DDTable"]) -> list[dict[str, Any]]:
        """<Varlık>Key / <Varlık>Id / <Varlık>_id kolonu, adı o varlık olan tablonun anahtarı sayılır; aynı adlı
        kolonu olan diğer tablolar ona bağlanır. Belirsiz eşleşmeler (hedef tablo bulunamayan ya da birden çok) atlanır."""
        stems: dict[str, str] = {}
        for name in tables:
            base = name.split(".", 1)[-1]
            stems[name] = self._PREFIX.sub("", base).lower()
        by_col: dict[str, list[str]] = {}
        for name, t in tables.items():
            for c in t.columns:
                by_col.setdefault(c.name.lower(), []).append(name)
        rows: list[dict[str, Any]] = []
        for col, owners in by_col.items():
            if len(owners) < 2:
                continue
            m = re.match(r"^(.+?)_?(key|id|kod|kodu|no)$", col)
            if not m:
                continue
            entity = m.group(1)
            targets = [t for t in owners if stems[t] == entity]
            if len(targets) != 1:
                continue
            target = targets[0]
            for src in owners:
                if src != target:
                    rows.append({"relationship_id": f"name:{src}.{col}", "from_table": tables[src].display_name or src,
                                 "from_column": col, "to_table": tables[target].display_name or target, "to_column": col})
        return rows

    def load(self) -> "DataDictionary":
        cfg = load_toml(self.settings.dictionary_config)
        con = self._connector(cfg)
        q = cfg["queries"]
        from app.data.connections import open_dictionary_reader, saved_dictionary

        saved = saved_dictionary()  # Bağlantı Ayarları: SQL Server / MySQL / Excel, rol başına bir ya da birden çok kaynak
        catalog = self._read_catalog()   # önce katalog: başlığı tanınmayan sütunlar içerikten bulunur, adlar ona eşlenir
        collected = None
        if saved and saved[0].get("kind") == "none":  # sözlük yok: tablolar / ilişkiler katalogdan gelir
            from app.dictionary.sources import Collected

            collected = Collected()
        elif saved:
            from app.dictionary.sources import collect

            reader, _ = open_dictionary_reader(saved[0])
            collected = collect(reader, saved[1], saved[0].get("mappings"), self.known(catalog))
            for w in collected.warnings:
                log.info("Sözlük: %s", w)
            if collected.errors:
                raise RuntimeError("Sözlük tabloları: " + " ".join(collected.errors))

        from app.dictionary.names import NameResolver, canon

        resolver = NameResolver((sch, obj) for sch, obj, *_ in catalog[0]) if catalog else None
        fix = resolver.resolve if resolver else canon   # katalog yoksa yalnız parantez / veritabanı öneki temizlenir
        name_keys = {"tables": ("table_name",), "columns": ("table_name",), "relationships": ("from_table", "to_table"),
                     "metrics": ("base_table",)}

        def rows(role: str) -> list[dict[str, Any]]:
            if collected is not None:
                out = collected.rows.get(role, [])
            else:
                sql = q.get(role)
                if not sql or not sql.strip():
                    return []
                r = con.execute(sql, 100_000)
                out = [dict(zip([c.lower() for c in r.columns], row)) for row in r.rows]
            for row in out:   # sözlükteki adlar veritabanındaki gerçek nesneye eşlenir
                for k in name_keys.get(role, ()):
                    if row.get(k) not in (None, ""):
                        row[k] = fix(row[k])
            return out

        tables: dict[str, DDTable] = {}
        for r in rows("tables"):
            name = str(r["table_name"]).lower()
            tables[name] = DDTable(name, r.get("business_name") or name, r.get("description") or "",
                                   r.get("subject_area") or "", r.get("grain") or "", r.get("row_count"),
                                   display_name=str(r["table_name"]),
                                   table_type=str(r.get("table_type") or "").lower())
        for r in rows("columns"):
            t = str(r["table_name"]).lower()
            if t not in tables:
                continue
            tables[t].columns.append(DDColumn(
                table=t, name=str(r["column_name"]).lower(), business_name=r.get("business_name") or r["column_name"],
                description=r.get("description") or "", data_type=r.get("data_type") or "",
                role=(r.get("column_role") or "attribute").lower(), default_aggregation=r.get("default_aggregation"),
                synonyms=[s.strip() for s in (r.get("synonyms") or "").split(",") if s.strip()],
                is_pii=bool(r.get("is_pii")), sample_values=r.get("sample_values") or "",
                display_name=str(r["column_name"]),
            ))
        rel_rows = rows("relationships")
        self.relationship_source = "dictionary"
        if not rel_rows and tables:  # ilişki tablosu yok: foreign key'lerden ya da anahtar kolon adlarından çıkar
            rel_rows, self.relationship_source = self._auto_relationship_rows(tables)
        rels = _group_relationships(rel_rows)
        if cfg.get("infer_cardinality", True) and any(r.cardinality is None for r in rels):
            cache = self._cardinality_cache()
            for r in rels:
                if r.cardinality is None and r.id in cache and cache[r.id]["sig"] == _rel_sig(r):
                    r.cardinality = cache[r.id]["cardinality"]
                    if cache[r.id].get("flip"):
                        r.from_table, r.to_table = r.to_table, r.from_table
                        r.pairs = [(b, a) for a, b in r.pairs]
            todo = [r for r in rels if r.cardinality is None]
            if todo:
                before = {r.id: (r.from_table, r.to_table) for r in todo}
                self._infer_cardinality(todo, tables)
                for r in todo:
                    if r.cardinality:
                        flip = before[r.id] != (r.from_table, r.to_table)
                        cache[r.id] = {"cardinality": r.cardinality, "flip": flip,
                                       "sig": _rel_sig(r) if not flip else _rel_sig(r, flipped=True)}
                self._save_cardinality_cache(cache)
        metrics = [DDMetric(r["metric_name"], r.get("business_name") or r["metric_name"], r.get("description") or "",
                            r.get("expression_sql") or "", (r.get("base_table") or "").lower(), r.get("value_format") or "number",
                            [s.strip() for s in (r.get("synonyms") or "").split(",") if s.strip()])
                   for r in rows("metrics")]
        with self._lock:
            self.tables, self.relationships, self.metrics = tables, rels, metrics
        for entry in self._view_registry():
            try:
                self.add_view(entry)
            except Exception as e:  # noqa: BLE001 — bozuk kayıt sözlüğü düşürmesin
                log.warning("View sözlüğe eklenemedi (%s): %s", entry.get("name"), e)
        self.name_changes = dict(resolver.changed) if resolver else {}
        if self.name_changes:
            log.info("Sözlükteki %d ad veritabanındaki nesneye eşlendi: %s", len(self.name_changes),
                     ", ".join(f"{a} → {b}" for a, b in list(self.name_changes.items())[:10]))
        self._merge_catalog(catalog)
        self.mark_snapshots()
        return self

    # ------------------------------------------------------------------ veritabanı kataloğu (yetkiler)
    UNDOCUMENTED_AREA = "Sözlükte tanımsız"
    # sözlükte tanımsız kolonlarda kişisel veri (PII) tahmini — sözlük işaretlemediği için güvenli tarafta kal
    _PII_NAME = re.compile(
        r"(e_?mail|eposta|e_?posta|phone|telefon|gsm|cep_?tel|mobile|fax|address|adres|addressline|postal|posta_?kodu|zip|"
        r"birth|dogum|tckn|tc_?kimlik|kimlik_?no|national_?id|ssn|passport|pasaport|iban|card_?number|kart_?no|"
        r"first_?name|last_?name|middle_?name|full_?name|ad_?soyad|^ad$|^soyad|isim|salary|maas|income|gelir)", re.IGNORECASE)
    _SKIP_OBJECTS = {"dbo.sysdiagrams"}   # SSMS diyagram tablosu
    # EDWDM view yetki seviyeleri: her veri seti için ana view + ekli varyantlar (personel / müşteri tanımlayıcı
    # verilerinin görünürlüğü). Ana view sözlükte tanımlıysa varyantlar da tanımlı sayılır (iş adı, açıklama, kolonlar).
    # Uzun ekler önce denenir (PersonnelMasked, Masked'tan önce).
    VIEW_VARIANTS: list[tuple[str, str, str]] = [
        ("PersonnelExcluded", "personel hariç", "Personel kayıtları filtrelenmiş, diğer müşteri verileri açık görünür."),
        ("PersonnelMasked", "personel maskeli", "Personeli ayırt edici veriler maskeli, müşteri verileri açık görünür."),
        ("Masked", "maskeli", "Personel dahil tüm müşteriler maskeli görünür."),
    ]

    def _variant_base(self, sch: str, obj: str) -> tuple["DDTable", tuple[str, str, str]] | None:
        """vXMasked / vXPersonnelExcluded / vXPersonnelMasked → sözlükte tanımlı ana view (vX) varsa onu döner."""
        low = obj.lower()
        for v in self.VIEW_VARIANTS:
            suf = v[0].lower()
            if low.endswith(suf) and len(low) > len(suf):
                base = self.tables.get(f"{sch}.{obj[:-len(suf)]}".lower())
                if base is not None and base.documented and not base.variant_of:
                    return base, v
        return None

    def _variant_table(self, full: str, name: str, base: "DDTable", v: tuple[str, str, str], rows: Any, otype: str,
                       cols: list[tuple[str, str, str]]) -> "DDTable":
        """Varyant view: tablo düzeyi bilgiler ana view'dan (+ varyant notu); aynı adlı kolonların iş adı / açıklama /
        rol / toplama / eş anlamlılar / kişisel veri işareti ana view'dan, diğerleri veritabanından."""
        _suffix, label, note = v
        has_bn = bool(base.business_name) and base.business_name.lower() not in (base.name, (base.display_name or "").lower())
        bn = f"{base.business_name} ({label})" if has_bn else name   # ana view'ın iş adı yoksa varyantın kendi adı
        desc = " ".join(x for x in (base.description, f"Yetki seviyesi: {note}") if x)
        t = DDTable(full, bn, desc, base.subject_area, base.grain, int(rows) if rows is not None else None,
                    display_name=name, table_type=base.table_type, documented=True, in_db=True, can_select=True,
                    object_type=otype, variant_of=base.name, snapshot_date=base.snapshot_date)
        by_name = {c.name: c for c in base.columns}
        for cname, ctyp, cdesc in cols:
            b = by_name.get(cname.lower())
            if b is not None:
                t.columns.append(DDColumn(full, b.name, b.business_name, b.description or cdesc, b.data_type or ctyp, b.role,
                                          b.default_aggregation, list(b.synonyms), b.is_pii, b.sample_values,
                                          display_name=cname))
            else:
                t.columns.append(DDColumn(full, cname.lower(), cname, cdesc, ctyp, "attribute", None, [], self._guess_pii(cname), "",
                                          display_name=cname))
        return t

    @classmethod
    def _guess_pii(cls, column: str) -> bool:
        return bool(cls._PII_NAME.search(re.sub(r"(?<=[a-z])(?=[A-Z])", "_", column)))

    @staticmethod
    def known(catalog: tuple[list, list] | None, column_names: list[str] | None = None):
        """Katalogdan içerik tanıma için ad kümeleri (sources.Known); katalog yoksa None."""
        if catalog is None:
            return None
        from app.dictionary.sources import Known

        cols = column_names if column_names is not None else [c for _s, _o, c, *_ in catalog[1]]
        return Known.from_catalog([(s, o) for s, o, *_ in catalog[0]], cols)

    def column_names(self) -> list[str]:
        """Veritabanındaki farklı kolon adları (katalogdaki tüm kolonları okumadan; Ayarlar testi için)."""
        r = self._data_connector.execute(  # type: ignore[union-attr]
            "SELECT DISTINCT c.name FROM sys.columns c JOIN sys.objects o ON o.object_id = c.object_id "
            "WHERE o.type IN ('U', 'V') AND o.is_ms_shipped = 0", 500_000)
        return [str(x[0]) for x in r.rows]

    def _read_catalog(self, columns: bool = True) -> tuple[list, list] | None:
        """(nesneler, kolonlar) — veri bağlantısının yetkisiyle. Okunamazsa None (catalog_error dolar)."""
        con = self._data_connector
        self.catalog_error = None
        if con is None or getattr(con, "dialect", "") != "tsql":
            return None
        try:
            objs = con.execute(
                "SELECT s.name, o.name, o.type, "
                "HAS_PERMS_BY_NAME(QUOTENAME(s.name) + '.' + QUOTENAME(o.name), 'OBJECT', 'SELECT'), "
                "CAST(ep.value AS nvarchar(1000)), "
                "(SELECT SUM(p.rows) FROM sys.partitions p WHERE p.object_id = o.object_id AND p.index_id IN (0, 1)) "
                "FROM sys.objects o JOIN sys.schemas s ON s.schema_id = o.schema_id "
                "LEFT JOIN sys.extended_properties ep ON ep.class = 1 AND ep.major_id = o.object_id AND ep.minor_id = 0 "
                "AND ep.name = 'MS_Description' "
                "WHERE o.type IN ('U', 'V') AND o.is_ms_shipped = 0 AND s.name NOT IN ('sys', 'INFORMATION_SCHEMA')", 200_000).rows
            cols = [] if not columns else con.execute(
                "SELECT s.name, o.name, c.name, TYPE_NAME(c.user_type_id), CAST(ep.value AS nvarchar(1000)) "
                "FROM sys.columns c JOIN sys.objects o ON o.object_id = c.object_id JOIN sys.schemas s ON s.schema_id = o.schema_id "
                "LEFT JOIN sys.extended_properties ep ON ep.class = 1 AND ep.major_id = c.object_id AND ep.minor_id = c.column_id "
                "AND ep.name = 'MS_Description' "
                "WHERE o.type IN ('U', 'V') AND o.is_ms_shipped = 0 AND s.name NOT IN ('sys', 'INFORMATION_SCHEMA') "
                "ORDER BY s.name, o.name, c.column_id", 2_000_000).rows
        except Exception as e:  # noqa: BLE001
            self.catalog_error = str(e)[:300]
            log.warning("Veritabanı kataloğu okunamadı (yetkiler sözlükten bilinemiyor): %s", e)
            return None
        return objs, cols

    def _merge_catalog(self, catalog: tuple[list, list] | None = None) -> None:
        """Veritabanındaki tablo / view'ları bağlanan hesabın yetkisiyle okur:
          * sözlükteki nesnelere in_db / can_select işlenir (yetki veritabanından gelir, sabit şema listesinden değil);
          * sözlükte olmayan ama SELECT yetkisi olan nesneler eklenir (documented=False; açıklama MS_Description'dan);
          * sözlükteki tablolara veritabanında olup sözlükte olmayan kolonlar eklenir (sorgu ekranı / öneriler için);
          * eklenen nesneler arasındaki foreign key'ler ilişki olarak alınır (N:1).
        Katalog okunamazsa (izin / bağlantı) sözlük olduğu gibi kalır."""
        if catalog is None:
            catalog = self._read_catalog()
        if catalog is None:
            return
        objs, cols = catalog
        by_obj: dict[str, list[tuple[str, str, str]]] = {}
        for sch, obj, col, typ, desc in cols:
            by_obj.setdefault(f"{sch}.{obj}".lower(), []).append((str(col), str(typ or ""), str(desc or "")))
        seen: set[str] = set()
        added = 0
        variants = 0
        with self._lock:
            for sch, obj, typ, perm, desc, rows in objs:
                full = f"{sch}.{obj}".lower()
                seen.add(full)
                can = bool(perm)
                t = self.tables.get(full)
                otype = "view" if str(typ).strip().upper() == "V" else "table"
                if t is not None:  # sözlükte var: yetkiyi işle, nesne tipini (tablo / view) al, eksik kolonları ekle
                    t.in_db, t.can_select, t.object_type = True, can, otype
                    have = {c.name for c in t.columns}
                    db_types = {cn.lower(): ct for cn, ct, _cd in by_obj.get(full, [])}
                    for c in t.columns:   # sözlükte veri tipi yoksa veritabanından (ölçü / boyut / tarih tahmini için)
                        if not c.data_type and db_types.get(c.name):
                            c.data_type = db_types[c.name]
                    for cname, ctyp, cdesc in by_obj.get(full, []):
                        if cname.lower() not in have:
                            t.columns.append(DDColumn(full, cname.lower(), cname, cdesc, ctyp, "attribute", None, [],
                                                      self._guess_pii(cname), "", display_name=cname))
                    if not t.description and desc:
                        t.description = str(desc)
                    continue
                if not can or full in self._SKIP_OBJECTS:  # yetki yoksa listelenmez (SQL Server zaten çoğu zaman göstermez)
                    continue
                name = f"{sch}.{obj}"
                vb = self._variant_base(str(sch), str(obj)) if otype == "view" else None
                if vb is not None:   # ana view sözlükte: varyant da tanımlı sayılır
                    self.tables[full] = self._variant_table(full, name, vb[0], vb[1], rows, otype, by_obj.get(full, []))
                    variants += 1
                    continue
                t = DDTable(full, name, str(desc or ""), self.UNDOCUMENTED_AREA, "", int(rows) if rows is not None else None,
                            display_name=name, table_type="view" if str(typ).strip().upper() == "V" else "",
                            documented=False, in_db=True, can_select=True, object_type=otype)
                t.columns = [DDColumn(full, c.lower(), c, cd, ct, "attribute", None, [], self._guess_pii(c), "", display_name=c)
                             for c, ct, cd in by_obj.get(full, [])]
                self.tables[full] = t
                added += 1
            for name, t in self.tables.items():
                if name not in seen and t.in_db is None:
                    t.in_db = False
            self.missing_in_db = sorted(t.display_name or n for n, t in self.tables.items() if t.in_db is False)
            if added:
                self._add_catalog_foreign_keys()
        log.info("Veritabanı kataloğu: %d nesne, %d tanesi sözlükte tanımsız olarak eklendi, %d view varyantı ana view'dan tanımlandı.",
                 len(seen), added, variants)
        if self.missing_in_db:
            log.warning("Sözlükteki %d nesne veritabanında bulunamadı: %s", len(self.missing_in_db), ", ".join(self.missing_in_db[:15]))

    def _add_catalog_foreign_keys(self) -> None:
        """Sözlükte tanımsız eklenen nesnelerin foreign key'lerini (henüz ilişki yoksa) N:1 ilişki olarak ekler."""
        existing = {frozenset((r.from_table, r.to_table)) for r in self.relationships}
        rows = [r for r in self._foreign_key_rows(self.tables)
                if (not self.tables[r["from_table"].lower()].documented or not self.tables[r["to_table"].lower()].documented)
                and frozenset((r["from_table"].lower(), r["to_table"].lower())) not in existing]
        if not rows:
            return
        for r in rows:
            r["cardinality"] = "N:1"   # foreign key: başvuran tablo çok, başvurulan tek
        self.relationships = self.relationships + _group_relationships(rows)

    # ------------------------------------------------------------------ onaylı view'lar
    def _view_registry(self) -> list[dict[str, Any]]:
        from app.data.views import ViewRegistry

        return ViewRegistry(self.settings.views_registry).all()

    def add_view(self, entry: dict[str, Any]) -> "DDTable":
        """Kalıcılaştırılmış bir dataset view'ını sözlüğe ekler; kaynak kolonlardan boyutlara ilişki kurar."""
        name = entry["name"].lower()
        cols = []
        for c in entry["columns"]:
            src = (c.get("lineage") or "").lower()
            src_col = None
            if src.count(".") >= 2:
                st, _, sc = src.rpartition(".")
                t = self.tables.get(st)
                src_col = next((x for x in t.columns if x.name == sc), None) if t else None
            if src:
                self.view_lineage[f"{name}.{c['name'].lower()}"] = src
            is_num = c.get("type") == "number"
            cols.append(DDColumn(
                table=name, name=c["name"].lower(), business_name=c.get("label") or c["name"],
                description=f"Kaynak: {src}" if src else "", data_type=c.get("type") or "",
                role="measure" if is_num else "dimension", default_aggregation="sum" if is_num else None,
                synonyms=src_col.synonyms if src_col else [], is_pii=bool(src_col and src_col.is_pii),
                sample_values="", display_name=c["name"]))
        table = DDTable(name, entry.get("business_name") or entry["name"], entry.get("description") or "",
                        "Onaylı rapor view'ları", "Rapor dataset'i (özet)", None, cols,
                        display_name=entry["name"], table_type="view", object_type="view")
        self.tables[name] = table
        self.relationships = [r for r in self.relationships if r.from_table != name]
        kinds = self._table_kinds()
        new_rels = []
        for c in entry["columns"]:
            src = (c.get("lineage") or "").lower()
            st, _, sc = src.rpartition(".")
            if st in self.tables and kinds.get(st) == "dimension":
                new_rels.append(DDRelationship(f"{name}__{c['name'].lower()}", name, st, [(c["name"].lower(), sc)]))
        if new_rels:
            self._infer_cardinality(new_rels, self.tables)
            # yalnız boyut tarafı tekilse (N:1 / 1:1) ilişki anlamlı: filtre view'a yayılabilir
            new_rels = [r for r in new_rels if r.from_table == name and r.to_unique]
        self.relationships.extend(new_rels)
        return table

    def _infer_cardinality(self, rels: list[DDRelationship], tables: dict[str, DDTable]) -> None:
        """Sözlükte kardinalite yoksa veriden çıkarır: bir taraf, join kolonları o tabloda tekilse '1'dir.

        Önce PK/unique index metadatasına bakılır (ucuz). Index yoksa ve tablo küçükse
        (≤ 2M satır) COUNT(DISTINCT) ile ölçülür; büyük tablolar 'N' (çok) kabul edilir.
        """
        con = self._data_connector or create_connector(self.settings)
        q = lambda n: "[" + n.replace("]", "]]") + "]"  # noqa: E731
        unique_sets: dict[str, list[set[str]]] = {}
        try:
            r = con.execute("""
                SELECT LOWER(SCHEMA_NAME(t.schema_id) + '.' + t.name), i.index_id, LOWER(c.name)
                FROM sys.indexes i
                JOIN sys.tables t ON t.object_id = i.object_id
                JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.is_included_column = 0
                JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
                WHERE i.is_unique = 1""", 200_000)
            idx: dict[tuple[str, int], set[str]] = {}
            for t, i, c in r.rows:
                idx.setdefault((t, i), set()).add(c)
            for (t, _), cols in idx.items():
                unique_sets.setdefault(t, []).append(cols)
        except Exception as e:  # noqa: BLE001
            log.warning("Unique index metadatası okunamadı: %s", e)
        cache: dict[tuple[str, frozenset[str]], bool | None] = {}

        def unique(table: str, cols: list[str]) -> bool | None:
            key = (table, frozenset(cols))
            if key in cache:
                return cache[key]
            res: bool | None = None
            if any(s <= set(cols) for s in unique_sets.get(table, [])):
                res = True
            else:
                rc = tables.get(table).row_count if tables.get(table) else None
                if rc is not None and rc > 2_000_000:
                    res = False
                else:
                    schema, _, name = table.partition(".")
                    t_sql = f"{q(schema)}.{q(name)}"
                    col_sql = ", ".join(q(c) for c in cols)
                    try:
                        n, d = con.execute(f"SELECT (SELECT COUNT(*) FROM {t_sql}), "
                                           f"(SELECT COUNT(*) FROM (SELECT DISTINCT {col_sql} FROM {t_sql}) x)", 1).rows[0]
                        res = n == d
                    except Exception as e:  # noqa: BLE001
                        log.warning("Kardinalite ölçülemedi (%s %s): %s", table, cols, e)
            cache[key] = res
            return res

        for rel in rels:
            if rel.cardinality is not None:
                continue
            fu = unique(rel.from_table, [a for a, _ in rel.pairs])
            tu = unique(rel.to_table, [b for _, b in rel.pairs])
            if fu is None or tu is None:
                continue
            if fu and not tu:  # 1:N → N:1 yönüne çevir
                rel.from_table, rel.to_table = rel.to_table, rel.from_table
                rel.pairs = [(b, a) for a, b in rel.pairs]
                fu, tu = tu, fu
            rel.cardinality = f"{'1' if fu else 'N'}:{'1' if tu else 'N'}"

    # ------------------------------------------------------------------ sorgular
    # ------------------------------------------------------------------ günlük anlık görüntüler
    SNAPSHOT_NOTE = 'GÜNLÜK ANLIK GÖRÜNTÜ: her kayıt her gün için ayrı satır olarak tekrarlanır ({col}). Toplam / sayı için tek gün seç: WHERE {col} = (SELECT MAX({col}) FROM {table}) (son gün). Trendde her dönemin tek gününü al (ör. ay sonu); başka bir anlık görüntüyle birleştirirken {col} kolonlarını da eşle.'

    def snapshot_columns(self) -> list[str]:
        """Günlük anlık görüntü tarih kolonları (dictionary.toml: snapshot_date_columns; varsayılan DataDate)."""
        try:
            cols = load_toml(self.settings.dictionary_config).get("snapshot_date_columns")
        except Exception:  # noqa: BLE001
            cols = None
        return [str(c) for c in (cols if isinstance(cols, list) else ["DataDate"]) if str(c).strip()]

    def mark_snapshots(self) -> int:
        """Tarih kolonu (DataDate …) olan tablo / view'lar günlük anlık görüntüdür: her kayıt her gün için tekrarlanır.
        Sorgu doğrulayıcı bunlarda tek gün seçilmeden toplama yapılmasını engeller (snapshot_guard.py)."""
        names = {c.lower() for c in self.snapshot_columns()}
        n = 0
        for t in self.tables.values():
            if t.snapshot_date:
                continue
            c = next((c for c in t.columns if c.name.lower() in names), None)
            if c is not None:
                t.snapshot_date = c.display_name or c.name
                if c.role in ("attribute", "", "dimension"):
                    c.role = "date"
                n += 1
        if n:
            log.info("Günlük anlık görüntü olarak işaretlenen nesne: %d (%s)", n, ", ".join(sorted(names)))
        return n

    def has_table(self, name: str) -> bool:
        return name.lower() in self.tables

    @staticmethod
    def usable(t: "DDTable") -> bool:
        """Sorguda kullanılabilir: veritabanında var (katalog) ve SELECT yetkisi var. Sözlükte olup veritabanında
        bulunmayan nesneler (ör. sözlüğe yazılmış stored procedure'ler) agent'a hiç gösterilmez."""
        return t.in_db is not False and t.can_select is not False

    def pii_columns(self, table: str) -> set[str]:
        t = self.tables.get(table.lower())
        return {c.name for c in t.columns if c.is_pii} if t else set()

    def relationships_for(self, table: str) -> list[DDRelationship]:
        t = table.lower()
        return [r for r in self.relationships if t in (r.from_table, r.to_table)]

    def join_path(self, sources: list[str], target: str, max_hops: int = 4) -> list[DDRelationship] | None:
        """Kaynak tablolardan hedefe en kısa ilişki zinciri (yönsüz, yalnız aktif ilişkiler öncelikli)."""
        from collections import deque

        target = target.lower()
        start = [t.lower() for t in sources if t.lower() in self.tables]
        if target in start:
            return []
        rels = sorted(self.relationships, key=lambda r: not r.active)
        prev: dict[str, tuple[str, DDRelationship] | None] = {t: None for t in start}
        q = deque((t, 0) for t in start)
        while q:
            u, d = q.popleft()
            if d >= max_hops:
                continue
            for r in rels:
                if u not in (r.from_table, r.to_table) or r.from_table == r.to_table:
                    continue
                v = r.to_table if u == r.from_table else r.from_table
                if v in prev:
                    continue
                prev[v] = (u, r)
                if v == target:
                    path, cur = [], v
                    while prev[cur] is not None:
                        pu, pr = prev[cur]
                        path.append(pr)
                        cur = pu
                    return list(reversed(path))
                q.append((v, d + 1))
        return None

    def tables_with_column(self, column: str) -> list[str]:
        col = column.lower()
        return [t.name for t in self.tables.values() if any(c.name == col for c in t.columns)]

    def search(self, query: str, limit: int = 6, allowed: Callable[["DDTable"], bool] | None = None) -> list[dict[str, Any]]:
        """Tablo / view araması — yalnız kullanılabilir nesneler (allowed verilirse rol politikası da)."""
        qt = tokens(query)
        if not qt:
            return []
        ok = allowed or self.usable
        results = []
        for t in self.tables.values():
            if not ok(t):
                continue
            t_score = _score(qt, [(t.business_name, 3), (t.name, 2), (t.description, 1.2), (t.subject_area, 1)])
            col_hits = []
            for c in t.columns:
                s = _score(qt, [(c.business_name, 3), (c.name, 2), (" ".join(c.synonyms), 2.5), (c.description, 1)])
                if s > 0:
                    col_hits.append((s, c))
            col_hits.sort(key=lambda x: -x[0])
            score = t_score + sum(s for s, _ in col_hits[:4]) * 0.7
            # olgu (fact) tablolarını hafif öne al: ölçüler oradadır
            if score > 0 and any(c.role == "measure" for c in t.columns):
                score *= 1.15
            if score > 0:
                results.append({
                    "table": t.name, "business_name": t.business_name, "description": t.description,
                    "subject_area": t.subject_area, "score": round(score, 2),
                    **({"snapshot_date": t.snapshot_date} if t.snapshot_date else {}),
                    "columns": [{"name": c.name, "business_name": c.business_name, "role": c.role,
                                 **({"pii": True} if c.is_pii else {})} for _, c in col_hits[:8]],
                })
        results.sort(key=lambda r: -r["score"])
        return results[:limit]

    def search_metrics(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        qt = tokens(query)
        scored = []
        for m in self.metrics:
            s = _score(qt, [(m.business_name, 3), (m.name, 2), (" ".join(m.synonyms), 2.5), (m.description, 1)]) if qt else 1
            if s > 0:
                scored.append((s, m))
        scored.sort(key=lambda x: -x[0])
        return [{"metric": m.name, "business_name": m.business_name, "description": m.description,
                 "expression_sql": m.expression_sql, "base_table": m.base_table, "format": m.value_format}
                for _, m in scored[:limit]]

    def table_details(self, name: str, include_pii: bool) -> dict[str, Any] | None:
        t = self.tables.get(name.lower())
        if not t:
            return None
        cols = []
        for c in t.columns:
            d: dict[str, Any] = {"name": c.name, "business_name": c.business_name, "type": c.data_type, "role": c.role}
            if c.description:
                d["description"] = c.description
            if c.default_aggregation:
                d["default_aggregation"] = c.default_aggregation
            if c.sample_values:
                d["sample_values"] = c.sample_values
            if c.is_pii:
                d["pii"] = True
                if not include_pii:
                    d["note"] = "Kişisel veri — sorgulanamaz"
            cols.append(d)
        out = {
            "table": t.name, "business_name": t.business_name, "description": t.description,
            "grain": t.grain, "row_count": t.row_count, "columns": cols,
            "joins": [r.describe() for r in self.relationships_for(t.name)],
        }
        if t.snapshot_date:
            out["snapshot"] = {"date_column": t.snapshot_date,
                               "rule": self.SNAPSHOT_NOTE.format(col=t.snapshot_date, table=t.display_name or t.name)}
        return out

    def is_view(self, name: str, kinds: dict[str, str] | None = None) -> bool:
        """Nesne tipi: veritabanı kataloğundan (sözlükte tablo / view ayrımı olmasa da); katalog yoksa sözlükteki table_type."""
        t = self.tables.get(name)
        if t is not None and t.object_type:
            return t.object_type == "view"
        return (kinds if kinds is not None else self._table_kinds()).get(name) == "view"

    def _table_kinds(self) -> dict[str, str]:
        """fact / dimension / bridge. Sözlükte table_type varsa o; yoksa ilişki grafiğinden:
        köprü = hiçbir tablonun işaret etmediği ve başka bir 'merkez' tabloya (≥2 dış ilişkisi olan) işaret eden tablo;
        fact = köprüler dışında kimsenin işaret etmediği, kendisi en az bir tabloya işaret eden tablo; kalanlar boyut."""
        out: dict[str, set[str]] = {t: set() for t in self.tables}
        inc: dict[str, set[str]] = {t: set() for t in self.tables}
        for r in self.relationships:
            if r.from_table in out and r.to_table in inc:
                out[r.from_table].add(r.to_table)
                inc[r.to_table].add(r.from_table)
        bridges = {t for t in self.tables if len(out[t]) >= 2 and not inc[t] and any(len(out[x]) >= 2 for x in out[t])}
        kinds = {}
        for name, t in self.tables.items():
            if t.table_type in ("fact", "dimension", "bridge", "view"):
                kinds[name] = t.table_type
            elif name in bridges:
                kinds[name] = "bridge"
            elif out[name] and not (inc[name] - bridges):
                kinds[name] = "fact"
            else:
                kinds[name] = "dimension"
        return kinds

    def _cache_path(self):
        return self.settings.cache_dir / "cardinality.json"

    def _cardinality_cache(self) -> dict[str, Any]:
        import json

        try:
            return json.loads(self._cache_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_cardinality_cache(self, cache: dict[str, Any]) -> None:
        import json

        try:
            self._cache_path().parent.mkdir(parents=True, exist_ok=True)
            self._cache_path().write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as e:
            log.warning("Kardinalite önbelleği yazılamadı: %s", e)

    # ------------------------------------------------------------------ ilişkisel model (UI)
    def model(self) -> dict[str, Any]:
        """Tablolar, kolonlar ve ilişkiler: arayüzdeki model diyagramı için."""
        rels = self.relationships
        key_cols: dict[str, set[str]] = {}
        for r in rels:
            key_cols.setdefault(r.from_table, set()).update(a for a, _ in r.pairs)
            key_cols.setdefault(r.to_table, set()).update(b for _, b in r.pairs)
        kinds = self._table_kinds()
        out_tables = []
        for t in self.tables.values():
            kind = kinds[t.name]
            schema, _, short = (t.display_name or t.name).partition(".")
            out_tables.append({
                "id": t.name, "name": t.display_name or t.name, "schema": schema, "short_name": short or schema,
                "business_name": t.business_name, "description": t.description, "subject_area": t.subject_area,
                "grain": t.grain, "row_count": t.row_count, "kind": kind,
                "columns": [{"name": c.display_name or c.name, "id": c.name, "business_name": c.business_name,
                             "data_type": c.data_type, "role": c.role, "is_pii": c.is_pii,
                             "is_key": c.name in key_cols.get(t.name, set()), "description": c.description}
                            for c in t.columns],
            })

        def col_display(table: str, col: str) -> str:
            tt = self.tables.get(table)
            return next((c.display_name for c in tt.columns if c.name == col and c.display_name), col) if tt else col

        return {
            "tables": out_tables,
            "relationships": [{
                "id": r.id, "from_table": r.from_table, "to_table": r.to_table,
                "pairs": [[a, b] for a, b in r.pairs],
                "pairs_display": [[col_display(r.from_table, a), col_display(r.to_table, b)] for a, b in r.pairs],
                "cardinality": r.cardinality, "role": r.role, "active": r.active,
            } for r in rels],
        }


    # ------------------------------------------------------------------ anlık görüntü (testler / çevrimdışı)
    def to_snapshot(self) -> dict[str, Any]:
        """Yüklenmiş sözlüğün tamamı (kardinalite dahil) JSON'a çevrilebilir sözlük olarak."""
        from dataclasses import asdict

        return {
            "tables": [{**{k: v for k, v in asdict(t).items() if k != "columns"},
                        "columns": [asdict(c) for c in t.columns]} for t in self.tables.values()],
            "relationships": [{**{k: v for k, v in asdict(r).items() if not k.startswith("_")},
                               "pairs": [list(p) for p in r.pairs]} for r in self.relationships],
            "metrics": [asdict(m) for m in self.metrics],
        }

    @classmethod
    def from_snapshot(cls, settings: Settings, snap: dict[str, Any], data_connector: Connector | None = None) -> "DataDictionary":
        dd = cls(settings, data_connector)
        for t in snap["tables"]:
            cols = [DDColumn(**c) for c in t["columns"]]
            dd.tables[t["name"]] = DDTable(**{k: v for k, v in t.items() if k != "columns"}, columns=cols)
        dd.relationships = [DDRelationship(**{**r, "pairs": [tuple(p) for p in r["pairs"]]}) for r in snap["relationships"]]
        dd.metrics = [DDMetric(**m) for m in snap["metrics"]]
        return dd
