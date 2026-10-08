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
        # iterasyon: bölge grafiğini donut yap — yalnız tür verilir; x / y alanlarını sistem category / value'ya yerleştirir
        AssistantTurn("", [call("update_visual", id="r", changes={"type": "donut"})]),
        AssistantTurn("Bölge grafiği donut oldu."),
    ]
    llm = FakeLLM(script, settings)
    store = SessionStore(settings.sessions_dir)
    agent = Agent(llm, services, store)
    s = store.create("standart")

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
    assert r.encoding.value == "sales_amount" and r.encoding.x is None
    failed = [t.tool for t in s.transcript if t.role == "tool" and t.tool.name == "update_visual" and not t.tool.ok]
    assert not failed, "x / y verilmiş donut reddedilmemeli (model aynı hatayla döngüye giriyordu)"
    assert not s.busy


def test_step_limit_and_unknown_tool(settings, services):
    settings.max_agent_steps = 3
    llm = FakeLLM([AssistantTurn("", [call("hack_the_db")])] * 5, settings)
    store = SessionStore(settings.sessions_dir)
    agent = Agent(llm, services, store)
    s = store.create("standart")
    _run(agent, s.id, "merhaba")
    s = store.get(s.id)
    assert len(llm.calls) == 3
    assert all(not t.tool.ok for t in s.transcript if t.role == "tool")
    assert "adım sınırına" in s.transcript[-1].content


def test_tool_not_allowed_in_phase(settings, services):
    llm = FakeLLM([AssistantTurn("", [call("run_sql", sql="SELECT 1")]), AssistantTurn("tamam")], settings)
    store = SessionStore(settings.sessions_dir)
    agent = Agent(llm, services, store)
    s = store.create("standart")
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
    s = store.create("standart")
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
    s = store.create("standart")
    list(Agent(llm, services, store).run_turn(s.id, "rapor"))
    s = store.get(s.id)
    assert s.phase == "design" and s.datasets[0].sql == sql

    # döngü kırıcı: aynı çağrı üst üste tekrarlanınca tur biter
    llm2 = FakeLLM([AssistantTurn("", [call("run_sql", sql=bad)])] * 10, settings)
    s2 = store.create("standart")
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
    s = store.create("standart")
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
    s = store.create("standart")
    list(Agent(llm, services, store).run_turn(s.id, "Koyu tema olsun"))
    design_call = next(c for c in llm.calls if "create_report_spec" in c["tools"])
    assert "Koyu tema olsun" in design_call["messages"][1]["content"]


def test_kpi_category_becomes_compare_field():
    from app.harness.tools import _fix_axes

    raw = {"datasets": [{"id": "k", "fields": [{"name": "s13", "type": "number"}, {"name": "s12", "type": "number"}]}],
           "visuals": [{"id": "a", "type": "kpi", "datasetId": "k", "encoding": {"value": "s13", "category": "s12"}, "options": {}}]}
    _fix_axes(raw)
    assert raw["visuals"][0]["options"]["compareField"] == "s12" and raw["visuals"][0]["encoding"]["category"] is None


def test_numeric_category_axis_uses_name_column():
    from app.harness.tools import _fix_axes

    raw = {"datasets": [{"id": "p", "fields": [{"name": "product_name", "type": "string"}, {"name": "order_count", "type": "number"},
                                                 {"name": "product_count", "type": "number"}]}],
           "visuals": [{"id": "b", "type": "bar", "datasetId": "p",
                        "encoding": {"x": "order_count", "y": ["product_count"], "category": "product_name"}}]}
    _fix_axes(raw)
    e = raw["visuals"][0]["encoding"]
    assert e["x"] == "product_name" and e["y"] == ["order_count", "product_count"]


def test_literal_only_dataset_is_rejected(settings, services):
    from app.harness.session import Session
    from app.harness.tools import ToolContext, _build_dataset

    ds, _, errs = _build_dataset(ToolContext(Session(), services), {"id": "kpi", "sql": "SELECT 40 AS order_count, 34 AS product_count"})
    assert ds is None and "UYDURMA" in errs[0]


