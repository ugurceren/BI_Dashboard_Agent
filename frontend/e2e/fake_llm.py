"""Uçtan uca testler için senaryolu sahte LLM (OpenAI uyumlu: /v1/models, /v1/chat/completions).

Gerçek agent (backend) bu sunucuyla konuşur; araçlar (sözlük, SQL doğrulama, veritabanı, dashboard) gerçekten çalışır.
Yalnız modelin kararları sabittir: faz (sistem talimatındaki "Şu anki faz: n/3") ve konuşmanın son adımına göre
senaryodaki sıradaki yanıt döner. Böylece her koşu aynı sonucu verir. Senaryo AdventureWorksDW'ye göre yazılmıştır.

Çalıştırma: python fake_llm.py [port]   (varsayılan 8091)
"""

from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = os.environ.get("E2E_FAKE_MODEL", "e2e-senaryo")   # tanıtım görüntülerinde gerçekçi ad

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


# ---------------------------------------------------------------- [detay] senaryosu: hata ve ret yolları
# İlk mesajda "[detay]" geçen raporda model sık yapılan hataları sırayla yapar; harness'in bunları yakalayıp
# kullanıcıya doğru yansıttığı test edilir. Faz değişince LLM geçmişi sıfırlandığı için sonraki fazlarda senaryo,
# sistem talimatındaki kayıtlı gereksinimlerden tanınır.
DETAIL_TITLE = "E2E Detay Raporu"
DETAIL_MARK = "E2E-DETAY"      # iş hedefinde: rapor adı kullanıcı tarafından değiştirilebilir
REL_DUE = {"from_table": "dbo.FactResellerSales", "to_table": "dbo.DimDate", "columns": [["DueDate", "FullDateAlternateKey"]],
           "cardinality": "N:1", "description": "Vade tarihi (tarih)"}
BAD_COL_SQL = "SELECT SUM(f.SalesAmountX) AS sales_amount FROM dbo.FactResellerSales f"
DELETE_SQL = "DELETE FROM dbo.FactResellerSales WHERE 1 = 0"
REGION_BAD_SQL = REGION_SQL.replace("SUM(f.SalesAmount)", "SUM(f.OlmayanKolon)")
YEAR_SQL = ("SELECT d.CalendarYear AS sales_year, SUM(f.SalesAmount) AS sales_amount FROM dbo.FactResellerSales f "
            "JOIN dbo.DimDate d ON d.FullDateAlternateKey = f.OrderDate GROUP BY d.CalendarYear")
TYPE_SQL = ("SELECT r.BusinessType AS business_type, SUM(f.SalesAmount) AS sales_amount FROM dbo.FactResellerSales f "
            "JOIN dbo.DimReseller r ON r.ResellerKey = f.ResellerKey GROUP BY r.BusinessType")
MONEY = {"name": "sales_amount", "label": "Satış Tutarı", "format": "currency"}
DETAIL_FILTERS = [{"id": "f_region", "label": "Bölge Grubu", "table": "dbo.DimSalesTerritory", "column": "SalesTerritoryGroup"}]


def detail_spec(bad: bool) -> dict:
    return {"title": DETAIL_TITLE, "filters": DETAIL_FILTERS, "visuals": [
        {"id": "d_sales", "type": "kpi", "title": "Satış Tutarı", "datasetId": "kpi", "encoding": {"value": "sales_amount"},
         "options": {"format": "compact"}},
        {"id": "d_orders", "type": "kpi", "title": "Sipariş Sayısı", "datasetId": "kpi", "encoding": {"value": "order_count"}},
        {"id": "d_region", "type": "bar", "title": "Bölgeye Göre", "datasetId": "region",
         "encoding": {"x": "region_group", "y": ["sales_amount"]}},
        {"id": "d_year", "type": "line", "title": "Yıllık Satış", "datasetId": "yearly",
         "encoding": {"x": "olmayan_kolon" if bad else "sales_year", "y": ["sales_amount"]}}]}


def tool_results(messages: list[dict]) -> list[tuple[str, bool]]:
    """Bu fazdaki araç sonuçları: (araç adı, başarılı mı)."""
    out = []
    for m in messages:
        if m.get("role") == "tool":
            c = str(m.get("content") or "")
            out.append((m.get("name") or "", not ('"error' in c or '"ok": false' in c)))
    return out


