"""SQL doğrulayıcı: modelin ürettiği SQL, veritabanına gitmeden önce buradan geçer.

Kurallar (hepsi modelden bağımsız, deterministik):
  * tek ifade, yalnızca sorgu (SELECT / WITH / UNION); DML, DDL, SELECT INTO, COPY, PRAGMA ... yasak
  * yalnızca sözlükte tanımlı, rolün izin verdiği şemalardaki tablolar
  * tablo fonksiyonları (read_csv, OPENROWSET ...) ve yasaklı fonksiyonlar engellenir
  * rol PII görmeye yetkili değilse sözlükte is_pii=true olan kolonlar ve PII içeren tablolarda SELECT * engellenir
  * JOIN'ler sözlükteki ilişkilerle eşlenir: eksik bileşik anahtar ve satır çoğalması (fan-out / chasm trap)
    reddedilir, sözlükte olmayan birleştirmeler uyarı olarak döner (join_guard.py)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from app.data.join_guard import JoinGuard
from app.dictionary.repository import DataDictionary


@dataclass
class RolePolicy:
    name: str
    allowed_schemas: list[str]
    denied_tables: list[str] = field(default_factory=list)
    allow_pii: bool = False
    max_rows: int = 5000


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    tables: list[str] = field(default_factory=list)
    sql: str = ""
    warnings: list[str] = field(default_factory=list)


_FORBIDDEN_NODES = tuple(
    getattr(exp, n)
    for n in ("Insert", "Update", "Delete", "Drop", "Create", "Alter", "AlterTable", "Merge", "Into",
              "Command", "Copy", "Pragma", "Set", "Use", "Transaction", "Commit", "Rollback", "Grant",
              "Attach", "Detach", "Install", "Load", "TruncateTable", "LoadData", "Execute")
    if hasattr(exp, n)
)


class SqlValidator:
    def __init__(self, dictionary: DataDictionary, dialect: str, denied_functions: list[str]):
        self.dictionary = dictionary
        self.dialect = dialect
        self.denied_functions = {f.lower() for f in denied_functions}
        self.join_guard = JoinGuard(dictionary)

    def validate(self, sql: str, policy: RolePolicy) -> ValidationResult:
        sql = (sql or "").strip().rstrip(";").strip()
        if not sql:
            return ValidationResult(False, ["SQL boş."])
        try:
            statements = [s for s in sqlglot.parse(sql, read=self.dialect) if s is not None]
        except sqlglot.errors.ParseError as e:
            return ValidationResult(False, [f"SQL ayrıştırılamadı: {str(e).splitlines()[0][:300]}"])
        if len(statements) != 1:
            return ValidationResult(False, ["Tek bir SQL ifadesi gönderin (';' ile birden çok ifade yasak)."])
        tree = statements[0]
        if not isinstance(tree, exp.Query):
            return ValidationResult(False, [f"Yalnızca SELECT sorgularına izin var ({type(tree).__name__} yasak)."])

        errors: list[str] = []
        for node in tree.walk():
            if isinstance(node, _FORBIDDEN_NODES):
                errors.append(f"İzin verilmeyen ifade: {type(node).__name__.upper()}.")
        if errors:
            return ValidationResult(False, sorted(set(errors)))

        # --- tablolar
        cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        alias_to_table: dict[str, str] = {}
        tables: set[str] = set()
        for t in tree.find_all(exp.Table):
            if not isinstance(t.this, exp.Identifier):
                errors.append(f"Tablo fonksiyonlarına izin yok: {t.this.sql(self.dialect)[:80]}")
                continue
            if not t.db and t.name.lower() in cte_names:
                continue
            if t.catalog:
                # BaskaDB.dbo.Tablo / linked server: izin kontrolünü atlatmasın
                errors.append(f"Veritabanı/sunucu adı kullanmayın: '{t.sql(self.dialect)}' yerine '{t.db}.{t.name}' yazın.")
                continue
            if not t.db:
                errors.append(f"Tabloyu şemasıyla yazın: '{t.name}' yerine ör. 'dbo.{t.name}'.")
                continue
            full = f"{t.db}.{t.name}".lower()
            schema = t.db.lower()
            if schema not in {s.lower() for s in policy.allowed_schemas}:
                errors.append(f"'{full}': '{schema}' şemasına erişim yetkiniz yok.")
                continue
            if full in {d.lower() for d in policy.denied_tables}:
                errors.append(f"'{full}' tablosuna erişim yetkiniz yok.")
                continue
            if not self.dictionary.has_table(full):
                errors.append(f"'{full}' veri sözlüğünde yok. Önce search_dictionary ile doğru tabloyu bulun.")
                continue
            tables.add(full)
            alias_to_table[t.alias_or_name.lower()] = full
            alias_to_table.setdefault(t.name.lower(), full)

        # --- değişkenler (@x, @@SERVERNAME ...): tek SELECT'te gerekmez, sistem bilgisi sızdırabilir
        for node in tree.find_all(*[getattr(exp, n) for n in ("Parameter", "SessionParameter", "Placeholder") if hasattr(exp, n)]):
            errors.append(f"Değişken / sistem değişkeni kullanılamaz: {node.sql(self.dialect)[:40]}")
            break

        # --- fonksiyonlar
        for f in tree.find_all(exp.Func):
            name = (f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower()
            if name in self.denied_functions:
                errors.append(f"'{name}' fonksiyonuna izin yok.")

        # --- kişisel veri (PII)
        if not policy.allow_pii and tables:
            pii = {t: self.dictionary.pii_columns(t) for t in tables}
            if any(pii.values()):
                # yalnızca projeksiyondaki * / t.* (COUNT(*) değil)
                stars = [e for sel in tree.find_all(exp.Select) for e in sel.expressions
                         if isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))]
                for star in stars:
                    qualifier = star.table.lower() if isinstance(star, exp.Column) and star.table else None
                    scope = [alias_to_table.get(qualifier)] if qualifier else list(tables)
                    hit = [t for t in scope if t and pii.get(t)]
                    if hit:
                        errors.append(f"Kişisel veri içeren tabloda SELECT * kullanılamaz ({', '.join(hit)}); kolonları açıkça yazın.")
                        break
                for col in tree.find_all(exp.Column):
                    if isinstance(col.this, exp.Star):
                        continue
                    cname = col.name.lower()
                    scope = [alias_to_table.get(col.table.lower())] if col.table else list(tables)
                    for t in scope:
                        if t and cname in pii.get(t, set()):
                            errors.append(f"'{t}.{cname}' kişisel veridir (PII); bu rolle sorgulanamaz. Toplulaştırılmış/anonim alanlar kullanın.")

        if errors:
            return ValidationResult(False, sorted(set(errors)), sorted(tables))
        joins = self.join_guard.check(tree)
        if joins.errors:
            return ValidationResult(False, joins.errors, sorted(tables), warnings=joins.warnings)
        return ValidationResult(True, [], sorted(tables), sql, joins.warnings)
