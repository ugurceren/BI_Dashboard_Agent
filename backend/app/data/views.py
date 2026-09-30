"""Dataset → onaylı view (kalıcılaştırma).

Akış:
  1. view_script(): dataset SQL'inden (doğrulayıcının düzelttiği hâliyle) inceleme için CREATE OR ALTER VIEW scripti üretir.
     Agent'ın veritabanında yazma yetkisi yoktur; scripti DBA / kullanıcı çalıştırır.
  2. Kullanıcı scripti çalıştırınca use_view(): view'ın gerçekten var olduğunu ve kolonlarını doğrular, view'ı yerel
     kayda (views.json) yazar, veri sözlüğüne ekler ve dataset'i `SELECT ... FROM rpt.v_...`'ye çevirir.
  3. Sözlükte view'ın kolonları kaynak model kolonlarıyla (lineage) eşlenir; boyut tablolarına ilişki kurulabiliyorsa
     (boyut kolonu tekilse) Power BI tarzı filtreler view'da da çalışır.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

VIEW_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,110}$")


def safe_view_name(dataset_id: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", dataset_id.translate(str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")))
    return f"v_{base}".strip("_")[:110]


class ViewRegistry:
    """Kalıcılaştırılmış view'ların yerel kaydı (kurum sözlüğüne yazmıyoruz; o tablolar kurumun)."""

    def __init__(self, path: Path):
        self.path = path

    def all(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def upsert(self, entry: dict[str, Any]) -> None:
        items = [e for e in self.all() if e["name"].lower() != entry["name"].lower()] + [entry]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


@dataclass
class ViewScript:
    name: str            # şema.view
    script: str
    body_sql: str
    errors: list[str]


def _strip_order_by(tree: exp.Expression) -> exp.Expression:
    """View'da TOP olmadan ORDER BY yasak (SQL Server). Sıralama dashboard'da yapılır."""
    if tree.args.get("order") is not None and tree.args.get("limit") is None:
        tree.set("order", None)
    return tree


def build_view_script(*, schema: str, name: str, dataset_id: str, description: str, validated_sql: str,
                      columns: list[str], session_title: str) -> ViewScript:
    errors: list[str] = []
    if not VIEW_NAME.match(schema) or not VIEW_NAME.match(name):
        errors.append("View ve şema adı yalnız harf, rakam ve _ içerebilir (harfle başlamalı).")
    if len(set(c.lower() for c in columns)) != len(columns):
        errors.append("Dataset kolon adları benzersiz olmalı; view oluşturulamaz.")
    unnamed = [c for c in columns if not re.match(r"^[^\s()\[\]]+$", c)]
    if unnamed:
        errors.append(f"Adı olmayan (takma adsız) kolonlar var: {unnamed}. SQL'de AS ile ad verin.")
    tree = _strip_order_by(sqlglot.parse_one(validated_sql, read="tsql"))
    body = tree.sql(dialect="tsql", pretty=True)
    full = f"{schema}.{name}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    script = f"""-- ============================================================================
-- BI Lens — onaylı view önerisi
-- View     : {full}
-- Dataset  : {dataset_id}  (rapor: {session_title})
-- Açıklama : {description or '-'}
-- Üretildi : {now}
-- Bu script agent tarafından ÜRETİLDİ ama ÇALIŞTIRILMADI. İnceleyip onaylayın, sonra çalıştırın.
-- Agent'ın veritabanında yazma yetkisi yoktur.
-- ============================================================================
IF SCHEMA_ID(N'{schema}') IS NULL EXEC(N'CREATE SCHEMA [{schema}]');
GO

CREATE OR ALTER VIEW [{schema}].[{name}] AS
{body};
GO

-- İsteğe bağlı: rapor okuyucu rolüne yalnızca bu view'ı açın
-- GRANT SELECT ON [{schema}].[{name}] TO [rapor_okuyucu];
"""
    return ViewScript(full, script, body, errors)


def view_columns(connector, schema: str, name: str) -> list[str] | None:
    """View varsa kolon adlarını döndürür, yoksa None. (Adlar regex ile doğrulandığı için metne gömülebilir.)"""
    if not VIEW_NAME.match(schema) or not VIEW_NAME.match(name):
        return None
    r = connector.execute(
        "SELECT c.name FROM sys.views v JOIN sys.schemas s ON s.schema_id = v.schema_id "
        "JOIN sys.columns c ON c.object_id = v.object_id "
        f"WHERE s.name = N'{schema}' AND v.name = N'{name}' ORDER BY c.column_id", 500)
    return [row[0] for row in r.rows] or None


def select_from_view(schema: str, name: str, columns: list[str]) -> str:
    cols = ", ".join(f"[{c}]" for c in columns)
    return f"SELECT {cols} FROM [{schema}].[{name}]"
