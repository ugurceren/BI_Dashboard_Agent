"""Report Spec: agent'ın ürettiği dashboard tanımı (docs/CONTRACT.md ile birebir).

Model çıktısındaki küçük hatalar (y'nin string gelmesi, eksik tema alanları) burada tolere edilir;
anlamsal hatalar (olmayan dataset/alan, çakışan pozisyon) ise modele geri bildirilir.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FieldType = Literal["number", "string", "date"]
ValueFormat = Literal["number", "currency", "percent", "compact"]
VisualType = Literal["kpi", "line", "area", "bar", "pie", "donut", "table", "scatter", "heatmap",
                     "funnel", "gauge", "treemap", "combo", "text", "matrix"]

_ID = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

# Renk adı → hex: yerel modeller sık sık "lacivert", "koyu mavi" gibi adlar yazıyor. Tarayıcı Türkçe adları tanımaz
# (kart sessizce eski renkte kalır); bilinen adlar hex'e çevrilir, bilinmeyen ad reddedilir (model hex ile yeniden dener).
COLOR_NAMES = {
    "lacivert": "#1e3a8a", "koyu lacivert": "#0f172a", "koyu mavi": "#1e40af", "mavi": "#2563eb", "açık mavi": "#60a5fa",
    "gök mavisi": "#38bdf8", "turkuaz": "#14b8a6", "camgöbeği": "#06b6d4", "yeşil": "#16a34a", "koyu yeşil": "#166534",
    "açık yeşil": "#4ade80", "zümrüt": "#059669", "kırmızı": "#dc2626", "koyu kırmızı": "#991b1b", "bordo": "#7f1d1d",
    "turuncu": "#ea580c", "sarı": "#eab308", "altın": "#ca8a04", "mor": "#7c3aed", "eflatun": "#a855f7", "pembe": "#db2777",
    "gri": "#64748b", "açık gri": "#e2e8f0", "koyu gri": "#334155", "füme": "#374151", "siyah": "#000000", "beyaz": "#ffffff",
    "kahverengi": "#92400e", "bej": "#f5f5dc", "krem": "#fef3c7",
}
# CSS adları (eski spec'lerde olabilir; tarayıcı tanır)
_CSS_NAMES = set("""aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown burlywood
cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgreen
darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen darkslateblue darkslategray
darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia
gainsboro ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki lavender
lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink
lightsalmon lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen linen magenta
maroon mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen mediumturquoise
mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab orange orangered orchid
palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum powderblue purple rebeccapurple red
rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue slategray slategrey snow
springgreen steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen""".split())


def to_hex_color(v: str) -> str:
    """'#2563eb' olduğu gibi; 'lacivert' / 'Koyu Mavi' → hex; CSS adı ('navy') olduğu gibi; diğerleri ValueError."""
    t = str(v).strip()
    if _HEX.match(t):
        return t
    key = " ".join(t.replace("İ", "i").replace("I", "ı").lower().replace("_", " ").replace("-", " ").split())
    if key in COLOR_NAMES:
        return COLOR_NAMES[key]
    if key.replace(" ", "") in _CSS_NAMES:
        return key.replace(" ", "")
    raise ValueError(f"hex renk olmalı (ör. #2563eb) ya da bilinen bir renk adı ({', '.join(list(COLOR_NAMES)[:8])}…): {v}")


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class DatasetField(_Base):
    name: str
    label: str | None = None
    type: FieldType = "string"
    format: ValueFormat | None = None


class Dataset(_Base):
    id: str
    description: str = ""
    sql: str
    fields: list[DatasetField] = Field(default_factory=list)
    view: str | None = None           # kalıcılaştırıldıysa kaynak view (ör. rpt.v_bolge_satis)
    original_sql: str | None = None   # view'a geçmeden önceki SQL

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("dataset id snake_case olmalı (harf ile başlar, harf/rakam/_)")
        return v


class Encoding(_Base):
    x: str | None = None
    y: list[str] | None = None
    series: str | None = None
    category: str | None = None
    value: str | None = None
    columns: list[str] | None = None
    # matrix (pivot tablo): satır boyutları (1-2 seviye), sütun boyutu (değerleri veriden sütun olur), ölçüler
    rows: list[str] | None = None
    columnDim: str | None = None
    values: list[str] | None = None

    @field_validator("y", "columns", "rows", "values", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [v]
        return v


class VisualOptions(_Base):
    stacked: bool | None = None
    horizontal: bool | None = None
    smooth: bool | None = None
    showLabels: bool | None = None
    showLegend: bool | None = None
    format: ValueFormat | None = None
    currency: str | None = None
    decimals: int | None = None
    sort: Literal["asc", "desc"] | None = None
    limit: int | None = None
    aggregate: Literal["sum", "avg", "count", "first", "last", "min", "max"] | None = None
    deltaField: str | None = None
    compareField: str | None = None  # önceki dönem değeri; deltaField yoksa değişim buradan hesaplanır
    deltaLabel: str | None = None
    sparklineDatasetId: str | None = None
    sparklineField: str | None = None
    target: float | None = None
    text: str | None = None
    color: str | None = None                # seri rengi; kpi'da değer + vurgu şeridi + sparkline rengi
    # KPI kart stili (yalnız kpi): kart zemini, yazı rengi (yoksa zemine göre otomatik), değer boyutu, sol renkli şerit
    background: str | None = None
    textColor: str | None = None
    valueSize: Literal["sm", "md", "lg", "xl"] | None = None
    accentBar: bool | None = None
    ignoreFilters: bool | None = None  # true: bu görsel filtrelerden etkilenmez (yalnız kullanıcı açıkça isterse)
    # matrix: satır / sütun genel toplamı, grup ara toplamları (varsayılan hepsi açık), sütun sınırı, koşullu hücre rengi
    rowTotals: bool | None = None
    columnTotals: bool | None = None
    subtotals: bool | None = None
    maxColumns: int | None = Field(default=None, ge=1, le=60)
    conditionalColor: bool | None = None


    @field_validator("color", "background", "textColor")
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        return None if v is None else to_hex_color(v)


class Position(_Base):
    x: int = Field(ge=0, le=11)
    y: int = Field(ge=0)
    w: int = Field(ge=1, le=12)
    h: int = Field(ge=1, le=12)

    @model_validator(mode="after")
    def _fits(self) -> "Position":
        if self.x + self.w > 12:
            raise ValueError(f"x + w 12'yi aşamaz (x={self.x}, w={self.w})")
        return self


class Visual(_Base):
    id: str
    type: VisualType
    title: str = ""
    subtitle: str | None = None
    datasetId: str | None = None
    encoding: Encoding = Field(default_factory=Encoding)
    options: VisualOptions = Field(default_factory=VisualOptions)
    position: Position
    page: str | None = None           # sayfa id'si (Power BI sayfası gibi); yoksa ilk sayfa


class Page(_Base):
    """Rapor sayfası: görseller visual.page ile bağlanır; filtreler tüm sayfalarda geçerlidir."""
    id: str
    title: str

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("sayfa id snake_case olmalı (harf ile başlar, harf/rakam/_)")
        return v


class Filter(_Base):
    """Model tabanlı filtre: table + column (sözlükteki bir boyut kolonu) verilirse tüm görsellere ilişkiler
    üzerinden yayılır (Power BI dilimleyicisi gibi). Yalnız field verilirse dataset kolonunun kökeni bulunur."""

    id: str
    label: str
    table: str | None = None      # ör. dbo.DimSalesTerritory
    column: str | None = None     # ör. SalesTerritoryGroup
    field: str | None = None      # eski/yedek: dataset kolon adı
    type: Literal["select", "multiselect"] = "multiselect"

    @model_validator(mode="after")
    def _target(self) -> "Filter":
        if not ((self.table and self.column) or self.field):
            raise ValueError("filtre için table+column (model kolonu) ya da field gerekli")
        return self


class Theme(_Base):
    mode: Literal["light", "dark"] = "light"
    palette: list[str] = Field(default_factory=lambda: ["#2563eb", "#0ea5e9", "#14b8a6", "#f59e0b", "#ef4444", "#8b5cf6", "#64748b"])
    background: str = "#f4f6fb"
    surface: str = "#ffffff"
    text: str = "#0f172a"
    mutedText: str = "#64748b"
    accent: str = "#2563eb"
    border: str = "#e2e8f0"
    fontFamily: str = "Inter, 'Segoe UI', system-ui, sans-serif"
    radius: int = Field(default=12, ge=0, le=32)
    cardStyle: Literal["flat", "outlined", "elevated"] = "elevated"
    density: Literal["compact", "comfortable"] = "comfortable"
    headerStyle: Literal["plain", "banner"] = "plain"

    @field_validator("palette")
    @classmethod
    def _palette(cls, v: list[str]) -> list[str]:
        try:
            v = [to_hex_color(c) for c in v]
        except ValueError:
            pass
        bad = [c for c in v if not _HEX.match(c)]
        if bad:
            raise ValueError(f"palette hex renk olmalı: {bad}")
        if len(v) < 3:
            raise ValueError("palette en az 3 renk içermeli")
        return v

    @field_validator("background", "surface", "text", "mutedText", "accent", "border")
    @classmethod
    def _hex(cls, v: str) -> str:
        v = to_hex_color(v)
        if not _HEX.match(v):
            raise ValueError(f"hex renk olmalı: {v}")
        return v


class Layout(_Base):
    columns: Literal[12] = 12
    rowHeight: int = Field(default=90, ge=40, le=200)


class ReportSpec(_Base):
    version: Literal[1] = 1
    title: str
    subtitle: str | None = None
    theme: Theme = Field(default_factory=Theme)
    layout: Layout = Field(default_factory=Layout)
    filters: list[Filter] = Field(default_factory=list)
    datasets: list[Dataset] = Field(default_factory=list)
    visuals: list[Visual] = Field(default_factory=list)
    pages: list[Page] = Field(default_factory=list)   # boş: tek sayfa; varsa sırasıyla sekmeler

    def page_of(self, v: "Visual") -> str | None:
        """Görselin sayfası (page yoksa ya da tanımsızsa ilk sayfa); sayfa yoksa None."""
        if not self.pages:
            return None
        ids = {p.id for p in self.pages}
        return v.page if v.page in ids else self.pages[0].id


# --------------------------------------------------------------------------- anlamsal kontrol
_NEEDS: dict[str, list[str]] = {
    "kpi": ["value"], "line": ["x", "y"], "area": ["x", "y"], "bar": ["x", "y"], "combo": ["x", "y"],
    "scatter": ["x", "y"], "pie": ["category", "value"], "donut": ["category", "value"],
    "funnel": ["category", "value"], "treemap": ["category", "value"], "gauge": ["value"],
    "heatmap": ["x", "category", "value"], "matrix": ["rows", "values"], "table": [], "text": [],
}


def semantic_errors(spec: ReportSpec) -> list[str]:
    """Visual'ların dataset/alan referanslarını ve yerleşimi kontrol eder. Boş liste = geçerli."""
    errors: list[str] = []
    ds = {d.id: d for d in spec.datasets}
    if len(ds) != len(spec.datasets):
        errors.append("Dataset id'leri benzersiz olmalı.")
    ids = [v.id for v in spec.visuals]
    if len(set(ids)) != len(ids):
        errors.append("Visual id'leri benzersiz olmalı.")
    page_ids = [p.id for p in spec.pages]
    if len(set(page_ids)) != len(page_ids):
        errors.append("Sayfa id'leri benzersiz olmalı.")
    for v in spec.visuals:
        if v.page and spec.pages and v.page not in page_ids:
            errors.append(f"visual '{v.id}': sayfa '{v.page}' yok. Mevcut sayfalar: {page_ids}")
        elif v.page and not spec.pages:
            errors.append(f"visual '{v.id}': sayfa '{v.page}' tanımlı değil (önce add_page ile sayfa ekleyin).")

    def fields_of(d: Dataset) -> set[str]:
        return {f.name for f in d.fields}

    for v in spec.visuals:
        where = f"visual '{v.id}' ({v.type})"
        if v.type == "text":
            if not v.options.text and not v.title:
                errors.append(f"{where}: options.text boş.")
            continue
        if not v.datasetId or v.datasetId not in ds:
            errors.append(f"{where}: datasetId '{v.datasetId}' yok. Mevcut: {sorted(ds)}")
            continue
        avail = fields_of(ds[v.datasetId])
        enc = v.encoding
        for need in _NEEDS.get(v.type, []):
            if not getattr(enc, need):
                errors.append(f"{where}: encoding.{need} gerekli.")
        refs: list[str] = []
        for k in ("x", "series", "category", "value"):
            if getattr(enc, k):
                refs.append(getattr(enc, k))
        refs += enc.y or []
        refs += enc.columns or []
        refs += (enc.rows or []) + (enc.values or []) + ([enc.columnDim] if enc.columnDim else [])
        if v.options.deltaField:
            refs.append(v.options.deltaField)
        if v.options.compareField:
            refs.append(v.options.compareField)
        missing = [r for r in refs if avail and r not in avail]
        if missing:
            # alan başka bir dataset'te varsa söyle (ör. subcategory → top_products): model yanlış dataset'te ısrar ediyordu
            elsewhere = sorted({d.id for d in spec.datasets if d.id != v.datasetId and set(missing) <= fields_of(d)})
            hint = f" Bu alan(lar) şu dataset'lerde var: {elsewhere} (datasetId'yi değiştir)." if elsewhere else ""
            errors.append(f"{where}: '{v.datasetId}' dataset'inde olmayan alan(lar): {missing}. Mevcut: {sorted(avail)}.{hint}")
        if v.type == "matrix" and enc.rows and len(enc.rows) > 2:
            errors.append(f"{where}: matris en çok 2 satır seviyesi alır (encoding.rows), verilen: {enc.rows}.")
        if v.type == "matrix" and enc.columnDim and enc.columnDim in (enc.rows or []):
            errors.append(f"{where}: '{enc.columnDim}' hem satır (rows) hem sütun boyutu (columnDim) olamaz.")
        if enc.series and enc.y and len(enc.y) > 1:
            errors.append(f"{where}: series kullanılırken y tek alan olmalı.")
        if v.options.sparklineDatasetId:
            sd = ds.get(v.options.sparklineDatasetId)
            if not sd:
                errors.append(f"{where}: sparklineDatasetId '{v.options.sparklineDatasetId}' yok.")
            elif v.options.sparklineField and fields_of(sd) and v.options.sparklineField not in fields_of(sd):
                errors.append(f"{where}: sparklineField '{v.options.sparklineField}' dataset'te yok.")

    # yerleşim çakışması (her sayfanın kendi ızgarası)
    cells: dict[tuple[str | None, int, int], str] = {}
    for v in spec.visuals:
        p = v.position
        pg = spec.page_of(v)
        for yy in range(p.y, p.y + p.h):
            for xx in range(p.x, p.x + p.w):
                other = cells.get((pg, xx, yy))
                if other and other != v.id:
                    errors.append(f"Yerleşim çakışması: '{v.id}' ile '{other}' üst üste biniyor (x={xx}, y={yy}).")
                    break
                cells[(pg, xx, yy)] = v.id
            else:
                continue
            break

    for f in spec.filters:
        if not (f.table and f.column) and not any(f.field in fields_of(d) for d in spec.datasets):
            errors.append(f"Filtre '{f.id}': '{f.field}' alanı hiçbir dataset'te yok; table+column ile model kolonunu verin.")
    return errors
