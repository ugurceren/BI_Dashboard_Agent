"""Uçtan uca testler için izole backend: kendi geçici oturum / platform / ilişki / bağlantı dosyalarıyla çalışır,
bu bilgisayardaki gerçek raporlara ve ayarlara dokunmaz. LLM olarak senaryolu sahte sunucuyu (fake_llm.py) kullanır.

Kullanım: python run_backend.py <port> <desktop|server> [--real-llm]
  --real-llm: LLM ayarı bu bilgisayarın Bağlantı Ayarları'ndan (connections.json) okunur (gerçek LLM duman testi).
Tek süreç (uvicorn bu süreçte çalışır): Playwright kapatınca geride sunucu kalmaz.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

E2E = Path(__file__).resolve().parent
BACKEND = E2E.parents[1] / "backend"


def warm_up() -> None:
    """Bu PC'deki SQL Server'da bir sorgunun İLK çalışması 10-70 sn sürebiliyor (sonrakiler < 1 sn). Testler bu soğuk
    başlangıca takılmasın diye senaryo ve demo sorguları sunucu açılmadan önce bir kez çalıştırılır (yalnız okuma)."""
    import json
    sys.path.insert(0, str(E2E))
    try:
        from app.config import get_settings
        from app.data.connector import create_connector
        from fake_llm import KPI_SQL, MONTH_SQL, REGION_SQL
        con = create_connector(get_settings())
        demo = json.loads((BACKEND.parent / "docs" / "demo_spec.json").read_text(encoding="utf-8"))
        for sql in [KPI_SQL, REGION_SQL, MONTH_SQL, *(d["sql"] for d in demo["datasets"])]:
            try:
                con.execute(sql, 1)
            except Exception as e:  # noqa: BLE001
                print(f"[warm-up] {str(e)[:120]}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 — ısınma olmasa da testler çalışır (daha yavaş)
        print(f"[warm-up] atlandı: {e}", file=sys.stderr)


def main() -> None:
    port, mode = int(sys.argv[1]), sys.argv[2]
    real_llm = "--real-llm" in sys.argv
    tmp = E2E / ".tmp" / f"{mode}-{port}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    env = {
        "SESSIONS_DIR": str(tmp / "sessions"), "META_SQLITE": str(tmp / "platform.db"),
        "MODEL_RELATIONSHIPS": str(tmp / "model_relationships.json"), "VIEWS_REGISTRY": str(tmp / "views.json"),
        "VIEW_SCRIPTS_DIR": str(tmp / "view_scripts"), "AUDIT_LOG": str(tmp / "audit.jsonl"),
        "PLATFORM_MODE": mode, "PLATFORM_ADMINS": "KURUM\\admin" if mode == "server" else "",
        # bu PC'deki SQL Server ilk (soğuk) sorguda yavaş; iki backend aynı anda açılırken 30 sn sınırı aşılabiliyor
        "QUERY_TIMEOUT_S": "180",
    }
    if real_llm and os.environ.get("E2E_LLM_SOURCE") == "env":
        # LLM .env'den (ör. EVREN, geniş bağlam): Bağlantı Ayarları (Spark) yok sayılır; E2E_LLM_MODEL modeli seçer
        env["BI_CONNECTIONS_FILE"] = str(tmp / "connections.json")       # yok: .env geçerli
        if os.environ.get("E2E_LLM_MODEL"):
            env["LLM_MODEL"] = os.environ["E2E_LLM_MODEL"]
    elif real_llm:
        src = BACKEND / "config" / "connections.json"   # gerçek LLM ayarı (yalnız okunur kopya)
        if src.exists():
            shutil.copy(src, tmp / "connections.json")
            env["BI_CONNECTIONS_FILE"] = str(tmp / "connections.json")
            if os.environ.get("E2E_LLM_MODEL"):   # ör. Spark'ta daha geniş bağlamlı model: yalnız geçici kopyada değişir
                conf = json.loads((tmp / "connections.json").read_text(encoding="utf-8"))
                conf.setdefault("llm", {})["model"] = os.environ["E2E_LLM_MODEL"]
                (tmp / "connections.json").write_text(json.dumps(conf, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        env |= {"BI_CONNECTIONS_FILE": str(tmp / "connections.json"),     # yok: veri bağlantısı .env (AdventureWorksDW)
                "LLM_BASE_URL": "http://127.0.0.1:8091/v1", "LLM_MODEL": "e2e-senaryo", "LLM_API_KEY": "e2e",
                "LLM_TOOL_MODE": "native", "LLM_CONTEXT_TOKENS": "32768", "VISION_MODEL": ""}
    os.environ.update(env)
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))
    warm_up()
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
