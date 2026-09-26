"""Harness'i gerçek model olmadan uçtan uca test eder (senaryolu FakeLLM)."""

import json

from app.harness.agent import Agent
from app.harness.session import SessionStore
from app.llm.gateway import AssistantTurn, _extract_prompt_tool_calls, _to_prompt_mode, parse_json_loose
from tests.conftest import FakeLLM, call

KPI_SQL = """SELECT SUM(f.amount_try) AS sales_amount, COUNT(*) AS tx_count
FROM dwh.fact_card_transaction f JOIN dwh.dim_date d ON d.date_key = f.date_key WHERE d.year = 2026"""
REGION_SQL = """SELECT b.region, SUM(f.amount_try) AS sales_amount
FROM dwh.fact_card_transaction f JOIN dwh.dim_branch b ON b.branch_id = f.branch_id GROUP BY b.region"""
TREND_SQL = """SELECT d.year_month, SUM(f.amount_try) AS sales_amount
FROM dwh.fact_card_transaction f JOIN dwh.dim_date d ON d.date_key = f.date_key GROUP BY 1 ORDER BY 1"""


def _run(agent, sid, text, images=None):
    return list(agent.run_turn(sid, text, images))


def test_full_flow(settings, services):
    script = [
        # --- ihtiyaç fazı: bir soru sor
        AssistantTurn("Hangi zaman aralığını istersiniz?"),
        # kullanıcı yanıtlar → gereksinimleri kaydet (faz → data, aynı turda devam)
        AssistantTurn("", [call("save_requirements", report_title="Kart Satış Raporu", business_goal="Satışları izlemek",
                                kpis=["Satış tutarı"], dimensions=["Bölge", "Ay"], time_range="2026")]),
        AssistantTurn("", [call("search_dictionary", query="kredi kartı satış bölge")]),
        AssistantTurn("", [call("get_table_details", tables=["dwh.fact_card_transaction", "dwh.dim_branch"])]),
        # hatalı SQL → harness hatayı modele geri verir
        AssistantTurn("", [call("run_sql", sql="SELECT national_id FROM dwh.dim_customer")]),
        AssistantTurn("", [call("run_sql", sql=REGION_SQL)]),
        AssistantTurn("", [call("save_datasets", datasets=[
            {"id": "kpi_summary", "description": "KPI", "sql": KPI_SQL},
            {"id": "region_sales", "description": "Bölge", "sql": REGION_SQL},
            {"id": "monthly_trend", "description": "Trend", "sql": TREND_SQL},
        ])]),
        AssistantTurn("Veriler hazır. Marmara en yüksek bölge. Nasıl bir tasarım istersiniz?"),
        # --- tasarım fazı
        AssistantTurn("", [call("create_report_spec", spec={
            "title": "Kart Satış Raporu",
            "theme": {"mode": "dark"},
            "visuals": [
                {"id": "k1", "type": "kpi", "title": "Satış", "datasetId": "kpi_summary", "encoding": {"value": "sales_amount"}},
                {"id": "k2", "type": "kpi", "title": "Adet", "datasetId": "kpi_summary", "encoding": {"value": "tx_count"},
                 "position": {"x": 0, "y": 0, "w": 3, "h": 2}},  # k1 ile çakışır → otomatik taşınır
                {"id": "r", "type": "bar", "title": "Bölge", "datasetId": "region_sales", "encoding": {"x": "region", "y": "sales_amount"}},
                {"id": "t", "type": "line", "title": "Trend", "datasetId": "monthly_trend", "encoding": {"x": "year_month", "y": ["sales_amount"]}},
            ]})]),
        AssistantTurn("Koyu temalı dashboard hazır."),
        # iterasyon: bölge grafiğini donut yap (önce hatalı encoding, sonra düzeltme)
        AssistantTurn("", [call("update_visual", id="r", changes={"type": "donut"})]),
        AssistantTurn("", [call("update_visual", id="r", changes={"type": "donut", "encoding": {"category": "region", "value": "sales_amount", "x": None, "y": None}})]),
        AssistantTurn("Bölge grafiği donut oldu."),
    ]
    llm = FakeLLM(script, settings)
    store = SessionStore(settings.sessions_dir)
    agent = Agent(llm, services, store)
    s = store.create("analyst")

    ev = _run(agent, s.id, "Kredi kartı satış raporu istiyorum")
    assert ev[-1].type == "done"
    s = store.get(s.id)
    assert s.phase == "requirements" and s.transcript[-1].role == "assistant"
    assert "save_requirements" in llm.calls[0]["tools"] and "run_sql" not in llm.calls[0]["tools"]

    _run(agent, s.id, "2026 yılı, bölge ve ay kırılımı")
    s = store.get(s.id)
    assert s.phase == "design"
    assert [d.id for d in s.datasets] == ["kpi_summary", "region_sales", "monthly_trend"]
    kpi = next(d for d in s.datasets if d.id == "kpi_summary")
    assert {f.name: f.format for f in kpi.fields} == {"sales_amount": "currency", "tx_count": "number"}
    tools = [t.tool for t in s.transcript if t.role == "tool"]
    pii = next(t for t in tools if t.name == "run_sql" and not t.ok)
    assert "PII" in json.dumps(llm.calls[5]["messages"][-1], ensure_ascii=False), "PII hatası modele geri verilmeli"
    assert pii is not None
    # faz değişince bağlam sıfırlanır: tasarım fazının ilk çağrısı yalnız sistem + kickoff içerir
    design_first = next(c for c in llm.calls if "create_report_spec" in c["tools"])
    assert len(design_first["messages"]) == 2 and "[HARNESS]" in design_first["messages"][1]["content"]
    assert "kpi_summary" in design_first["messages"][0]["content"]

    _run(agent, s.id, "Koyu tema, sade olsun")
    s = store.get(s.id)
    assert s.spec is not None and s.spec_version == 1
    assert s.spec.theme.mode == "dark" and s.spec.theme.background == "#0b1220"
    pos = {v.id: (v.position.x, v.position.y) for v in s.spec.visuals}
    assert len(set(pos.values())) == 4, "çakışmalar çözülmeli"
    assert {d.id for d in s.spec.datasets} == {"kpi_summary", "region_sales", "monthly_trend"}

    _run(agent, s.id, "Bölge grafiğini donut yap")
    s = store.get(s.id)
    r = next(v for v in s.spec.visuals if v.id == "r")
    assert r.type == "donut" and r.encoding.category == "region" and s.spec_version == 2
    failed = [t.tool for t in s.transcript if t.role == "tool" and t.tool.name == "update_visual" and not t.tool.ok]
    assert failed, "eksik encoding'li ilk güncelleme reddedilmeli"
    assert not s.busy


