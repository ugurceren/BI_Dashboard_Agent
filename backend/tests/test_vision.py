import base64
import io

from PIL import Image, ImageDraw

from app.harness.vision import analyze_design_images, extract_colors


def _dark_dashboard() -> Image.Image:
    img = Image.new("RGB", (800, 500), "#0b1220")
    d = ImageDraw.Draw(img)
    for i in range(4):
        d.rounded_rectangle([20 + i * 195, 20, 195 + i * 195, 120], 12, fill="#141c2f")
    for i, (h, c) in enumerate([(200, "#22d3ee"), (260, "#a78bfa"), (150, "#f472b6"), (230, "#22d3ee")]):
        d.rectangle([60 + i * 90, 470 - h, 120 + i * 90, 470], fill=c)
    return img


def test_extract_colors_dark():
    c = extract_colors(_dark_dashboard())
    assert c["mode"] == "dark"
    assert c["background"].startswith("#0")
    assert len(c["palette"]) >= 3


def test_analyze_without_vision_model():
    buf = io.BytesIO()
    _dark_dashboard().save(buf, "PNG")
    url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    class NoVision:
        vision_enabled = False

    brief, notes = analyze_design_images(NoVision(), [url], "buna benzesin")
    assert brief.mode == "dark" and brief.palette and brief.accent == brief.palette[0]
    assert any("VISION_MODEL" in n for n in notes)
