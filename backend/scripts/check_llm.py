"""Kurum LLM sunucusunun bu harness için uygunluğunu test eder.

    python scripts/check_llm.py

Kontroller: bağlantı + model listesi, düz sohbet, native tool calling, prompt-modu tool calling,
Türkçe yanıt, (varsa) vision model. Sonunda önerilen LLM_TOOL_MODE yazdırılır.
"""

from __future__ import annotations

import base64
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.llm.gateway import LLMError, LLMGateway  # noqa: E402

TOOLS = [{"type": "function", "function": {
    "name": "search_dictionary", "description": "Veri sözlüğünde iş terimiyle tablo arar.",
    "parameters": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}}}}]
MSGS = [{"role": "system", "content": "Sen bir BI asistanısın. Veri hakkında bilgi gerekiyorsa araç kullan."},
        {"role": "user", "content": "Kredi kartı satış tutarı hangi tabloda? Veri sözlüğüne bak."}]


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")


def main() -> int:
    s = get_settings()
    print(f"LLM: {s.llm_model} @ {s.llm_base_url}")
    gw = LLMGateway(s)
    h = gw.health()
    if not h["reachable"]:
        fail(f"Sunucuya erişilemiyor: {h.get('error')}")
        return 1
    ok(f"Sunucu erişilebilir. Modeller: {', '.join(h.get('available_models', [])[:5])}")
    if h.get("error"):
        fail(h["error"])

    t0 = time.perf_counter()
    try:
        r = gw.chat([{"role": "user", "content": "Tek cümleyle: BI nedir?"}])
        ok(f"Sohbet ({time.perf_counter() - t0:.1f} sn): {r.content[:100]!r}")
    except LLMError as e:
        fail(f"Sohbet başarısız: {e}")
        return 1

    results = {}
    for mode in ("native", "prompt"):
        gw.tool_mode = mode
        t0 = time.perf_counter()
        try:
            r = gw.chat(MSGS, TOOLS)
            good = bool(r.tool_calls) and r.tool_calls[0].name == "search_dictionary" and "query" in r.tool_calls[0].arguments
            results[mode] = good
            (ok if good else fail)(f"{mode} tool calling ({time.perf_counter() - t0:.1f} sn): "
                                   f"{[(c.name, c.arguments) for c in r.tool_calls] or r.content[:120]}")
        except LLMError as e:
            results[mode] = False
            fail(f"{mode} tool calling hata: {e}")

    if s.vision_model:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (400, 240), "#0f172a")
        d = ImageDraw.Draw(img)
        for i, (hgt, col) in enumerate([(120, "#22d3ee"), (180, "#a78bfa"), (90, "#f472b6")]):
            d.rectangle([60 + i * 100, 220 - hgt, 120 + i * 100, 220], fill=col)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        try:
            out = gw.vision("Bu görselde hangi grafik türü var ve arka plan açık mı koyu mu? Kısa yanıt ver.", [url])
            ok(f"Vision ({s.vision_model}): {out[:120]!r}")
        except LLMError as e:
            fail(f"Vision: {e}")
    else:
        print("  - VISION_MODEL tanımlı değil (örnek görsellerden yalnızca renkler çıkarılır)")

    rec = "native" if results.get("native") else "prompt" if results.get("prompt") else None
    print()
    if rec:
        print(f"Önerilen ayar: LLM_TOOL_MODE={rec}")
        return 0
    print("Model araç çağıramıyor. Daha büyük/instruct bir model deneyin (ör. Qwen3-32B, Qwen2.5-72B-Instruct, Llama-3.3-70B).")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
