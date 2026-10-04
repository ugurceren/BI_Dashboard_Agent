"""Test altyapısı.

* `services`      — AdventureWorks sözlüğünün sabit kopyası (tests/fixtures/aw_dictionary.json) + sahte bağlantı:
                    SQL Server olmadan her yerde çalışır (doğrulayıcı, JOIN koruması, filtre SQL'i, harness akışı).
* `sql_services`  — gerçek SQL Server (AdventureWorksDW2025 + BI_Meta); erişilemezse test atlanır.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import sqlglot
from sqlglot import exp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings, load_toml  # noqa: E402
from app.data.connector import QueryResult  # noqa: E402
from app.llm.gateway import AssistantTurn, ToolCall  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "aw_dictionary.json"


class FakeLLM:
    """Senaryolu model: her chat() çağrısında sıradaki yanıtı döndürür, gelen mesajları kaydeder."""

    tool_mode = "native"
    vision_enabled = False

    def __init__(self, script: list[AssistantTurn], settings: Settings):
        self.script = list(script)
        self.calls: list[dict] = []
        self.s = settings

    def chat(self, messages, tools=None):
        self.calls.append({"messages": messages, "tools": [t["function"]["name"] for t in tools or []]})
        if not self.script:
            return AssistantTurn("(senaryo bitti)")
        return self.script.pop(0)


class FakeConnector:
    """Veritabanı yerine: sorgunun en dış SELECT kolonlarına göre 3 sahte satır üretir.
    Toplama/aritmetik ifadeler sayı, diğerleri metin döner. Çalıştırılan SQL'ler kaydedilir."""

    dialect = "tsql"

    def __init__(self):
        self.executed: list[str] = []

    def execute(self, sql: str, max_rows: int) -> QueryResult:
        self.executed.append(sql)
        top = sqlglot.parse_one(sql, read="tsql")
        while not isinstance(top, exp.Select) and hasattr(top, "left"):
            top = top.left
        cols, types = [], []
        for i, p in enumerate(top.expressions):
            inner = p.this if isinstance(p, exp.Alias) else p
            numeric = bool(inner.find(exp.AggFunc)) or isinstance(inner, (exp.Binary, exp.Cast)) \
                or (isinstance(inner, exp.Literal) and inner.is_number)
            cols.append(p.alias_or_name or f"col{i}")
            types.append("number" if numeric else "string")
        # GROUP BY'sız toplama sorgusu gerçek veritabanında olduğu gibi tek satır döner
        n = 1 if top.find(exp.AggFunc) and not top.args.get("group") else 3
        rows = [[(r + 1) * 10.5 if t == "number" else f"{c}_{r}" for c, t in zip(cols, types)] for r in range(n)]
        return QueryResult(cols, types, rows[:max_rows], False, 1)

    def ping(self) -> None:
        pass


def call(name: str, **args) -> ToolCall:
    return ToolCall(id=f"c_{name}", name=name, arguments=args)


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, sessions_dir=tmp_path / "sessions", audit_log=tmp_path / "audit.jsonl",
                    llm_base_url="http://127.0.0.1:9/v1")


def _services(settings, dictionary, connector):
    from app.data.validator import RolePolicy, SqlValidator
    from app.harness.tools import Services

    pol = load_toml(settings.policy_config)
    policies = {n: RolePolicy(name=n, **c) for n, c in pol["roles"].items()}
    return Services(settings, dictionary, connector, SqlValidator(dictionary, "tsql", pol["sql"]["denied_functions"]), policies)


@pytest.fixture()
def services(settings):
    from app.dictionary.repository import DataDictionary

    con = FakeConnector()
    dd = DataDictionary.from_snapshot(settings, json.loads(FIXTURE.read_text(encoding="utf-8")), con)
    return _services(settings, dd, con)


@pytest.fixture(scope="session")
def sql_services(tmp_path_factory):
    from app.data.connector import QueryError, create_connector
    from app.dictionary.repository import DataDictionary

    tmp = tmp_path_factory.mktemp("sql")
    s = Settings(_env_file=None, sessions_dir=tmp / "sessions", audit_log=tmp / "audit.jsonl")
    try:
        con = create_connector(s)
        con.ping()
        dd = DataDictionary(s, con).load()
    except (QueryError, RuntimeError, Exception) as e:  # noqa: BLE001
        pytest.skip(f"SQL Server erişilemiyor: {str(e)[:120]}")
    return _services(s, dd, con)


@pytest.fixture(autouse=True)
def _no_live_catalog(monkeypatch):
    """Ayarlar uçları sözlük testinde veri kaynağının kataloğunu okur; testlerde gerçek sunucu yok (zaman aşımı beklenmesin)."""
    import app.main as m
    monkeypatch.setattr(m, "_data_catalog", lambda data: {})


@pytest.fixture()
def conn_file(tmp_path, monkeypatch):
    """Arayüzden kaydedilen bağlantı ayarları geçici dosyaya (gerçek backend/config/connections.json'a dokunulmaz)."""
    import app.data.connections as conns
    f = tmp_path / "connections.json"
    monkeypatch.setattr(conns, "CONNECTIONS_FILE", f)
    monkeypatch.setattr(conns, "installed_drivers", lambda: ["ODBC Driver 17 for SQL Server"])
    return f
