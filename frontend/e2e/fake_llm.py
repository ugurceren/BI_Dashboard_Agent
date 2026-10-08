"""Uçtan uca testler için senaryolu sahte LLM (OpenAI uyumlu: /v1/models, /v1/chat/completions).

Gerçek agent (backend) bu sunucuyla konuşur; araçlar (sözlük, SQL doğrulama, veritabanı, dashboard) gerçekten çalışır.
Yalnız modelin kararları sabittir: faz (sistem talimatındaki "Şu anki faz: n/3") ve konuşmanın son adımına göre
senaryodaki sıradaki yanıt döner. Böylece her koşu aynı sonucu verir. Senaryo AdventureWorksDW'ye göre yazılmıştır.

Çalıştırma: python fake_llm.py [port]   (varsayılan 8091)
"""

from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "e2e-senaryo"

KPI_SQL = ("SELECT SUM(f.SalesAmount) AS sales_amount, COUNT(DISTINCT f.SalesOrderNumber) AS order_count "
           "FROM dbo.FactResellerSales f")
REGION_SQL = ("SELECT st.SalesTerritoryGroup AS region_group, SUM(f.SalesAmount) AS sales_amount FROM dbo.FactResellerSales f "
              "JOIN dbo.DimSalesTerritory st ON st.SalesTerritoryKey = f.SalesTerritoryKey GROUP BY st.SalesTerritoryGroup")
MONTH_SQL = ("SELECT CONVERT(char(7), f.OrderDate, 126) AS sales_month, SUM(f.SalesAmount) AS sales_amount "
             "FROM dbo.FactResellerSales f GROUP BY CONVERT(char(7), f.OrderDate, 126)")

SPEC = {
    "title": "E2E Bayi Satışları",
    "subtitle": "Uçtan uca test raporu",
    "filters": [{"id": "f_region", "label": "Bölge Grubu", "table": "dbo.DimSalesTerritory", "column": "SalesTerritoryGroup"}],
    "visuals": [
        {"id": "k_sales", "type": "kpi", "title": "Satış Tutarı", "datasetId": "kpi", "encoding": {"value": "sales_amount"},
         "options": {"format": "compact"}},
        {"id": "k_orders", "type": "kpi", "title": "Sipariş Sayısı", "datasetId": "kpi", "encoding": {"value": "order_count"}},
        {"id": "b_region", "type": "bar", "title": "Bölge Grubuna Göre Satış", "datasetId": "region",
         "encoding": {"x": "region_group", "y": ["sales_amount"]}},
        {"id": "l_month", "type": "line", "title": "Aylık Satış", "datasetId": "monthly",
         "encoding": {"x": "sales_month", "y": ["sales_amount"]}},
    ],
}


def call(name: str, args: dict) -> dict:
    return {"tool": name, "args": args}


def say(text: str) -> dict:
    return {"text": text}


def phase_of(messages: list[dict]) -> str:
    sys_text = next((m.get("content") or "" for m in messages if m.get("role") == "system"), "")
    return "design" if "3/3" in sys_text else "data" if "2/3" in sys_text else "requirements"


def done_tools(messages: list[dict]) -> list[str]:
    return [tc["function"]["name"] for m in messages if m.get("role") == "assistant" for tc in (m.get("tool_calls") or [])]