def test_same_title_overwrites_older_report(settings):
    store = SessionStore(settings.sessions_dir)
    a = store.create("standart"); a.title = "Bayi Satış Raporu"; store.save(a)
    b = store.create("standart"); store.create("standart")          # iki "Yeni rapor" taslağı birbirini silmez
    b.title = "bayi  satış RAPORU"; store.save(b)                # aynı isim (büyük/küçük harf, boşluk farkı)
    titles = [x["title"] for x in store.list()]
    assert titles.count("Yeni rapor") == 1 and len([t for t in titles if "satış" in t.lower()]) == 1
    assert b.id in [x["id"] for x in store.list()] and a.id not in [x["id"] for x in store.list()]


def test_rename_conflict_and_summary(settings):
    from fastapi.testclient import TestClient

    import app.main as m
    from app.harness.session import SessionStore as SS

    m.state.store = SS(settings.sessions_dir)
    c = TestClient(m.app)
    a = m.state.store.create("standart"); a.title = "A Raporu"; m.state.store.save(a)
    b = m.state.store.create("standart")
    r = c.put(f"/api/sessions/{b.id}/title", json={"title": "a  raporu"})
    assert r.status_code == 409 and r.json()["conflict_id"] == a.id
    r = c.put(f"/api/sessions/{b.id}/title", json={"title": "A Raporu", "overwrite": True})
    assert r.status_code == 200 and r.json()["title"] == "A Raporu"
    ids = [x["id"] for x in m.state.store.list()]
    assert b.id in ids and a.id not in ids
    assert {"kpis", "visual_count", "business_goal", "theme"} <= set(m.state.store.list()[0])


def test_report_status(settings):
    from fastapi.testclient import TestClient

    import app.main as m
    from app.harness.session import SessionStore as SS

    m.state.store = SS(settings.sessions_dir)
    c = TestClient(m.app)
    s = m.state.store.create("standart")
    assert m.state.store.list()[0]["status"] == "idea" and not m.state.store.list()[0]["status_explicit"]
    assert c.put(f"/api/sessions/{s.id}/status", json={"status": "live"}).status_code == 200
    assert m.state.store.list()[0]["status"] == "live"
    assert c.put(f"/api/sessions/{s.id}/status", json={"status": "xx"}).status_code == 400


def test_summary_source_tables_and_domains(tmp_path):
    from app.harness.session import _summary
    d = {"id": "abc", "title": "X", "datasets": [
        {"id": "a", "sql": "WITH c AS (SELECT 1 AS x) SELECT f.SalesAmount FROM dbo.FactResellerSales f JOIN dbo.DimDate d ON 1=1 JOIN c ON 1=1"},
        {"id": "b", "sql": "SELECT * FROM rpt.v_x", "view": "rpt.v_x", "original_sql": "SELECT p.EnglishProductName FROM DimProduct p"},
        {"id": "c", "sql": "SELECT FROM WHERE (("},
    ]}
    s = _summary(d)
    assert s["source_tables"] == ["dbo.dimdate", "dbo.dimproduct", "dbo.factresellersales", "rpt.v_x"]


def test_user_title_not_overwritten_by_agent(settings, services):
    """Kullanıcı raporu adlandırdıysa agent'ın save_requirements / spec başlığı adı değiştirmez."""
    from fastapi.testclient import TestClient

    import app.main as m
    from app.harness.session import SessionStore as SS
    from app.harness.tools import ToolContext, h_save_requirements

    m.state.store = SS(settings.sessions_dir)
    c = TestClient(m.app)
    s = m.state.store.create("standart")
    assert c.put(f"/api/sessions/{s.id}/title", json={"title": "Bölge Satış Özeti"}).status_code == 200
    s = m.state.store.get(s.id)
    assert s.title_locked
    r = h_save_requirements(ToolContext(s, services), {"report_title": "Agent Başlığı", "business_goal": "x", "kpis": ["Ciro"], "dimensions": []})
    assert r.ok and s.title == "Bölge Satış Özeti" and s.requirements.report_title == "Bölge Satış Özeti"

    s2 = m.state.store.create("standart")  # adlandırılmamış yeni rapor: agent adı verir
    h_save_requirements(ToolContext(s2, services), {"report_title": "Agent Başlığı", "business_goal": "x", "kpis": ["Ciro"], "dimensions": []})
    assert s2.title == "Agent Başlığı" and not s2.title_locked


