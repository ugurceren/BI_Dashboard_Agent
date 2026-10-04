"""Sözlük kaynakları: her sözlük rolü (tablolar / kolonlar / ilişkiler / metrikler) bir ya da birden çok
tablodan (SQL Server, MySQL) ya da Excel sayfasından okunur. Yapı esnektir:

  * TEK TABLO: yalnız "Kolonlar" yeterli. Tablo listesi kolon satırlarındaki table_name'den çıkarılır; tablo
    düzeyindeki bilgiler aynı satırlardaki table_business_name / table_description / subject_area / grain /
    row_count / table_type kolonlarından okunur (tabloya ait satırlarda tekrar edebilir).
  * ÇOK TABLO: Tablolar / Kolonlar / İlişkiler / Metrikler ayrı tablolarda; aynı role birden çok tablo verilebilir.
  * KARIŞIK: ör. Kolonlar + İlişkiler; ya da Tablolar ayrı ama bazı tablolar yalnız kolon sözlüğünde.
  * İlişkiler seçilmezse repository SQL Server foreign key'lerinden ya da anahtar kolon adlarından çıkarır.
  * Kolon adları esnek: "Tablo Adı", "Kolon Adı", "Açıklama", "Kişisel Veri" gibi Türkçe / İngilizce karşılıklar tanınır.

  * her kaynakta var olan beklenen kolonlar alınır, eksik İSTEĞE BAĞLI kolonlar boş (None) gelir;
  * ZORUNLU kolon eksikse o tablo / sayfa kullanılmaz ve açık bir hata döner;
  * aynı role birden çok kaynak verilirse satırlar birleştirilir (ör. satış ve finans ekiplerinin ayrı kolon sözlükleri);
  * değerler normalize edilir: is_pii / is_active "Evet", "1", "x", True → True; row_count sayıya çevrilir.

Sözlük yalnızca meta veridir (tablo / kolon açıklamaları); rapor verisi her zaman SQL Server'dan okunur.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

ROLES: dict[str, dict[str, Any]] = {
    "tables": {
        "label": "Tablolar", "required": ["table_name"],
        "optional": ["business_name", "description", "subject_area", "grain", "row_count", "table_type"],
        "must": False,   # seçilmezse Kolonlar'dan çıkarılır
    },
    "columns": {
        "label": "Kolonlar", "required": ["table_name", "column_name"],
        "optional": ["business_name", "description", "data_type", "column_role", "default_aggregation",
                     "synonyms", "is_pii", "sample_values",
                     # tek tablolu sözlükler için tablo düzeyi bilgiler
                     "table_business_name", "table_description", "subject_area", "grain", "row_count", "table_type"],
        "must": True,
    },
    "relationships": {
        "label": "İlişkiler", "required": ["from_table", "from_column", "to_table", "to_column"],
        "optional": ["relationship_id", "cardinality", "role", "is_active"],
        "must": False,
    },
    "metrics": {
        "label": "Metrikler", "required": ["metric_name", "expression_sql"],
        "optional": ["business_name", "description", "base_table", "value_format", "synonyms"],
        "must": False,
    },
}
# kaynak türüne göre varsayılan tablo / sayfa adları
DEFAULTS: dict[str, dict[str, list[str]]] = {
    "sqlserver": {"tables": ["meta.dd_tables"], "columns": ["meta.dd_columns"],
                  "relationships": ["meta.dd_relationships"], "metrics": ["meta.dd_metrics"]},
    "mysql": {"tables": ["dd_tables"], "columns": ["dd_columns"], "relationships": ["dd_relationships"], "metrics": ["dd_metrics"]},
    "excel": {"tables": ["Tablolar"], "columns": ["Kolonlar"], "relationships": ["İlişkiler"], "metrics": ["Metrikler"]},
    # sözlük yok: yalnız veritabanı kataloğu (yetkili tablo / view, MS_Description, foreign key'ler)
    "none": {"tables": [], "columns": [], "relationships": [], "metrics": []},
}
DEFAULT_SOURCES = DEFAULTS["sqlserver"]
KINDS = tuple(DEFAULTS)

_BOOL_FIELDS = {"is_pii", "is_active"}
_INT_FIELDS = {"row_count"}
_TRUE = {"1", "true", "yes", "y", "evet", "e", "x", "✓", "doğru", "var"}


def default_sources(kind: str) -> dict[str, list[str]]:
    return {k: list(v) for k, v in DEFAULTS.get(kind, DEFAULT_SOURCES).items()}


def suggest_role(columns: set[str]) -> str | None:
    """Kolonlarına göre en uygun sözlük rolü (tüm zorunlu kolonları olanlar içinden en çok eşleşen)."""
    best, score = None, 0
    for role, spec in ROLES.items():
        if not all(c in columns for c in spec["required"]):
            continue
        s = len(spec["required"]) * 2 + sum(c in columns for c in spec["optional"])
        if s > score:
            best, score = role, s
    return best


_FOLD = str.maketrans({"ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u",
                       "ö": "o", "Ö": "o", "ç": "c", "Ç": "c", "â": "a", "î": "i", "û": "u"})
# yaygın Türkçe / İngilizce kolon adı karşılıkları → standart ad (karşılaştırma katlanmış, küçük harfle)
ALIASES: dict[str, str] = {
    # şema ayrı kolondaysa (ör. INFORMATION_SCHEMA dökümü: TABLE_SCHEMA + TABLE_NAME) table_name'e birleştirilir
    **dict.fromkeys(["schema", "sema", "sema_adi", "table_schema", "tablo_semasi", "owner", "schema_adi", "view_schema"], "schema_name"),
    **dict.fromkeys(["tablo", "tablo_adi", "tablo_ismi", "tablename", "table", "object_name", "nesne_adi", "tam_tablo_adi",
                     "view_name", "viewname", "view", "view_adi", "nesne", "nesne_ismi", "obje_adi", "tablo_view_adi", "table_or_view"], "table_name"),
    **dict.fromkeys(["kolon", "kolon_adi", "kolon_ismi", "alan", "alan_adi", "sutun", "sutun_adi", "columnname", "column", "field_name", "field"], "column_name"),
    **dict.fromkeys(["is_adi", "is_ismi", "gorunen_ad", "gorunen_adi", "kolon_is_adi", "alan_is_adi", "display_name", "label", "etiket"], "business_name"),
    **dict.fromkeys(["aciklama", "kolon_aciklamasi", "alan_aciklamasi", "tanim", "desc", "comment", "yorum",
                     "column_description", "column_desc", "field_description", "kolon_aciklama", "alan_aciklama"], "description"),
    **dict.fromkeys(["tablo_is_adi", "tablo_gorunen_ad", "table_label", "table_display_name"], "table_business_name"),
    **dict.fromkeys(["tablo_aciklamasi", "tablo_tanimi", "table_desc", "table_comment", "view_description", "view_aciklamasi"], "table_description"),
    **dict.fromkeys(["konu_alani", "konu", "domain", "alan_grubu", "is_alani"], "subject_area"),
    **dict.fromkeys(["granularite", "tanecik", "satir_duzeyi"], "grain"),
    **dict.fromkeys(["satir_sayisi", "kayit_sayisi", "rows"], "row_count"),
    **dict.fromkeys(["tablo_turu", "tablo_tipi", "table_kind"], "table_type"),
    **dict.fromkeys(["veri_tipi", "veri_turu", "tip", "type", "datatype"], "data_type"),
    **dict.fromkeys(["kolon_rolu", "rol_tipi", "role_type"], "column_role"),
    **dict.fromkeys(["varsayilan_toplama", "toplama", "aggregation"], "default_aggregation"),
    **dict.fromkeys(["es_anlamlilar", "es_anlamli", "anahtar_kelimeler", "keywords", "aliases"], "synonyms"),
    **dict.fromkeys(["kisisel_veri", "kvkk", "pii", "hassas", "hassas_veri", "is_sensitive"], "is_pii"),
    **dict.fromkeys(["ornek_degerler", "ornek", "samples"], "sample_values"),
    **dict.fromkeys(["kaynak_tablo", "from_tablo"], "from_table"),
    **dict.fromkeys(["kaynak_kolon", "from_kolon"], "from_column"),
    **dict.fromkeys(["hedef_tablo", "to_tablo"], "to_table"),
    **dict.fromkeys(["hedef_kolon", "to_kolon"], "to_column"),
    **dict.fromkeys(["iliski_id", "iliski_adi"], "relationship_id"),
    **dict.fromkeys(["kardinalite"], "cardinality"),
    **dict.fromkeys(["aktif", "aktif_mi"], "is_active"),
    **dict.fromkeys(["metrik", "metrik_adi", "olcu_adi"], "metric_name"),
    **dict.fromkeys(["formul", "ifade", "expression", "sql_ifadesi"], "expression_sql"),
    **dict.fromkeys(["temel_tablo"], "base_table"),
    **dict.fromkeys(["bicim", "format"], "value_format"),
}


_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _norm_key(k: Any) -> str:
    """'SchemaName' / 'Schema Name' / 'schema-name' → 'schema_name'; Türkçe ve yaygın karşılıklar standart ada çevrilir."""
    raw = _CAMEL.sub("_", str(k or "").strip())
    key = re.sub(r"[\s\-./]+", "_", raw.translate(_FOLD).lower())
    key = re.sub(r"_+", "_", key).strip("_")
    return ALIASES.get(key, key)


def _norm_value(key: str, v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip()
        if v == "":
            return None
    if key in _BOOL_FIELDS:
        if v is None:
            return None if key == "is_active" else False
        return v if isinstance(v, bool) else str(v).strip().casefold() in _TRUE
    if key in _INT_FIELDS and v is not None:
        try:
            return int(float(str(v).replace(".", "").replace(",", "."))) if isinstance(v, str) else int(v)
        except (TypeError, ValueError):
            return None
    return v


# ------------------------------------------------------------------ okuyucular
class Reader(Protocol):
    kind: str

    def columns(self, names: list[str]) -> dict[str, list[str]]: ...       # küçük harf ad → kolon başlıkları (ham)
    def read(self, name: str) -> list[dict[str, Any]]: ...                 # satırlar: ham başlık → değer
    def list_tables(self) -> dict[str, list[str]]: ...                     # görünen ad → kolonlar


_SQL_NAME = re.compile(r"^[A-Za-z_][\w$#@]*\.[A-Za-z_][\w$#@]*$")
_MY_NAME = re.compile(r"^[\w$]+(\.[\w$]+)?$")


def _rows(columns: list[str], raw: list[list[Any]]) -> list[dict[str, Any]]:
    return [dict(zip(columns, r)) for r in raw]


class SqlServerReader:
    kind = "sqlserver"

    def __init__(self, con):
        self.con = con

    @staticmethod
    def valid(name: str) -> bool:
        return bool(_SQL_NAME.match(name or ""))

    def columns(self, names: list[str]) -> dict[str, list[str]]:
        wanted = [n for n in names if self.valid(n)]
        if not wanted:
            return {}
        cond = " OR ".join(f"(TABLE_SCHEMA = '{n.split('.', 1)[0]}' AND TABLE_NAME = '{n.split('.', 1)[1]}')" for n in wanted)
        r = self.con.execute(f"SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE {cond} "
                             "ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION", 50_000)
        out: dict[str, list[str]] = {}
        for schema, table, col in r.rows:
            out.setdefault(f"{schema}.{table}".lower(), []).append(str(col))
        return out

    def read(self, name: str) -> list[dict[str, Any]]:
        schema, table = name.split(".", 1)
        r = self.con.execute(f"SELECT * FROM [{schema}].[{table}]", 200_000)
        return _rows(r.columns, r.rows)

    def list_tables(self) -> dict[str, list[str]]:
        r = self.con.execute(
            "SELECT c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS c "
            "JOIN INFORMATION_SCHEMA.TABLES t ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME "
            "WHERE c.TABLE_SCHEMA NOT IN ('sys', 'INFORMATION_SCHEMA') ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION", 50_000)
        out: dict[str, list[str]] = {}
        for schema, table, col in r.rows:
            out.setdefault(f"{schema}.{table}", []).append(str(col))
        return out


class MySQLReader:
    """MySQL / MariaDB (pymysql). Tablo adı: 'tablo' (seçili veritabanında) ya da 'veritabanı.tablo'."""
    kind = "mysql"

    def __init__(self, host: str, port: int, user: str, password: str, database: str, ssl: bool = False, timeout: int = 15):
        import pymysql

        self.database = database
        self.conn = pymysql.connect(host=host, port=int(port or 3306), user=user, password=password, database=database or None,
                                    charset="utf8mb4", connect_timeout=timeout, read_timeout=60, autocommit=True,
                                    ssl={"ssl": {}} if ssl else None)

    @staticmethod
    def valid(name: str) -> bool:
        return bool(_MY_NAME.match(name or ""))

    def _split(self, name: str) -> tuple[str, str]:
        db, _, t = name.rpartition(".")
        return (db or self.database), t

    def _query(self, sql: str, args: tuple = ()) -> tuple[list[str], list[list[Any]]]:
        with self.conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute(sql, args)
            cols = [d[0] for d in cur.description or []]
            return cols, [list(r) for r in cur.fetchall()]

    def probe(self) -> dict[str, Any]:
        _, rows = self._query("SELECT VERSION(), DATABASE(), CURRENT_USER()")
        ver, db, user = rows[0]
        return {"server_name": self.conn.host, "database": db, "version": f"MySQL {ver}", "login": user, "driver": "PyMySQL"}

    def databases(self) -> list[str]:
        _, rows = self._query("SHOW DATABASES")
        return [r[0] for r in rows if r[0] not in ("information_schema", "mysql", "performance_schema", "sys")]

    def columns(self, names: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for n in names:
            if not self.valid(n):
                continue
            db, t = self._split(n)
            _, rows = self._query("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                                  "ORDER BY ORDINAL_POSITION", (db, t))
            if rows:
                out[n.lower()] = [str(r[0]) for r in rows]
        return out

    def read(self, name: str) -> list[dict[str, Any]]:
        db, t = self._split(name)
        cols, rows = self._query(f"SELECT * FROM `{db}`.`{t}`")
        return _rows(cols, rows)

    def list_tables(self) -> dict[str, list[str]]:
        _, rows = self._query("SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s "
                              "ORDER BY TABLE_NAME, ORDINAL_POSITION", (self.database,))
        out: dict[str, list[str]] = {}
        for t, c in rows:
            out.setdefault(t, []).append(str(c))
        return out


class ExcelReader:
    """Excel çalışma kitabı (.xlsx / .xlsm): her sayfa bir tablo; ilk dolu satır başlıktır."""
    kind = "excel"

    def __init__(self, path: str | Path):
        import openpyxl

        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"Excel dosyası bulunamadı: {self.path}")
        wb = openpyxl.load_workbook(self.path, read_only=True, data_only=True)
        self._sheets: dict[str, tuple[list[str], list[list[Any]]]] = {}
        for ws in wb.worksheets:
            it = ws.iter_rows(values_only=True)
            header: list[str] = []
            for row in it:
                if any(v not in (None, "") for v in row):
                    header = [str(v).strip() if v is not None else "" for v in row]
                    break
            data = [list(r) for r in it if any(v not in (None, "") for v in r)]
            self._sheets[ws.title] = (header, data)
        wb.close()

    @staticmethod
    def valid(name: str) -> bool:
        return bool(name and name.strip())

    def _find(self, name: str) -> str | None:
        if name in self._sheets:
            return name
        key = name.strip().casefold()
        return next((s for s in self._sheets if s.strip().casefold() == key), None)

    def probe(self) -> dict[str, Any]:
        return {"server_name": self.path.name, "database": f"{len(self._sheets)} sayfa", "version": "Excel", "driver": "openpyxl"}

    def columns(self, names: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for n in names:
            s = self._find(n)
            if s is not None:
                out[n.lower()] = [c for c in self._sheets[s][0] if c]
        return out

    def read(self, name: str) -> list[dict[str, Any]]:
        header, data = self._sheets[self._find(name)]  # type: ignore[index]
        return [{h: v for h, v in zip(header, r) if h} for r in data]

    def list_tables(self) -> dict[str, list[str]]:
        return {s: [c for c in h if c] for s, (h, _) in self._sheets.items()}


# ------------------------------------------------------------------ toplama
# tablo / kolon satırlarında şema ayrı kolonda olabilir (ör. SchemaName + view_name); zorunlu değil, uyarı üretmez
_EXTRA = {"tables": ["schema_name"], "columns": ["schema_name"]}
# içerikten tanıma: değerleri veritabanı kataloğuyla karşılaştırılan alanlar
_CONTENT_KIND = {"table_name": "object", "from_table": "object", "to_table": "object", "base_table": "object",
                 "column_name": "column", "from_column": "column", "to_column": "column", "schema_name": "schema"}
_SAMPLE = 500


@dataclass
class Known:
    """Veritabanı kataloğundaki adlar (küçük harf) — başlığı tanınmayan sütunları içerikten bulmak için."""
    objects: set[str] = field(default_factory=set)    # şema.nesne
    names: set[str] = field(default_factory=set)      # nesne
    columns: set[str] = field(default_factory=set)
    schemas: set[str] = field(default_factory=set)

    @classmethod
    def from_catalog(cls, objects: list[tuple[str, str]], columns: list[str]) -> "Known":
        k = cls()
        for sch, obj in objects:
            k.objects.add(f"{sch}.{obj}".lower())
            k.names.add(str(obj).lower())
            k.schemas.add(str(sch).lower())
        k.columns = {str(c).lower() for c in columns}
        return k

    def matches(self, kind: str, value: Any) -> bool:
        if value in (None, ""):
            return False
        if kind == "object":
            from app.dictionary.names import canon

            v = canon(value).lower()
            return v in self.objects or v.rsplit(".", 1)[-1] in self.names
        v = str(value).strip().strip("[]").lower()
        return v in (self.columns if kind == "column" else self.schemas)


@dataclass
class Collected:
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    explicit: set[str] = field(default_factory=set)   # kaynak seçilmiş roller
    derived_tables: int = 0
    # kaynak (tablo / sayfa) → {"role", "headers", "mapping": alan → başlık | None, "how": alan → header | content | manual}
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def counts(self) -> dict[str, int]:
        return {r: len(v) for r, v in self.rows.items()}

    def learned_mappings(self) -> dict[str, dict[str, str]]:
        """Kaydedilecek eşleme: elle seçilenler ve içerikten bulunanlar (başlıktan tanınanlar her seferinde yeniden bulunur)."""
        out: dict[str, dict[str, str]] = {}
        for name, info in self.sources.items():
            m = {f: (info["mapping"].get(f) or "") for f, how in info["how"].items() if how in ("manual", "content")}
            if m:
                out[name] = m
        return out


def _header_map(headers: list[str], fields: list[str], manual: dict[str, str] | None) -> tuple[dict[str, str | None], dict[str, str]]:
    """alan → başlık. Önce elle seçilen ("" = kullanma), sonra başlık adı (Türkçe / İngilizce / CamelCase karşılıklar)."""
    by_norm: dict[str, str] = {}
    for h in headers:
        by_norm.setdefault(_norm_key(h), h)
    folded = {str(h).strip().casefold(): h for h in headers}
    mapping: dict[str, str | None] = {}
    how: dict[str, str] = {}
    for f in fields:
        if manual and f in manual:
            want = manual[f]
            if want == "":
                mapping[f], how[f] = None, "manual"
                continue
            h = want if want in headers else folded.get(str(want).strip().casefold())
            if h is not None:
                mapping[f], how[f] = h, "manual"
                continue
        mapping[f] = by_norm.get(f)
        if mapping[f] is not None:
            how[f] = "header"
    return mapping, how


def _detect(rows: list[dict[str, Any]], headers: list[str], taken: set[str], kind: str, known: Known) -> str | None:
    """Değerleri katalogdaki adlarla en çok örtüşen (en az %50; şema için %80) ve henüz kullanılmayan sütun."""
    sample = rows[:_SAMPLE]
    best, score = None, 0.0
    for h in headers:
        if h in taken:
            continue
        vals = [r.get(h) for r in sample if r.get(h) not in (None, "")]
        if not vals:
            continue
        hit = sum(1 for v in vals if known.matches(kind, v)) / len(vals)
        if hit > score:
            best, score = h, hit
    return best if score >= (0.8 if kind == "schema" else 0.5) else None


def collect(reader: Reader, sources: dict[str, list[str]], mappings: dict[str, dict[str, str]] | None = None,
            known: Known | None = None) -> Collected:
    """Seçilen tablo / sayfaları okur. Sütunlar alanlara şu sırayla eşlenir: elle seçilen (mappings) → başlık adı →
    (zorunlu alanlar ve şema için) içerik: değerleri veritabanı kataloğundaki tablo / kolon / şema adlarıyla örtüşen sütun."""
    out = Collected()
    names = sorted({n for lst in sources.values() for n in lst or []})
    bad = [n for n in names if not reader.valid(n)]  # type: ignore[attr-defined]
    if bad:
        out.errors.append(f"Geçersiz ad: {', '.join(bad)}" + (" (şema.tablo biçiminde olmalı)" if reader.kind == "sqlserver" else ""))
    cols = reader.columns([n for n in names if n not in bad])
    what, its, in_it = ("sayfa", "sayfası", "sayfasında") if reader.kind == "excel" else ("tablo", "tablosu", "tablosunda")
    manual_all = {str(k).casefold(): v for k, v in (mappings or {}).items()}
    for role, spec in ROLES.items():
        rows: list[dict[str, Any]] = []
        used = False
        fields = spec["required"] + spec["optional"] + _EXTRA.get(role, [])
        for name in sources.get(role) or []:
            if name in bad:
                continue
            headers = cols.get(name.lower())
            if headers is None:
                out.errors.append(f"{spec['label']}: '{name}' {its} bulunamadı" + ("" if reader.kind == "excel" else " ya da okuma yetkisi yok") + ".")
                continue
            headers = [str(h) for h in headers if h not in (None, "")]
            raw = reader.read(name)
            mapping, how = _header_map(headers, fields, manual_all.get(name.casefold()))
            if known is not None:   # başlığı tanınmayan zorunlu alanlar / şema: içerikten
                for f in spec["required"] + _EXTRA.get(role, []):
                    if mapping.get(f) is None and how.get(f) != "manual" and f in _CONTENT_KIND:
                        h = _detect(raw, headers, {v for v in mapping.values() if v}, _CONTENT_KIND[f], known)
                        if h is not None:
                            mapping[f], how[f] = h, "content"
                            out.warnings.append(f"{spec['label']}: '{name}' {in_it} {f} başlıktan tanınmadı; "
                                                f"içeriğine göre '{h}' sütunu kullanıldı.")
            out.sources[name] = {"role": role, "headers": headers, "mapping": mapping, "how": how}
            missing = [f for f in spec["required"] if not mapping.get(f)]
            if missing:
                out.errors.append(f"{spec['label']}: '{name}' {in_it} zorunlu kolon(lar) yok: {', '.join(missing)}. "
                                  "Başlık eşleme'den hangi sütun olduğunu seçin.")
                continue
            absent = [c for c in spec["optional"] if not mapping.get(c)]
            if absent:
                out.warnings.append(f"{spec['label']}: '{name}' {in_it} olmayan kolonlar boş sayıldı: {', '.join(absent)}.")
            used = True
            skipped = 0
            for r in raw:
                row = {k: _norm_value(k, r.get(mapping[k]) if mapping.get(k) else None) for k in spec["required"] + spec["optional"]}
                if any(row[k] in (None, "") for k in spec["required"]):
                    skipped += 1
                    continue
                sch = _norm_value("schema_name", r.get(mapping["schema_name"])) if mapping.get("schema_name") else None
                if sch and "." not in str(row["table_name"]).strip("[]"):
                    row["table_name"] = f"{sch}.{row['table_name']}"
                rows.append(row)
            if skipped:
                out.warnings.append(f"{spec['label']}: '{name}' içinde zorunlu alanı boş {skipped} satır atlandı.")
        if used:
            out.rows[role] = rows
            out.explicit.add(role)
        elif spec["must"]:
            out.errors.append(f"{spec['label']} için en az bir geçerli {what} seçilmeli.")
    _derive_tables(out)
    return out


def _derive_tables(out: Collected) -> None:
    """Kolon satırlarında olup Tablolar'da olmayan tabloları ekler (tek tablolu sözlük). Tablo düzeyi bilgiler
    kolon satırlarındaki table_* / subject_area / grain / row_count / table_type alanlarından (ilk dolu değer) alınır."""
    cols = out.rows.get("columns") or []
    if not cols:
        return
    have = {str(t["table_name"]).lower() for t in out.rows.get("tables") or []}
    derived: dict[str, dict[str, Any]] = {}
    src = {"business_name": "table_business_name", "description": "table_description", "subject_area": "subject_area",
           "grain": "grain", "row_count": "row_count", "table_type": "table_type"}
    for c in cols:
        key = str(c["table_name"]).lower()
        if key in have:
            continue
        t = derived.setdefault(key, {"table_name": c["table_name"], **{k: None for k in ROLES["tables"]["optional"]}})
        for k, from_k in src.items():
            if t[k] is None and c.get(from_k) not in (None, ""):
                t[k] = c[from_k]
    if derived:
        out.rows["tables"] = (out.rows.get("tables") or []) + list(derived.values())
        out.derived_tables = len(derived)
        if "tables" not in out.explicit:
            out.warnings.append(f"Tablolar kolon sözlüğünden çıkarıldı ({len(derived)} tablo).")
        else:
            out.warnings.append(f"Yalnız kolon sözlüğünde olan {len(derived)} tablo da eklendi.")
