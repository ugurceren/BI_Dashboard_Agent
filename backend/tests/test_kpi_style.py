"""KPI kart stili (renk, zemin, yazı boyutu, şerit) ve şemada olmayan alanların modele bildirilmesi:
uydurulan bir alan sessizce atılınca model 'değiştirdim' deyip ön yüz aynı kalıyordu."""

import pytest

from app.harness.session import Session
from app.harness.tools import ToolContext, h_create_report_spec, h_update_report, h_update_visual
from app.spec.models import Dataset, DatasetField


@pytest.fixture()
def ctx(services):
    ds = [Dataset(id="kpi", sql="SELECT 1 AS amount", fields=[DatasetField(name="amount", type="number")])]
    c = ToolContext(Session(phase="design", datasets=ds), services)
    r = h_create_report_spec(c, {"spec": {"title": "Satış", "visuals": [
        {"id": "k1", "type": "kpi", "title": "Tutar", "datasetId": "kpi", "encoding": {"value": "amount"}}]}})
    assert r.ok, r.content
    return c


def _kpi(ctx):
    return ctx.session.spec.visuals[0].options


def test_kpi_style_options_are_applied(ctx):
    r = h_update_visual(ctx, {"id": "k1", "changes": {"options": {
        "color": "#16a34a", "background": "#0f172a", "textColor": "#f8fafc", "valueSize": "xl", "accentBar": True}}})
    assert r.ok, r.content
    o = _kpi(ctx)
    assert (o.color, o.background, o.textColor, o.valueSize, o.accentBar) == ("#16a34a", "#0f172a", "#f8fafc", "xl", True)
    assert "uygulanmadı" not in r.summary


def test_unsupported_only_change_is_reported_not_claimed(ctx):
    v = ctx.session.spec_version
    r = h_update_visual(ctx, {"id": "k1", "changes": {"options": {"fontSize": 40, "valueColor": "#ff0000"}}})
    assert not r.ok
    text = str(r.content)
    assert "options.fontSize" in text and "options.valueColor" in text and "DEĞİŞMEDİ" in text
    assert ctx.session.spec_version == v                       # spec dokunulmadı


def test_mixed_change_applies_supported_and_warns(ctx):
    r = h_update_visual(ctx, {"id": "k1", "changes": {"options": {"color": "#dc2626", "labelPosition": "top"}}})
    assert r.ok and "uygulanmadı" in r.summary
    assert any("options.labelPosition" in n for n in r.content["notes"])
    assert _kpi(ctx).color == "#dc2626"


def test_bad_color_is_rejected(ctx):
    r = h_update_visual(ctx, {"id": "k1", "changes": {"options": {"background": "url(x)"}}})
    assert not r.ok and "hex" in str(r.content)


def test_unknown_theme_field_reported(ctx):
    r = h_update_report(ctx, {"theme": {"accent": "#7c3aed", "kpiGlow": True}})
    assert "theme.kpiGlow" in str(r.content)


def test_update_report_mode_switch_resets_surface_colors(ctx):
    """Açık temadaki rapor "koyu yap" isteğiyle gerçekten koyulaşır; tekrar açığa dönünce açık renkler gelir."""
    r = h_update_report(ctx, {"theme": {"mode": "dark"}})
    assert r.ok, r.content
    t = ctx.session.spec.theme
    assert t.mode == "dark" and t.background == "#0b1220" and t.text == "#e5e7eb"
    r = h_update_report(ctx, {"theme": {"mode": "light", "background": "#fafafa"}})
    assert r.ok, r.content
    t = ctx.session.spec.theme
    assert t.background == "#fafafa" and t.text == "#0f172a" and t.surface == "#ffffff"
