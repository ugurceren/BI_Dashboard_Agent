from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.llm.gateway import AssistantTurn, ToolCall  # noqa: E402


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


def call(name: str, **args) -> ToolCall:
    return ToolCall(id=f"c_{name}", name=name, arguments=args)


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, sessions_dir=tmp_path / "sessions", audit_log=tmp_path / "audit.jsonl",
                    llm_base_url="http://127.0.0.1:9/v1")


@pytest.fixture()
def services(settings):
    from app.config import load_toml
    from app.data.connector import create_connector
    from app.data.validator import RolePolicy, SqlValidator
    from app.dictionary.repository import DataDictionary
    from app.harness.tools import Services

    con = create_connector(settings)
    dd = DataDictionary(settings, con).load()
    pol = load_toml(settings.policy_config)
    policies = {n: RolePolicy(name=n, **c) for n, c in pol["roles"].items()}
    return Services(settings, dd, con, SqlValidator(dd, con.dialect, pol["sql"]["denied_functions"]), policies)
