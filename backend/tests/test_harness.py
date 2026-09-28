"""Harness'i gerçek model olmadan uçtan uca test eder (senaryolu FakeLLM)."""

import json

from app.harness.agent import Agent
from app.harness.session import SessionStore
from app.llm.gateway import AssistantTurn, _extract_prompt_tool_calls, _to_prompt_mode, parse_json_loose
from tests.conftest import FakeLLM, call

KPI_SQL = """SELECT SUM(f.SalesAmount) AS sales_amount, COUNT(DISTINCT f.SalesOrderNumber) AS order_count
FROM dbo.FactInternetSales f JOIN dbo.DimDate d ON d.DateKey = f.OrderDateKey WHERE d.CalendarYear = 2013"""
REGION_SQL = """SELECT t.SalesTerritoryRegion AS region, SUM(f.SalesAmount) AS sales_amount
FROM dbo.FactInternetSales f JOIN dbo.DimSalesTerritory t ON t.SalesTerritoryKey = f.SalesTerritoryKey
GROUP BY t.SalesTerritoryRegion"""
TREND_SQL = """SELECT CONVERT(char(7), d.FullDateAlternateKey, 126) AS year_month, SUM(f.SalesAmount) AS sales_amount
FROM dbo.FactInternetSales f JOIN dbo.DimDate d ON d.DateKey = f.OrderDateKey
GROUP BY CONVERT(char(7), d.FullDateAlternateKey, 126) ORDER BY year_month"""


def _run(agent, sid, text, images=None):
    return list(agent.run_turn(sid, text, images))


