"""Örnek dashboard görselinden tasarım özeti (DesignBrief) çıkarır.

İki katman:
  1. Piksel analizi (Pillow, deterministik): arka plan, açık/koyu mod, baskın vurgu renkleri.
     Renkleri küçük vision modelleri uydurabilir; pikselden okumak her zaman daha doğrudur.
  2. Vision model (varsa): yerleşim, grafik türleri, stil notları.
Ana model multimodal olmak zorunda değildir; ona yalnızca bu metin özeti verilir.
"""

from __future__ import annotations

import base64
import colorsys
import io
import logging
from typing import Any

from PIL import Image

from app.harness.session import DesignBrief
from app.llm.gateway import LLMError, LLMGateway, parse_json_loose

log = logging.getLogger(__name__)

VISION_PROMPT = """Bu bir BI dashboard örneği. Tasarımını başka bir dashboard'a uygulamak için analiz et.
Sadece aşağıdaki JSON'u döndür, başka metin yazma:
{
  "summary": "tasarımın 2-3 cümlelik Türkçe özeti",
  "mode": "light" veya "dark",
  "layout": "yerleşimin tarifi: üstte kaç KPI kartı, grafiklerin dizilişi, kolon sayısı, yan menü var mı",
  "chart_types": ["görülen grafik türleri: kpi, line, area, bar, donut, pie, table, gauge, heatmap, matrix (pivot tablo), treemap, funnel, scatter"],
  "style_notes": ["kart stili (gölgeli/çerçeveli/düz), köşe yuvarlaklığı, yoğunluk, yazı tipi karakteri, başlık alanı gibi notlar"]
}"""


def _decode(data_url: str) -> Image.Image:
    b64 = data_url.split(",", 1)[1] if data_url.startswith("data:") else data_url
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _lum(rgb: tuple[int, int, int]) -> float:
    r, g, b = (c / 255 for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def extract_colors(img: Image.Image) -> dict[str, Any]:
    small = img.copy()
    small.thumbnail((240, 240))
    q = small.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette() or []
    counts = sorted(q.getcolors() or [], reverse=True)
    total = sum(c for c, _ in counts) or 1
    colors = [((pal[i * 3], pal[i * 3 + 1], pal[i * 3 + 2]), c / total) for c, i in counts]
    background = colors[0][0]
    dark = _lum(background) < 0.4
    accents = []
    for rgb, share in colors:
        h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
        if s > 0.35 and 0.2 < l < 0.8 and share > 0.002:
            if all(sum(abs(a - b) for a, b in zip(rgb, other)) > 60 for other, _ in accents):
                accents.append((rgb, share))
    surface = next((rgb for rgb, share in colors[1:] if abs(_lum(rgb) - _lum(background)) < 0.15 and share > 0.03), background)
    return {
        "mode": "dark" if dark else "light",
        "background": _hex(background),
        "surface": _hex(surface),
        "palette": [_hex(rgb) for rgb, _ in accents[:7]],
    }


def analyze_design_images(gateway: LLMGateway, images: list[str], user_text: str) -> tuple[DesignBrief, list[str]]:
    """(brief, notlar) döndürür. Notlar UI'da araç adımı olarak gösterilir."""
    notes: list[str] = []
    try:
        img = _decode(images[0])
        colors = extract_colors(img)
        notes.append(f"Renkler pikselden çıkarıldı: {colors['mode']} tema, arka plan {colors['background']}, "
                     f"{len(colors['palette'])} vurgu rengi")
    except Exception as e:  # noqa: BLE001
        log.warning("Görsel çözümlenemedi: %s", e)
        colors = {}
        notes.append("Görsel dosyası okunamadı.")

    brief = DesignBrief(summary="", mode=colors.get("mode"), background=colors.get("background"),
                        palette=colors.get("palette") or None,
                        accent=(colors.get("palette") or [None])[0])
    if gateway.vision_enabled:
        try:
            raw = gateway.vision(VISION_PROMPT + (f"\n\nKullanıcının notu: {user_text}" if user_text else ""), images[:2])
            data = parse_json_loose(raw)
            if isinstance(data, dict):
                brief.summary = str(data.get("summary") or "")
                brief.layout = data.get("layout")
                brief.chart_types = [str(c) for c in data.get("chart_types") or []] or None
                brief.style_notes = [str(c) for c in data.get("style_notes") or []] or None
                if not brief.mode and data.get("mode") in ("light", "dark"):
                    brief.mode = data["mode"]
                notes.append(f"Vision model yerleşimi analiz etti ({gateway.s.vision_model})")
        except (LLMError, ValueError) as e:
            notes.append(f"Vision model kullanılamadı: {str(e)[:120]}")
    else:
        notes.append("VISION_MODEL tanımlı değil: yalnızca renkler kullanılacak; yerleşimi metinle tarif etmeniz faydalı olur.")
    if not brief.summary:
        brief.summary = (f"Örnek görsel: {brief.mode or 'açık'} tema, arka plan {brief.background}, vurgu renkleri {brief.palette}."
                         + (f" Kullanıcı notu: {user_text}" if user_text else ""))
    return brief, notes
