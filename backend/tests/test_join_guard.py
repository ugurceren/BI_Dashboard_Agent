"""JOIN doğrulayıcı: kardinalite, bileşik anahtar, fan-out ve chasm trap."""

import pytest
import sqlglot

from app.data.join_guard import JoinGuard
from app.data.validator import RolePolicy
from app.dictionary.repository import DDColumn, DDRelationship, DDTable, _group_relationships

ANALYST = RolePolicy("analyst", ["dwh"])


# --------------------------------------------------------------------------- DuckDB demo sözlüğüyle (gerçek veri)
@pytest.mark.parametrize("sql", [
    # fact → boyut (N:1) zinciri
    "SELECT b.region, SUM(f.amount_try) FROM dwh.fact_card_transaction f JOIN dwh.dim_branch b ON b.branch_id = f.branch_id GROUP BY b.region",
    # USING ile
    "SELECT region, SUM(amount_try) FROM dwh.fact_card_transaction JOIN dwh.dim_branch USING (branch_id) GROUP BY region",
    # iki fact, önce CTE'de toplanmış
    """WITH t AS (SELECT branch_id, SUM(amount_try) s FROM dwh.fact_card_transaction GROUP BY branch_id),
            l AS (SELECT branch_id, SUM(amount_try) s FROM dwh.fact_loan_disbursement GROUP BY branch_id)
       SELECT b.region, SUM(t.s), SUM(l.s) FROM dwh.dim_branch b JOIN t ON t.branch_id = b.branch_id
       JOIN l ON l.branch_id = b.branch_id GROUP BY b.region""",
    # çoğalan tarafta sadece COUNT(DISTINCT) / MAX
    "SELECT b.region, COUNT(DISTINCT f.customer_id), MAX(f.amount_try) FROM dwh.dim_branch b JOIN dwh.fact_card_transaction f ON f.branch_id = b.branch_id GROUP BY b.region",
    # WHERE ile eski usul birleştirme
    "SELECT b.region, SUM(f.amount_try) FROM dwh.fact_card_transaction f, dwh.dim_branch b WHERE b.branch_id = f.branch_id GROUP BY b.region",
])
def test_safe_joins(services, sql):
    r = services.validator.validate(sql, ANALYST)
    assert r.ok, r.errors
    assert not r.warnings, r.warnings


@pytest.mark.parametrize("sql", [
    # chasm trap: iki fact ortak boyut üzerinden
    """SELECT b.region, SUM(t.amount_try), SUM(l.amount_try) FROM dwh.dim_branch b
       JOIN dwh.fact_card_transaction t ON t.branch_id = b.branch_id
       JOIN dwh.fact_loan_disbursement l ON l.branch_id = b.branch_id GROUP BY b.region""",
    # boyut kolonunu fact satırları üzerinden toplamak
    "SELECT SUM(p.annual_fee_try) FROM dwh.dim_product p JOIN dwh.fact_card_transaction f ON f.product_id = p.product_id",
    # müşteri üzerinden işlem + kredi
    """SELECT c.segment, SUM(l.amount_try) FROM dwh.fact_loan_disbursement l
       JOIN dwh.dim_customer c ON c.customer_id = l.customer_id
       JOIN dwh.fact_card_transaction t ON t.customer_id = c.customer_id GROUP BY c.segment""",
])
def test_fanout_rejected(services, sql):
    r = services.validator.validate(sql, ANALYST)
    assert not r.ok
    assert any("Satır çoğalması" in e for e in r.errors), r.errors


def test_undefined_join_is_warning(services):
    r = services.validator.validate(
        "SELECT SUM(f.amount_try) FROM dwh.fact_card_transaction f JOIN dwh.dim_channel c ON c.channel_id = f.mcc_id", ANALYST)
    assert r.ok and any("sözlükte tanımlı değil" in w for w in r.warnings)


def test_table_details_show_cardinality_and_role(services):
    joins = services.dictionary.table_details("dwh.fact_card_transaction", False)["joins"]
    assert any("[N:1" in j and "rol: İşlem tarihi" in j for j in joins)


