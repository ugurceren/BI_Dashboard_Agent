"""Model bağlamını temizle: konuşma geçmişi sıfırlanır, kayıtlı durum ve doğrulanmış sorgular korunur."""

import json

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app.harness.session import Requirements, SessionStore


@pytest.fixture()
def client(settings, services, monkeypatch):
    m.state.services = services
    m.state.store = SessionStore(settings.sessions_dir)
    monkeypatch.setattr(m, "get_settings", lambda: settings)
    return TestClient(m.app)


def test_reset_context_keeps_state_and_verified_sql(client, settings):
    s = m.state.store.create("standart")
    s.requirements = Requirements(report_title="Satış")
    s.llm_messages = [{"role": "user", "content": "x" * 500}, {"role": "assistant", "content": "y" * 300}]
    s.phase_memory = {"verified_sql": [{"sql": "SELECT 1"}], "repeats": 2, "calls": {"a": 1}, "fail_streak": {"run_sql": 3}}
    m.state.store.save(s)
    assert m.state.store.get(s.id).public()["llm_context_chars"] > 800

    r = client.post(f"/api/sessions/{s.id}/reset-context")
    assert r.status_code == 200 and r.json()["llm_context_chars"] == 0
    s = m.state.store.get(s.id)
    assert s.llm_messages == [] and s.requirements.report_title == "Satış"
    assert s.phase_memory == {"verified_sql": [{"sql": "SELECT 1"}]}
    assert "Model bağlamı temizlendi" in s.transcript[-1].content
    rec = [json.loads(x) for x in settings.audit_log.read_text(encoding="utf-8").splitlines() if '"context_reset"' in x]
    assert rec and rec[0]["messages"] == 2


def test_reset_context_refused_while_turn_running(client):
    s = m.state.store.create("standart")
    lock = m.state.store.lock(s.id)
    lock.acquire()
    try:
        assert client.post(f"/api/sessions/{s.id}/reset-context").status_code == 409
    finally:
        lock.release()