def is_detail(messages: list[dict]) -> bool:
    sys_text = next((m.get("content") or "" for m in messages if m.get("role") == "system"), "")
    return DETAIL_MARK in sys_text or any("[detay]" in str(m.get("content") or "") for m in messages if m.get("role") == "user")


def decide_detail(messages: list[dict]) -> dict:
    phase, last = phase_of(messages), messages[-1]
    res = tool_results(messages)
    calls = lambda n: sum(1 for t, _ in res if t == n)            # noqa: E731
    fresh = last.get("role") == "user" and not str(last.get("content") or "").startswith("[HARNESS]")
    u = str(last.get("content") or "").lower() if fresh else ""
    prev, prev_ok = (res[-1] if res and last.get("role") == "tool" else ("", True))

    if phase == "requirements":
        if prev == "save_requirements" and not prev_ok:
            return say("Hangi KPI'ları görmek istersiniz? Örneğin satış tutarı, sipariş sayısı.")
        # ilk tur: önce bu fazda kapalı bir araç (atlanmalı, hata gibi görünmemeli), sonra uydurma argümanla tablo ayrıntısı
        # (table_name → tables kabul edilmeli), sonra KPI'sız kayıt (reddedilmeli)
        if fresh and calls("run_sql") == 0:
            return call("run_sql", {"sql": "SELECT TOP 5 * FROM dbo.DimReseller"})
        if prev == "run_sql":
            return call("get_table_details", {"table_name": "dbo.DimReseller"})
        if fresh or prev == "get_table_details":
            req = {"report_title": DETAIL_TITLE, "business_goal": f"Bayi satışlarını izlemek ({DETAIL_MARK})", "audience": "Satış yönetimi",
                   "dimensions": ["Bölge grubu", "Yıl"], "time_range": "Tüm dönem"}
            # ilk turda KPI'sız kayıt (harness reddetmeli), kullanıcı KPI'ları söyleyince tam kayıt
            return call("save_requirements", {**req, "kpis": [] if calls("save_requirements") == 0 else ["Satış tutarı", "Sipariş sayısı"]})
        return say("Gereksinimler tamam.")

    if phase == "data":
        if "hayır" in u:
            return call("propose_model", {"tables": ["dbo.FactResellerSales", "dbo.DimSalesTerritory", "dbo.DimDate", "dbo.DimReseller"]})
        if "evet" in u:
            return call("save_relationships", {"scope": "global" if "ortak" in u else "report", "relationships": [REL_DUE]})
        if "düzelt" in u:
            return call("save_datasets", {"datasets": [
                {"id": "region", "description": "Bölge grubu kırılımı", "sql": REGION_SQL,
                 "fields": [{"name": "region_group", "label": "Bölge Grubu"}, MONEY]},
                {"id": "yearly", "description": "Yıllık trend", "sql": YEAR_SQL,
                 "fields": [{"name": "sales_year", "label": "Yıl"}, MONEY]}]})
        if calls("search_dictionary") == 0:
            return call("search_dictionary", {"query": "bayi satış bölge"})
        if calls("propose_model") == 0:
            return call("propose_model", {"tables": ["dbo.FactResellerSales", "dbo.DimSalesTerritory", "dbo.DimDate"]})
        if prev == "propose_model" and calls("save_relationships") == 0:
            return call("save_relationships", {"scope": "global", "relationships": [REL_DUE]})   # onaysız: reddedilmeli
        if prev in ("propose_model", "save_relationships") and not prev_ok or prev == "propose_model":
            return say("Önerdiğim tablolar: dbo.FactResellerSales, dbo.DimSalesTerritory, dbo.DimDate. Yeni ilişki: "
                       "FactResellerSales.DueDate → DimDate (N:1). Bu tablolarla devam edeyim mi? İlişki ortak modele mi, "
                       "yalnız bu rapora mı kaydedilsin?")
        if prev == "save_relationships" and prev_ok:
            return call("run_sql", {"sql": BAD_COL_SQL, "purpose": "toplam satış"})
        if prev == "run_sql" and not prev_ok:
            return call("run_sql", {"sql": DELETE_SQL if calls("run_sql") == 1 else REGION_SQL, "purpose": "deneme"})
        if prev == "run_sql" and prev_ok:
            return call("save_datasets", {"datasets": [
                {"id": "kpi", "description": "KPI özeti", "sql": KPI_SQL,
                 "fields": [MONEY, {"name": "order_count", "label": "Sipariş Sayısı", "format": "number"}]},
                {"id": "region", "description": "Bölge grubu kırılımı", "sql": REGION_BAD_SQL,
                 "fields": [{"name": "region_group", "label": "Bölge Grubu"}, MONEY]}]})
        if prev == "save_datasets" and not prev_ok:
            return say("KPI veri kümesi kaydedildi; bölge kırılımı hatalı kolon nedeniyle kaydedilemedi. Düzeltip yıllık trendle "
                       "birlikte kaydedeyim mi?")
        return say("Veri hazır.")

    # tasarım
    if "varsayılan" in u:
        return call("create_report_spec", {"spec": detail_spec(bad=True)})        # bilinmeyen kolon: reddedilmeli
    if prev == "create_report_spec" and not prev_ok:
        return call("create_report_spec", {"spec": detail_spec(bad=False)})
    if "bayi türü" in u and "filtre" in u:
        return call("update_report", {"filters": [*DETAIL_FILTERS, {"id": "f_type", "label": "Bayi Türü", "table": "dbo.DimReseller",
                                                                    "column": "BusinessType"}]})
    if "bayi türü" in u:
        return call("add_dataset", {"id": "by_type", "description": "Bayi türü kırılımı", "sql": TYPE_SQL,
                                    "fields": [{"name": "business_type", "label": "Bayi Türü"}, MONEY]})
    if prev == "add_dataset" and prev_ok:
        return call("add_visual", {"visual": {"id": "d_type", "type": "bar", "title": "Bayi Türüne Göre", "datasetId": "by_type",
                                              "encoding": {"x": "business_type", "y": ["sales_amount"]}}})
    if prev:
        return say("İsteğiniz işlendi; sonucu sağdaki panelde görebilirsiniz.")
    return say("Veriler hazır: kpi, region ve yearly veri kümeleri. Nasıl bir tasarım istersiniz? Tarif edebilir ya da "
               "'varsayılan tasarımla başla' diyebilirsiniz.")


