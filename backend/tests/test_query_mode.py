"""Sorgu modu: kullanıcının hazır SQL'iyle önizleme ve dataset kaydı → tasarım fazı."""

import json

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app.harness.phases import system_prompt
from app.harness.session import SessionStore

KPI_SQL = ("SELECT SUM(f.SalesAmount) AS sales_amount, COUNT(DISTINCT f.SalesOrderNumber) AS order_count "
           "FROM dbo.FactInternetSales f")
REGION_SQL = ("SELECT t.SalesTerritoryRegion AS region, SUM(f.SalesAmount) AS sales_amount FROM dbo.FactInternetSales f "
              "JOIN dbo.DimSalesTerritory t ON t.SalesTerritoryKey = f.SalesTerritoryKey GROUP BY t.SalesTerritoryRegion")


@pytest.fixture()
def client(settings, services, monkeypatch):
    m.state.services = services
    m.state.store = SessionStore(settings.sessions_dir)
    monkeypatch.setattr(m, "get_settings", lambda: settings)
    return TestClient(m.app)


def _audit(settings, event):
    if not settings.audit_log.exists():
        return []
    return [r for r in map(json.loads, settings.audit_log.read_text(encoding="utf-8").splitlines()) if r.get("event") == event]


def test_drafts_and_mode_persist(client):
    s = m.state.store.create("standart")
    r = client.put(f"/api/sessions/{s.id}/query-drafts", json={"mode": "query", "drafts": [{"id": "bolge", "title": "Bölge", "sql": REGION_SQL}]})
    assert r.status_code == 200 and r.json()["data_mode"] == "query" and r.json()["query_drafts"][0]["id"] == "bolge"
    assert m.state.store.get(s.id).query_drafts[0].sql == REGION_SQL
    assert client.put(f"/api/sessions/{s.id}/query-drafts", json={"mode": "x"}).status_code == 400


def test_preview_runs_with_dataset_validation(client, settings):
    s = m.state.store.create("standart")
    ok = client.post(f"/api/sessions/{s.id}/query-preview", json={"sql": REGION_SQL}).json()
    assert ok["ok"] and ok["columns"] == ["region", "sales_amount"] and ok["rows"]
    bad = client.post(f"/api/sessions/{s.id}/query-preview", json={"sql": "SELECT * FROM dbo.YokBoyleTablo"}).json()
    assert not bad["ok"] and bad["errors"]
    dml = client.post(f"/api/sessions/{s.id}/query-preview", json={"sql": "DELETE FROM dbo.DimProduct"}).json()
    assert not dml["ok"]
    assert len(_audit(settings, "query_preview")) == 3


def test_from_query_saves_datasets_and_moves_to_design(client, settings, services):
    s = m.state.store.create("standart")
    body = {"datasets": [{"id": "kpi_ozet", "title": "Özet", "sql": KPI_SQL},
                         {"id": "bolge_satis", "title": "Bölge satışları", "sql": REGION_SQL}]}
    r = client.post(f"/api/sessions/{s.id}/datasets/from-query", json=body)
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["phase"] == "design" and [d["id"] for d in st["datasets"]] == ["kpi_ozet", "bolge_satis"]
    assert st["requirements"]["report_title"] == "Özet" and st["title"] == "Özet" and st["query_drafts"][1]["id"] == "bolge_satis"
    s = m.state.store.get(s.id)
    assert "hazır SQL" in s.llm_messages[-1]["content"] and s.dataset_profiles["bolge_satis"]["columns"]
    assert "Sorgu modundan 2 veri kümesi" in s.transcript[-1].content
    assert system_prompt(s, "tsql")          # gereksinim sohbeti yapılmadan tasarım talimatı oluşur
    rec = _audit(settings, "datasets_from_query")
    assert rec and rec[0]["datasets"] == ["kpi_ozet", "bolge_satis"] and rec[0]["tables"]


def test_from_query_kpi_prefix_multi_row_allowed_for_user_sql(client):
    s = m.state.store.create("standart")
    r = client.post(f"/api/sessions/{s.id}/datasets/from-query", json={"datasets": [{"id": "kpi_bolge", "sql": REGION_SQL}]})
    assert r.status_code == 200, r.text


def test_from_query_errors_return_422_and_keep_phase(client):
    s = m.state.store.create("standart")
    r = client.post(f"/api/sessions/{s.id}/datasets/from-query",
                    json={"datasets": [{"id": "ok_ds", "sql": REGION_SQL}, {"id": "Kotu Ad", "sql": REGION_SQL}]})
    assert r.status_code == 422 and any("snake_case" in e for e in r.json()["detail"])
    r = client.post(f"/api/sessions/{s.id}/datasets/from-query",
                    json={"datasets": [{"id": "a", "sql": REGION_SQL}, {"id": "a", "sql": KPI_SQL}]})
    assert r.status_code == 422
    s = m.state.store.get(s.id)
    assert s.phase == "requirements" and not s.datasets


def test_from_query_in_design_adds_dataset_without_reset(client):
    s = m.state.store.create("standart")
    assert client.post(f"/api/sessions/{s.id}/datasets/from-query", json={"datasets": [{"id": "bolge", "sql": REGION_SQL}]}).status_code == 200
    s = m.state.store.get(s.id)
    s.llm_messages.append({"role": "assistant", "content": "tasarım konuşması"})
    m.state.store.save(s)
    r = client.post(f"/api/sessions/{s.id}/datasets/from-query", json={"datasets": [{"id": "ozet", "sql": KPI_SQL}]})
    assert r.status_code == 200
    s = m.state.store.get(s.id)
    assert {d.id for d in s.datasets} == {"bolge", "ozet"} and s.phase == "design"
    assert any(x.get("content") == "tasarım konuşması" for x in s.llm_messages), "tasarım fazındaki bağlam silinmemeli"