def test_step_limit_and_unknown_tool(settings, services):
    settings.max_agent_steps = 3
    llm = FakeLLM([AssistantTurn("", [call("hack_the_db")])] * 5, settings)
    store = SessionStore(settings.sessions_dir)
    agent = Agent(llm, services, store)
    s = store.create("analyst")
    _run(agent, s.id, "merhaba")
    s = store.get(s.id)
    assert len(llm.calls) == 3
    assert all(not t.tool.ok for t in s.transcript if t.role == "tool")
    assert "adım sınırına" in s.transcript[-1].content


def test_tool_not_allowed_in_phase(settings, services):
    llm = FakeLLM([AssistantTurn("", [call("run_sql", sql="SELECT 1")]), AssistantTurn("tamam")], settings)
    store = SessionStore(settings.sessions_dir)
    agent = Agent(llm, services, store)
    s = store.create("analyst")
    _run(agent, s.id, "sql çalıştır")
    s = store.get(s.id)
    t = next(t for t in s.transcript if t.role == "tool")
    assert not t.tool.ok and "bu fazda" in llm.calls[1]["messages"][-1]["content"]


def test_prompt_mode_roundtrip():
    text, calls = _extract_prompt_tool_calls(
        'Bakıyorum.\n<tool_call>{"name": "search_dictionary", "arguments": {"query": "satış",}}</tool_call>')
    assert text == "Bakıyorum." and calls[0].name == "search_dictionary" and calls[0].arguments == {"query": "satış"}
    msgs = _to_prompt_mode([
        {"role": "system", "content": "S"},
        {"role": "user", "content": "U"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "x", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "R"},
    ], [{"type": "function", "function": {"name": "x", "description": "d", "parameters": {}}}])
    assert "<tools>" in msgs[0]["content"] and "<tool_call>" in msgs[2]["content"] and msgs[3]["role"] == "user"


def test_parse_json_loose():
    assert parse_json_loose('```json\n{"a": 1,}\n```') == {"a": 1}
    assert parse_json_loose('İşte: {"a": [1, 2,]} bitti') == {"a": [1, 2]}