def decide(messages: list[dict]) -> dict:
    if is_detail(messages):
        return decide_detail(messages)
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

    # tasarım — düzenleme senaryoları: son kullanıcı mesajındaki anahtar kelimeye göre (demo dashboard üzerinde)
    if last.get("role") == "user":
        u = user.lower()
        if "halka" in u:
            return call("update_visual", {"id": "country_bar", "changes": {
                "type": "donut", "encoding": {"category": "territory_group", "value": "sales_amount", "x": None, "y": None}}})
        if "taşı" in u:      # "yeni sayfaya taşı" da "yeni sayfa" içerir: önce taşıma
            return call("update_visual", {"id": "product_table", "changes": {"page": "detay"}})
        if "yeni sayfa" in u:
            return call("add_page", {"id": "detay", "title": "Ürün Detayı", "first_title": "Özet"})
        if "koyu" in u:
            return call("update_report", {"theme": {"mode": "dark"}})
        if "fontsize" in u:      # desteklenmeyen alan: uygulanmamalı, model "yaptım" diyememeli
            return call("update_visual", {"id": "kpi_sales", "changes": {"options": {"fontSize": 40, "valueColor": "#ff0000"}}})
        if "karışık" in u:      # desteklenen + desteklenmeyen
            return call("update_visual", {"id": "kpi_sales", "changes": {"options": {"color": "#dc2626", "labelPosition": "top"}}})
        if "lacivert" in u:
            return call("update_visual", {"id": "kpi_orders", "changes": {"options": {"background": "#0f172a", "valueSize": "xl"}}})
        if "kaldır" in u:
            return call("remove_visual", {"ids": ["kpi_customers"]})
    if last.get("role") == "tool" and tools and tools[-1] in ("add_page", "update_report", "remove_visual") \
            or (last.get("role") == "tool" and tools and tools[-1] == "update_visual" and "yeşil" not in user.lower()):
        return say("İsteğiniz işlendi; sonucu sağdaki panelde görebilirsiniz.")
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
