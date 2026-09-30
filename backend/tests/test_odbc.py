"""ODBC sürücüsü otomatik seçimi."""

from app.data.odbc import best_sql_server_driver, resolve_driver

CONN = "DRIVER={ODBC Driver 18 for SQL Server};SERVER=srv;DATABASE=db;Trusted_Connection=yes;TrustServerCertificate=yes;"


def test_keeps_installed_driver():
    assert resolve_driver(CONN, ["ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server"]) == CONN


def test_falls_back_to_driver_17():
    out = resolve_driver(CONN, ["SQL Server", "ODBC Driver 17 for SQL Server"])
    assert out.startswith("DRIVER={ODBC Driver 17 for SQL Server};SERVER=srv;") and "TrustServerCertificate=yes" in out


def test_newest_modern_driver_wins_and_legacy_last():
    assert best_sql_server_driver(["SQL Server", "ODBC Driver 13 for SQL Server", "ODBC Driver 17 for SQL Server"]) == "ODBC Driver 17 for SQL Server"
    out = resolve_driver(CONN, ["SQL Server"])
    assert "DRIVER={SQL Server}" in out and "TrustServerCertificate" not in out


def test_unbraced_driver_and_no_driver_available():
    s = "Driver=ODBC Driver 18 for SQL Server;Server=x;"
    assert resolve_driver(s, ["ODBC Driver 17 for SQL Server"]) == "Driver={ODBC Driver 17 for SQL Server};Server=x;"
    assert resolve_driver(CONN, ["PostgreSQL Unicode"]) == CONN
    assert resolve_driver("SERVER=x;", ["ODBC Driver 17 for SQL Server"]) == "SERVER=x;"


def test_driver17_string_on_driver18_machine_trusts_certificate():
    s = "DRIVER={ODBC Driver 17 for SQL Server};SERVER=srv;Trusted_Connection=yes;"
    out = resolve_driver(s, ["ODBC Driver 18 for SQL Server"])
    assert out == "DRIVER={ODBC Driver 18 for SQL Server};SERVER=srv;Trusted_Connection=yes;TrustServerCertificate=yes;"
    enc = "DRIVER={ODBC Driver 17 for SQL Server};SERVER=srv;Encrypt=yes;"
    assert "TrustServerCertificate" not in resolve_driver(enc, ["ODBC Driver 18 for SQL Server"])  # açık ayar korunur