def last_user(messages: list[dict]) -> str:
    return next((str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"), "")


def decide(messages: list[dict]) -> dict:
    phase, tools, last = phase_of(messages), done_tools(messages), messages[-1]
    user = last_user(messages)
    if phase == "requirements":
        if "save_requirements" not in tools:
            return call("save_requirements", {
                "report_title": "E2E Bayi Satışları", "business_goal": "Bayi satışlarını bölge grubuna ve aya göre izlemek",
                "audience": "Satış yönetimi", "kpis": ["Satış tutarı", "Sipariş sayısı"], "dimensions": ["Bölge grubu", "Ay"],
                "time_range": "Tüm dönem"})
        return say("Gereksinimler tamam, veri fazına geçiyoruz.")

    if phase == "data":
        if "search_dictionary" not in tools:
            return call("search_dictionary", {"query": "bayi satış bölge"})
        if "propose_model" not in tools:
            return call("propose_model", {"tables": ["dbo.FactResellerSales", "dbo.DimSalesTerritory", "dbo.DimDate"]})
        if last.get("role") == "tool" and tools[-1] == "propose_model":
            return say("Bulduğum tablolar:\n- **dbo.FactResellerSales** (fact): bayi satışları\n- **dbo.DimSalesTerritory** (boyut): "
                       "satış bölgeleri\n- **dbo.DimDate** (boyut): takvim\n\nYeni ilişki önerisi: FactResellerSales.OrderDate → "
                       "DimDate.FullDateAlternateKey (N:1).\n\nBu tablolarla ve bu ilişkilerle devam edeyim mi? İlişki ortak modele "
                       "mi kaydedilsin, yalnız bu rapora mı?")
        if "save_relationships" not in tools and "evet" in user.lower():
            return call("save_relationships", {"scope": "report" if "rapor" in user.lower() else "global", "relationships": [{
                "from_table": "dbo.FactResellerSales", "to_table": "dbo.DimDate",
                "columns": [["OrderDate", "FullDateAlternateKey"]], "cardinality": "N:1", "description": "Sipariş tarihi (tarih)"}]})
        if "run_sql" not in tools and "save_relationships" in tools:
            return call("run_sql", {"sql": REGION_SQL, "purpose": "bölge kırılımını test et"})
        if "save_datasets" not in tools and "run_sql" in tools:
            return call("save_datasets", {"datasets": [
                {"id": "kpi", "description": "KPI özeti", "sql": KPI_SQL,
                 "fields": [{"name": "sales_amount", "label": "Satış Tutarı", "format": "currency"},
                            {"name": "order_count", "label": "Sipariş Sayısı", "format": "number"}]},
                {"id": "region", "description": "Bölge grubu kırılımı", "sql": REGION_SQL,
                 "fields": [{"name": "region_group", "label": "Bölge Grubu"}, {"name": "sales_amount", "label": "Satış Tutarı", "format": "currency"}]},
                {"id": "monthly", "description": "Aylık trend", "sql": MONTH_SQL,
                 "fields": [{"name": "sales_month", "label": "Ay"}, {"name": "sales_amount", "label": "Satış Tutarı", "format": "currency"}]}]})
        return say("Veri hazır.")

    # tasarım
    if "create_report_spec" not in tools:
        return call("create_report_spec", {"spec": SPEC})
    if "yeşil" in user.lower() and "update_visual" not in tools:
        return call("update_visual", {"id": "k_sales", "changes": {"options": {"color": "#16a34a"}}})
    if last.get("role") == "tool" and tools and tools[-1] == "update_visual":
        return say("Satış Tutarı KPI'sı artık yeşil.")
    if last.get("role") == "tool" and tools and tools[-1] == "create_report_spec":
        return say("Dashboard oluşturuldu: üstte satış tutarı ve sipariş sayısı KPI'ları, altında bölge grubu ve aylık trend "
                   "grafikleri. Bölge grubu filtresiyle tüm görseller süzülebilir.")
    return say("Panel sağda görünüyor; değişiklik isterseniz yazın.")


def completion(step: dict) -> dict:
    msg: dict = {"role": "assistant", "content": step.get("text")}
    finish = "stop"
    if "tool" in step:
        msg["content"] = None
        msg["tool_calls"] = [{"id": f"call_{int(time.time() * 1000)}", "type": "function",
                              "function": {"name": step["tool"], "arguments": json.dumps(step["args"], ensure_ascii=False)}}]
        finish = "tool_calls"
    return {"id": "cmpl-e2e", "object": "chat.completion", "created": int(time.time()), "model": MODEL,
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": MODEL, "object": "model", "max_model_len": 32768}]})
        else:
            self._json(404, {"error": "yok"})

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json(404, {"error": "yok"})
            return
        try:
            self._json(200, completion(decide(body.get("messages") or [])))
        except Exception as e:  # noqa: BLE001
            self._json(500, {"error": {"message": f"senaryo hatası: {e}"}})

    def log_message(self, *a) -> None:  # sessiz
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8091
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
