import pytest

from app.data.validator import RolePolicy

ANALYST = RolePolicy("analyst", ["dwh"])
ADMIN = RolePolicy("admin", ["dwh"], allow_pii=True)


@pytest.mark.parametrize("sql", [
    "SELECT b.region, SUM(f.amount_try) FROM dwh.fact_card_transaction f JOIN dwh.dim_branch b ON b.branch_id = f.branch_id GROUP BY 1",
    "SELECT segment, COUNT(*) FROM dwh.dim_customer GROUP BY segment",
    "WITH x AS (SELECT * FROM dwh.dim_branch) SELECT region, COUNT(*) FROM x GROUP BY region",
    "SELECT 1 AS a UNION ALL SELECT 2",
    "SELECT COUNT(DISTINCT customer_id) FROM dwh.fact_card_transaction;",
])
def test_allowed(services, sql):
    r = services.validator.validate(sql, ANALYST)
    assert r.ok, r.errors


@pytest.mark.parametrize("sql,needle", [
    ("DELETE FROM dwh.dim_date", "Yalnızca SELECT"),
    ("DROP TABLE dwh.dim_date", "Yalnızca SELECT"),
    ("SELECT 1; DROP TABLE dwh.dim_date", "Tek bir SQL"),
    ("SELECT * INTO dwh.x FROM dwh.dim_date", "INTO"),
    ("SELECT * FROM meta.dd_tables", "şemasına erişim"),
    ("SELECT * FROM read_csv('C:/secret.csv')", "izin yok"),
    ("SELECT * FROM dim_date", "şemasıyla"),
    ("SELECT * FROM dwh.not_in_dictionary", "sözlüğünde yok"),
    ("SELECT * FROM dwh.dim_customer", "SELECT *"),
    ("SELECT c.* FROM dwh.dim_customer c", "SELECT *"),
    ("SELECT national_id FROM dwh.dim_customer", "PII"),
    ("SELECT c.phone_number FROM dwh.fact_card_transaction f JOIN dwh.dim_customer c ON c.customer_id = f.customer_id", "PII"),
    ("SELECT getenv('PATH')", "izin yok"),
    ("COPY (SELECT 1) TO 'x.csv'", "Yalnızca SELECT"),
    ("ATTACH 'x.db'", "Yalnızca SELECT"),
    ("SELECT (1 FROM", "ayrıştırılamadı"),
    ("SELECT * FROM otherdb.dwh.dim_date", "Veritabanı/sunucu adı"),
])
def test_rejected(services, sql, needle):
    r = services.validator.validate(sql, ANALYST)
    assert not r.ok
    assert any(needle in e for e in r.errors), r.errors


def test_pii_allowed_for_admin(services):
    assert services.validator.validate("SELECT national_id FROM dwh.dim_customer", ADMIN).ok


def test_connector_is_read_only(services):
    from app.data.connector import QueryError

    with pytest.raises(QueryError):
        services.connector.execute("CREATE TABLE dwh.hack AS SELECT 1", 10)


def test_dictionary_search_turkish(services):
    hits = services.dictionary.search("kredi kartı satışları")
    assert hits[0]["table"] == "dwh.fact_card_transaction"
    assert any(c["name"] == "amount_try" for c in hits[0]["columns"])
    assert services.dictionary.search("KREDİ KULLANDIRIMLARI")[0]["table"] == "dwh.fact_loan_disbursement"