def test_data_phase_shows_saved_dataset_sql(settings):
    """Veri fazına geri dönülünce kayıtlı dataset'lerin SQL'i prompt'ta olmalı (model onları DB tablosu sanmasın)."""
    from app.harness.phases import system_prompt
    from app.harness.session import Session
    from app.spec.models import Dataset, DatasetField

    s = Session(user_role="standart")
    s.phase = "data"
    s.datasets = [Dataset(id="promo_sales", description="Promosyon bazında satış",
                          sql="SELECT PromotionKey, SUM(SalesAmount) AS sales FROM dbo.FactInternetSales GROUP BY PromotionKey",
                          fields=[DatasetField(name="PromotionKey"), DatasetField(name="sales", type="number")])]
    p = system_prompt(s, "tsql")
    assert "promo_sales" in p and "GROUP BY PromotionKey" in p and "tablo DEĞİLDİR" in p and "AYNI id" in p


def test_cross_filter_reaches_view_backed_dataset(settings, services):
    """Onaylı view (yalnız bölge kırılımı) kategori filtresini taşıyamaz: kaynak SQL filtrelenerek çalışmalı."""
    import app.main as m
    from app.harness.session import Session
    from app.spec.models import Dataset, DatasetField

    m.state.services = services
    src = ("SELECT t.SalesTerritoryRegion AS region, SUM(f.SalesAmount) AS sales_amount FROM dbo.FactResellerSales f "
           "JOIN dbo.DimSalesTerritory t ON t.SalesTerritoryKey = f.SalesTerritoryKey GROUP BY t.SalesTerritoryRegion")
    s = Session(user_role="standart")
    s.datasets = [Dataset(id="region_sales", sql="SELECT region, sales_amount FROM rpt.v_bolge", view="rpt.v_bolge", original_sql=src,
                          fields=[DatasetField(name="region"), DatasetField(name="sales_amount", type="number")])]
    key = "dbo.dimproductcategory.englishproductcategoryname"
    out = m._dashboard_data(s, [{"key": key, "values": ["Bikes"]}])
    assert out["applied"]["region_sales"] == [key]



def test_style_claim_without_tool_is_nudged(settings, services):
    """'KPI yazı tipini 40 punto yap' → model araç çağırmadan 'ayarlandı' dedi: metin kullanıcıya gösterilmez, araç
    çağırtılır; desteklenmeyen alan "UYGULANMADI" döner ve model bunu söyler."""
    from app.harness.tools import ToolContext, h_create_report_spec
    from app.spec.models import Dataset, DatasetField

    store = SessionStore(settings.sessions_dir)
    s = store.create("standart")
    s.set_phase("design")
    s.datasets = [Dataset(id="kpi", sql="SELECT 1 AS amount", fields=[DatasetField(name="amount", type="number")])]
    assert h_create_report_spec(ToolContext(s, services), {"spec": {"title": "T", "visuals": [
        {"id": "k1", "type": "kpi", "title": "Tutar", "datasetId": "kpi", "encoding": {"value": "amount"}}]}}).ok
    store.save(s)
    llm = FakeLLM([
        AssistantTurn("KPI yazı tipi boyutu 40 punto olarak ayarlandı."),          # yalan: araç yok
        AssistantTurn("", [call("update_visual", id="k1", changes={"options": {"fontSize": 40}})]),
        AssistantTurn("Yazı tipi boyutu desteklenmiyor; değer boyutunu xl yapabilirim."),
    ], settings)
    _run(Agent(llm, services, store), s.id, "KPI yazı tipini 40 punto yap")
    s = store.get(s.id)
    shown = [t.content for t in s.transcript if t.role == "assistant"]
    assert "KPI yazı tipi boyutu 40 punto olarak ayarlandı." not in shown
    assert shown[-1].startswith("Yazı tipi boyutu desteklenmiyor")
    assert any(t.tool and t.tool.name == "update_visual" for t in s.transcript)
