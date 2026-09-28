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
                     "funnel", "gauge", "treemap", "combo", "text"]

_ID = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


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

    @field_validator("y", "columns", mode="before")
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
    aggregate: Literal["sum", "avg", "first", "last", "min", "max"] | None = None
    deltaField: str | None = None
    deltaLabel: str | None = None
    sparklineDatasetId: str | None = None
    sparklineField: str | None = None
    target: float | None = None
    text: str | None = None
    color: str | None = None


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
        bad = [c for c in v if not _HEX.match(c)]
        if bad:
            raise ValueError(f"palette hex renk olmalı: {bad}")
        if len(v) < 3:
            raise ValueError("palette en az 3 renk içermeli")
        return v

    @field_validator("background", "surface", "text", "mutedText", "accent", "border")
    @classmethod
    def _hex(cls, v: str) -> str:
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


# --------------------------------------------------------------------------- anlamsal kontrol
_NEEDS: dict[str, list[str]] = {
    "kpi": ["value"], "line": ["x", "y"], "area": ["x", "y"], "bar": ["x", "y"], "combo": ["x", "y"],
    "scatter": ["x", "y"], "pie": ["category", "value"], "donut": ["category", "value"],
    "funnel": ["category", "value"], "treemap": ["category", "value"], "gauge": ["value"],
    "heatmap": ["x", "category", "value"], "table": [], "text": [],
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
        if v.options.deltaField:
            refs.append(v.options.deltaField)
        missing = [r for r in refs if avail and r not in avail]
        if missing:
            errors.append(f"{where}: '{v.datasetId}' dataset'inde olmayan alan(lar): {missing}. Mevcut: {sorted(avail)}")
        if enc.series and enc.y and len(enc.y) > 1:
            errors.append(f"{where}: series kullanılırken y tek alan olmalı.")
        if v.options.sparklineDatasetId:
            sd = ds.get(v.options.sparklineDatasetId)
            if not sd:
                errors.append(f"{where}: sparklineDatasetId '{v.options.sparklineDatasetId}' yok.")
            elif v.options.sparklineField and fields_of(sd) and v.options.sparklineField not in fields_of(sd):
                errors.append(f"{where}: sparklineField '{v.options.sparklineField}' dataset'te yok.")

    # yerleşim çakışması
    cells: dict[tuple[int, int], str] = {}
    for v in spec.visuals:
        p = v.position
        for yy in range(p.y, p.y + p.h):
            for xx in range(p.x, p.x + p.w):
                other = cells.get((xx, yy))
                if other and other != v.id:
                    errors.append(f"Yerleşim çakışması: '{v.id}' ile '{other}' üst üste biniyor (x={xx}, y={yy}).")
                    break
                cells[(xx, yy)] = v.id
            else:
                continue
            break

    for f in spec.filters:
        if not (f.table and f.column) and not any(f.field in fields_of(d) for d in spec.datasets):
            errors.append(f"Filtre '{f.id}': '{f.field}' alanı hiçbir dataset'te yok; table+column ile model kolonunu verin.")
    return errors