def test_full_flow(settings, services):
    script = [
        # --- ihtiyaç fazı: bir soru sor
        AssistantTurn("Hangi zaman aralığını istersiniz?"),
        # kullanıcı yanıtlar → gereksinimleri kaydet (faz → data, aynı turda devam)
        AssistantTurn("", [call("save_requirements", report_title="İnternet Satış Raporu", business_goal="Satışları izlemek",
                                kpis=["Satış tutarı"], dimensions=["Bölge", "Ay"], time_range="2013")]),
        AssistantTurn("", [call("search_dictionary", query="internet satış bölge")]),
        AssistantTurn("", [call("get_table_details", tables=["dbo.FactInternetSales", "dbo.DimSalesTerritory"])]),
        # hatalı SQL → harness hatayı modele geri verir
        AssistantTurn("", [call("run_sql", sql="SELECT EmailAddress FROM dbo.DimCustomer")]),
        AssistantTurn("", [call("run_sql", sql=REGION_SQL)]),
        AssistantTurn("", [call("save_datasets", datasets=[
            {"id": "kpi_summary", "description": "KPI", "sql": KPI_SQL},
            {"id": "region_sales", "description": "Bölge", "sql": REGION_SQL},
            {"id": "monthly_trend", "description": "Trend", "sql": TREND_SQL},
        ])]),
        AssistantTurn("Veriler hazır. Southwest en yüksek bölge. Nasıl bir tasarım istersiniz?"),
        # --- tasarım fazı
        AssistantTurn("", [call("create_report_spec", spec={
            "title": "İnternet Satış Raporu",
            "theme": {"mode": "dark"},
            "visuals": [
                {"id": "k1", "type": "kpi", "title": "Satış", "datasetId": "kpi_summary", "encoding": {"value": "sales_amount"}},
                {"id": "k2", "type": "kpi", "title": "Adet", "datasetId": "kpi_summary", "encoding": {"value": "order_count"},
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

    ev = _run(agent, s.id, "İnternet satış raporu istiyorum")
    assert ev[-1].type == "done"
    s = store.get(s.id)
    assert s.phase == "requirements" and s.transcript[-1].role == "assistant"
    assert "save_requirements" in llm.calls[0]["tools"] and "run_sql" not in llm.calls[0]["tools"]

    _run(agent, s.id, "2013 yılı, bölge ve ay kırılımı")
    s = store.get(s.id)
    assert s.phase == "design"
    assert [d.id for d in s.datasets] == ["kpi_summary", "region_sales", "monthly_trend"]
    kpi = next(d for d in s.datasets if d.id == "kpi_summary")
    assert {f.name: f.format for f in kpi.fields} == {"sales_amount": "currency", "order_count": "number"}
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
    k1 = next(v for v in s.spec.visuals if v.id == "k1")
    assert k1.options.currency == settings.default_currency, "para birimi belirtilmemiş tutar KPI'sına varsayılan verilmeli"
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


def test_repeat_guard_and_working_memory(settings, services):
    from app.harness.phases import system_prompt

    sql = "SELECT TOP 5 SalesTerritoryGroup AS grp FROM dbo.DimSalesTerritory"
    llm = FakeLLM([
        AssistantTurn("", [call("save_requirements", report_title="R", business_goal="g", kpis=["Satış"], dimensions=["Bölge"])]),
        AssistantTurn("", [call("run_sql", sql=sql, purpose="bölgeler")]),
        AssistantTurn("", [call("run_sql", sql=sql, purpose="bölgeler")]),   # aynı çağrı → önbellek
        AssistantTurn("tamam"),
    ], settings)
    store = SessionStore(settings.sessions_dir)
    agent = Agent(llm, services, store)
    s = store.create("analyst")
    _run(agent, s.id, "rapor")
    s = store.get(s.id)
    runs = [t.tool for t in s.transcript if t.role == "tool" and t.tool.name == "run_sql"]
    assert runs[1].summary.startswith("Tekrarlanan çağrı")
    assert len(services.connector.executed) == 1, "tekrar eden sorgu veritabanına gitmemeli"
    prompt = system_prompt(s, "tsql")
    assert "Çalışma hafızası" in prompt and sql in prompt


def test_trim_keeps_phase_anchor_within_budget():
    from app.harness.agent import _trim

    msgs = [{"role": "user", "content": "[HARNESS] görev"}]
    for i in range(50):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": str(i), "type": "function",
                     "function": {"name": "x", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": str(i), "content": "r" * 1000})
    out = _trim(msgs, 10_000)
    assert out[0]["content"] == "[HARNESS] görev"
    assert "bağlamdan çıkarıldı" in out[1]["content"]
    assert out[2]["role"] == "assistant" and sum(len(m.get("content") or "") for m in out) < 14_000
    assert _trim(msgs[:3], 10_000) == msgs[:3], "bütçe aşılmıyorsa hiçbir şey atılmamalı"


def test_error_hint_suggests_join_path(settings, services):
    from app.harness.session import Session
    from app.harness.tools import ToolContext, _error_hints

    msg = "[SQL Server]Invalid column name 'EnglishProductCategoryName'. (207)"
    hints = _error_hints(ToolContext(Session(), services), msg, ["dbo.factresellersales", "dbo.dimproduct"])
    assert "dbo.dimproductcategory" in hints[0] and "dbo.dimproductsubcategory.productcategorykey" in hints[0]


def test_save_dataset_by_verified_number_and_loop_breaker(settings, services):
    sql = "SELECT TOP 5 SalesTerritoryGroup AS grp, SUM(1) AS n FROM dbo.DimSalesTerritory GROUP BY SalesTerritoryGroup"
    bad = "SELECT nope FROM dbo.DimSalesTerritory"
    llm = FakeLLM([
        AssistantTurn("", [call("save_requirements", report_title="R", business_goal="g", kpis=["Satış"], dimensions=["Bölge"])]),
        AssistantTurn("", [call("run_sql", sql=sql)]),
        AssistantTurn("", [call("save_datasets", datasets=[{"id": "groups", "verified": 1}])]),
        AssistantTurn("hazır"),
    ], settings)
    store = SessionStore(settings.sessions_dir)
    s = store.create("analyst")
    list(Agent(llm, services, store).run_turn(s.id, "rapor"))
    s = store.get(s.id)
    assert s.phase == "design" and s.datasets[0].sql == sql

    # döngü kırıcı: aynı çağrı üst üste tekrarlanınca tur biter
    llm2 = FakeLLM([AssistantTurn("", [call("run_sql", sql=bad)])] * 10, settings)
    s2 = store.create("analyst")
    s2.set_phase("data")
    s2.requirements = s.requirements
    store.save(s2)
    list(Agent(llm2, services, store).run_turn(s2.id, "devam"))
    s2 = store.get(s2.id)
    assert len(llm2.calls) == 4 and "tekrar tekrar" in s2.transcript[-1].content


def test_unfulfilled_claim_is_nudged(settings, services):
    sql = "SELECT SalesTerritoryGroup AS grp, SUM(1) AS n FROM dbo.DimSalesTerritory GROUP BY SalesTerritoryGroup"
    spec = {"title": "T", "visuals": [{"id": "b", "type": "bar", "title": "B", "datasetId": "g", "encoding": {"x": "grp", "y": ["n"]}}]}
    llm = FakeLLM([
        AssistantTurn("", [call("save_requirements", report_title="R", business_goal="g", kpis=["Satış"], dimensions=["Bölge"])]),
        AssistantTurn("", [call("save_datasets", datasets=[{"id": "g", "sql": sql}])]),
        AssistantTurn("Nasıl bir tasarım?"),
        AssistantTurn("Tasarım tamamlandı, dashboard oluşturuldu."),   # yalan: araç çağrısı yok
        AssistantTurn("", [call("create_report_spec", spec=spec)]),
        AssistantTurn("Hazır."),
    ], settings)
    store = SessionStore(settings.sessions_dir)
    agent = Agent(llm, services, store)
    s = store.create("analyst")
    _run(agent, s.id, "rapor")
    _run(agent, s.id, "koyu tema")
    s = store.get(s.id)
    assert s.spec is not None and s.spec_version == 1
    shown = [t.content for t in s.transcript if t.role == "assistant"]
    assert "Tasarım tamamlandı, dashboard oluşturuldu." not in shown and shown[-1] == "Hazır."


def test_wrong_filter_column_is_resolved(settings, services):
    from app.harness.session import Session
    from app.harness.tools import ToolContext, _resolve_filters

    raw = {"filters": [{"id": "f", "label": "Ürün Kategorisi", "table": "dbo.DimProduct", "column": "ProductCategory"}]}
    notes = _resolve_filters(ToolContext(Session(), services), raw)
    assert raw["filters"][0]["table"] == "dbo.DimProductCategory" and notes


def test_swapped_axes_are_fixed():
    from app.harness.tools import _fix_axes

    raw = {"datasets": [{"id": "r", "fields": [{"name": "region", "type": "string"}, {"name": "sales", "type": "number"}]}],
           "visuals": [{"id": "b", "type": "bar", "datasetId": "r", "encoding": {"x": "sales", "y": ["region"]}}]}
    assert _fix_axes(raw) and raw["visuals"][0]["encoding"] == {"x": "region", "y": ["sales"]}


def test_spec_sanitizes_series_and_delta():
    from app.harness.tools import _fix_axes

    raw = {"datasets": [{"id": "k", "fields": [{"name": "sales", "type": "number"}, {"name": "m", "type": "string"}]}],
           "visuals": [{"id": "a", "type": "kpi", "datasetId": "k", "encoding": {"value": "sales"}, "options": {"deltaField": "sales"}},
                       {"id": "b", "type": "line", "datasetId": "k", "encoding": {"x": "m", "y": ["sales"], "series": "sales"}}]}
    notes = _fix_axes(raw)
    assert raw["visuals"][0]["options"]["deltaField"] is None and raw["visuals"][1]["encoding"]["series"] is None and len(notes) == 2


def test_user_message_carried_into_next_phase(settings, services):
    sql = "SELECT SalesTerritoryGroup AS grp, SUM(1) AS n FROM dbo.DimSalesTerritory GROUP BY SalesTerritoryGroup"
    llm = FakeLLM([
        AssistantTurn("", [call("save_requirements", report_title="R", business_goal="g", kpis=["Satış"], dimensions=["Bölge"])]),
        AssistantTurn("", [call("save_datasets", datasets=[{"id": "g", "sql": sql}])]),
        AssistantTurn("tamam"),
    ], settings)
    store = SessionStore(settings.sessions_dir)
    s = store.create("analyst")
    list(Agent(llm, services, store).run_turn(s.id, "Koyu tema olsun"))
    design_call = next(c for c in llm.calls if "create_report_spec" in c["tools"])
    assert "Koyu tema olsun" in design_call["messages"][1]["content"]


def test_kpi_category_becomes_compare_field():
    from app.harness.tools import _fix_axes

    raw = {"datasets": [{"id": "k", "fields": [{"name": "s13", "type": "number"}, {"name": "s12", "type": "number"}]}],
           "visuals": [{"id": "a", "type": "kpi", "datasetId": "k", "encoding": {"value": "s13", "category": "s12"}, "options": {}}]}
    _fix_axes(raw)
    assert raw["visuals"][0]["options"]["compareField"] == "s12" and raw["visuals"][0]["encoding"]["category"] is None
