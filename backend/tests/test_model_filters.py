"""Model tabanlı filtre yayılımı (Power BI tarzı) ve alan kökeni."""

import pytest

from app.data.model_filters import ModelFilter, ModelFilterEngine
from app.data.validator import RolePolicy
from app.dictionary.repository import _group_relationships
from tests.test_join_guard import _DD, _t

ANALYST = RolePolicy("analyst", ["dwh"])


@pytest.fixture()
def eng(services):
    return ModelFilterEngine(services.dictionary, "duckdb")


def _run(services, sql):
    v = services.validator.validate(sql, ANALYST)
    assert v.ok, v.errors
    return services.connector.execute(v.sql, 5000).rows


def test_filter_propagates_from_dimension_to_fact(services, eng):
    # dataset'te şube/bölge yok; filtre dim_branch → fact_card_transaction ilişkisiyle yayılmalı
    sql = "SELECT d.year, SUM(f.amount_try) AS s FROM dwh.fact_card_transaction f JOIN dwh.dim_date d ON d.date_key = f.date_key GROUP BY d.year ORDER BY 1"
    out, applied = eng.apply(sql, [ModelFilter("dwh.dim_branch", "region", ["Ege"])])
    assert applied == ["dwh.dim_branch.region"] and "EXISTS" in out
    got = _run(services, out)
    want = _run(services, """SELECT d.year, SUM(f.amount_try) FROM dwh.fact_card_transaction f JOIN dwh.dim_date d ON d.date_key = f.date_key
                             JOIN dwh.dim_branch b ON b.branch_id = f.branch_id WHERE b.region = 'Ege' GROUP BY d.year ORDER BY 1""")
    assert [(r[0], round(r[1], 2)) for r in got] == [(r[0], round(r[1], 2)) for r in want]


def test_filter_table_in_query_is_filtered_directly(eng):
    sql = "SELECT b.region, SUM(f.amount_try) FROM dwh.fact_card_transaction f JOIN dwh.dim_branch b ON b.branch_id = f.branch_id GROUP BY 1"
    out, applied = eng.apply(sql, [ModelFilter("dwh.dim_branch", "region", ["Ege", "Marmara"])])
    assert "b.region IN ('Ege', 'Marmara')" in out and "EXISTS" not in out and applied


def test_filter_applies_inside_ctes_and_union_branches(services, eng):
    sql = """WITH t AS (SELECT branch_id, SUM(amount_try) s FROM dwh.fact_card_transaction GROUP BY branch_id)
             SELECT 'kart' AS k, SUM(s) FROM t
             UNION ALL SELECT 'kredi', SUM(amount_try) FROM dwh.fact_loan_disbursement"""
    out, applied = eng.apply(sql, [ModelFilter("dwh.dim_branch", "region", ["Ege"])])
    assert out.count("EXISTS") == 2 and applied
    assert len(_run(services, out)) == 2


def test_unrelated_dataset_is_not_filtered(eng):
    # kredi tablosunun işyeri kategorisiyle ilişkisi yok
    sql = "SELECT SUM(amount_try) FROM dwh.fact_loan_disbursement"
    out, applied = eng.apply(sql, [ModelFilter("dwh.dim_merchant_category", "category_name", ["Market"])])
    assert applied == [] and out.strip().upper().startswith("SELECT SUM")


def test_fact_preferred_over_dimension_path(eng):
    # dim_customer da (ana şube üzerinden) dim_branch'ten ulaşılabilir; fact varken yalnız fact filtrelenmeli
    sql = "SELECT c.segment, SUM(f.amount_try) FROM dwh.fact_card_transaction f JOIN dwh.dim_customer c ON c.customer_id = f.customer_id GROUP BY 1"
    out, _ = eng.apply(sql, [ModelFilter("dwh.dim_branch", "region", ["Ege"])])
    assert out.count("EXISTS") == 1 and "= f.branch_id" in out


def test_lineage_through_cte(eng):
    sql = """WITH x AS (SELECT b.region AS reg, SUM(f.amount_try) AS s FROM dwh.fact_card_transaction f
             JOIN dwh.dim_branch b ON b.branch_id = f.branch_id GROUP BY b.region) SELECT reg AS bolge, s FROM x"""
    assert eng.lineage(sql) == {"bolge": "dwh.dim_branch.region"}


# --------------------------------------------------------------------------- elle kurulmuş model
@pytest.fixture()
def toy():
    tables = [_t("s.cat", ["cat_id", "name"]), _t("s.prod", ["prod_id", "cat_id"]),
              _t("s.sales", ["prod_id", "ship_date", "order_date", "amt"]), _t("s.dt", ["d", "yr"]),
              _t("s.ord", ["no", "line", "amt"]), _t("s.ordx", ["no", "line", "tag"])]
    rels = _group_relationships([
        {"relationship_id": "p_c", "from_table": "s.prod", "from_column": "cat_id", "to_table": "s.cat", "to_column": "cat_id", "cardinality": "N:1"},
        {"relationship_id": "s_p", "from_table": "s.sales", "from_column": "prod_id", "to_table": "s.prod", "to_column": "prod_id", "cardinality": "N:1"},
        {"relationship_id": "s_od", "from_table": "s.sales", "from_column": "order_date", "to_table": "s.dt", "to_column": "d", "cardinality": "N:1", "is_active": 1},
        {"relationship_id": "s_sd", "from_table": "s.sales", "from_column": "ship_date", "to_table": "s.dt", "to_column": "d", "cardinality": "N:1", "is_active": 0},
        {"relationship_id": "x", "from_table": "s.ordx", "from_column": "no", "to_table": "s.ord", "to_column": "no", "cardinality": "N:1"},
        {"relationship_id": "x", "from_table": "s.ordx", "from_column": "line", "to_table": "s.ord", "to_column": "line", "cardinality": "N:1"},
    ])
    dd = _DD(tables, rels)
    dd._table_kinds = lambda: {"s.sales": "fact", "s.ordx": "bridge"}
    return ModelFilterEngine(dd, "duckdb")


def test_multi_hop_nested_exists(toy):
    out, applied = toy.apply("SELECT SUM(amt) FROM s.sales f", [ModelFilter("s.cat", "name", ["Bikes"])])
    assert applied and out.count("EXISTS") == 2
    assert "_mf0.prod_id = f.prod_id" in out and "_mf1.cat_id = _mf0.cat_id" in out and "_mf1.name IN ('Bikes')" in out


def test_only_active_relationship_propagates(toy):
    out, _ = toy.apply("SELECT SUM(amt) FROM s.sales f", [ModelFilter("s.dt", "yr", [2013])])
    assert "= f.order_date" in out and "ship_date" not in out


def test_composite_key_path(toy):
    out, _ = toy.apply("SELECT COUNT(*) FROM s.ordx x", [ModelFilter("s.ord", "amt", [5])])
    assert "_mf0.no = x.no" in out and "_mf0.line = x.line" in out