# --------------------------------------------------------------------------- elle kurulmuş sözlük: bileşik anahtar, 1:1, N:N
class _DD:
    def __init__(self, tables, rels):
        self.tables = {t.name: t for t in tables}
        self.relationships = rels

    def has_table(self, name):
        return name in self.tables


def _t(name, cols):
    return DDTable(name, name, "", "", "", 100, [DDColumn(name, c, c, "", "", "attribute", None, [], False, "") for c in cols])


@pytest.fixture()
def guard():
    tables = [_t("s.orders", ["order_no", "line_no", "amount"]), _t("s.order_tags", ["order_no", "line_no", "tag", "w"]),
              _t("s.person", ["id", "x"]), _t("s.passport", ["person_id", "y"]),
              _t("s.student", ["id", "fee"]), _t("s.course", ["id", "credits"])]
    rels = _group_relationships([
        {"relationship_id": "fk_tags", "from_table": "s.order_tags", "from_column": "order_no", "to_table": "s.orders", "to_column": "order_no", "cardinality": "N:1"},
        {"relationship_id": "fk_tags", "from_table": "s.order_tags", "from_column": "line_no", "to_table": "s.orders", "to_column": "line_no", "cardinality": "N:1"},
        {"relationship_id": "pp", "from_table": "s.person", "from_column": "id", "to_table": "s.passport", "to_column": "person_id", "cardinality": "1:1"},
        {"relationship_id": "enr", "from_table": "s.student", "from_column": "id", "to_table": "s.course", "to_column": "id", "cardinality": "N:N"},
    ])
    return JoinGuard(_DD(tables, rels))


def _check(guard, sql):
    return guard.check(sqlglot.parse_one(sql, read="duckdb"))


def test_composite_key_grouped_and_partial_rejected(guard):
    rel = next(r for r in guard.dd.relationships if r.id == "fk_tags")
    assert rel.pairs == [("order_no", "order_no"), ("line_no", "line_no")]
    rep = _check(guard, "SELECT COUNT(DISTINCT t.tag) FROM s.orders o JOIN s.order_tags t ON t.order_no = o.order_no")
    assert any("Bileşik anahtar eksik" in e and "line_no" in e for e in rep.errors), rep.errors
    full = _check(guard, "SELECT t.tag, SUM(t.w) FROM s.orders o JOIN s.order_tags t ON t.order_no = o.order_no AND t.line_no = o.line_no GROUP BY 1")
    assert not full.errors, full.errors  # ölçü 'çok' taraftan: güvenli
    bad = _check(guard, "SELECT t.tag, SUM(o.amount) FROM s.orders o JOIN s.order_tags t ON t.order_no = o.order_no AND t.line_no = o.line_no GROUP BY 1")
    assert any("Satır çoğalması" in e for e in bad.errors)


def test_one_to_one_is_safe(guard):
    rep = _check(guard, "SELECT SUM(p.x), SUM(q.y) FROM s.person p JOIN s.passport q ON q.person_id = p.id")
    assert not rep.errors and not rep.warnings


def test_many_to_many_rejected(guard):
    rep = _check(guard, "SELECT SUM(s.fee) FROM s.student s JOIN s.course c ON c.id = s.id")
    assert any("çoktan çoka" in e for e in rep.errors), rep.errors


def test_one_to_many_is_normalized():
    [r] = _group_relationships([{"from_table": "d.dim", "from_column": "k", "to_table": "d.fact", "to_column": "k", "cardinality": "1:N"}])
    assert (r.from_table, r.to_table, r.cardinality) == ("d.fact", "d.dim", "N:1")


def test_model_for_ui(services):
    m = services.dictionary.model()
    kinds = {t["id"]: t["kind"] for t in m["tables"]}
    assert kinds["dwh.fact_card_transaction"] == "fact" and kinds["dwh.dim_branch"] == "dimension"
    tx = next(t for t in m["tables"] if t["id"] == "dwh.fact_card_transaction")
    assert any(c["id"] == "branch_id" and c["is_key"] for c in tx["columns"])
    rel = next(r for r in m["relationships"] if r["id"] == "tx_date")
    assert rel["cardinality"] == "N:1" and rel["role"] == "İşlem tarihi"
