"""KPI / tema okunabilirlik kuralları: model okunmayan renk seçerse kayıttan önce düzeltilir ve not döner."""

from app.harness.tools import _contrast, _readability_rules


def _kpi(**opts):
    return {"id": "k", "type": "kpi", "position": {"x": 0, "y": 0, "w": opts.pop("w", 4), "h": 2}, "options": opts}


def test_value_color_unreadable_on_card_background_is_lightened():
    raw = {"theme": {}, "visuals": [_kpi(background="#1e2a4a", color="#1e3a8a", accentBar=True)]}
    notes = _readability_rules(raw)
    col = raw["visuals"][0]["options"]["color"]
    assert col != "#1e3a8a" and _contrast(col, "#1e2a4a") >= 3 and any("değer / şerit rengi" in n for n in notes)


def test_text_color_unreadable_is_dropped_for_auto():
    raw = {"theme": {}, "visuals": [_kpi(background="lacivert", textColor="#1f2937")]}
    notes = _readability_rules(raw)
    assert "textColor" not in raw["visuals"][0]["options"] and any("yazı rengi" in n for n in notes)


def test_readable_choices_are_kept():
    raw = {"theme": {}, "visuals": [_kpi(background="#1e3a8a", textColor="#ffffff", color="#fbbf24", valueSize="xl")]}
    assert _readability_rules(raw) == []
    assert raw["visuals"][0]["options"] == {"background": "#1e3a8a", "textColor": "#ffffff", "color": "#fbbf24", "valueSize": "xl"}


def test_xl_value_on_narrow_card_becomes_lg():
    raw = {"theme": {}, "visuals": [_kpi(valueSize="xl", w=2)]}
    _readability_rules(raw)
    assert raw["visuals"][0]["options"]["valueSize"] == "lg"


def test_dark_theme_with_dark_muted_text_is_fixed():
    raw = {"theme": {"mode": "dark", "surface": "#111a2e", "text": "#1f2937", "mutedText": "#334155"}, "visuals": []}
    notes = _readability_rules(raw)
    t = raw["theme"]
    assert t["text"] == "#e5e7eb" and _contrast(t["mutedText"], "#111a2e") >= 3 and len(notes) == 2
