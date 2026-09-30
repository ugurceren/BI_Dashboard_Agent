"""BI Lens ortam kontrolü: backend neden açılmıyor?

    backend\\.venv\\Scripts\\python -X utf8 scripts\\doctor.py            # tam kontrol (veritabanı + LLM dahil)
    backend\\.venv\\Scripts\\python -X utf8 scripts\\doctor.py --quick    # açılış öncesi hızlı kontrol (start.bat)
    backend\\.venv\\Scripts\\python -X utf8 scripts\\doctor.py --port 8010

Her kontrol [ OK ] / [UYARI] / [HATA] olarak yazılır; en az bir HATA varsa çıkış kodu 1'dir.
"""

from __future__ import annotations

import argparse
import importlib
import os
import socket
import struct
import subprocess
import sys
import traceback
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

errors = 0


def say(level: str, msg: str, hint: str = "") -> None:
    global errors
    tag = {"ok": "[ OK ]", "warn": "[UYARI]", "err": "[HATA]"}[level]
    if level == "err":
        errors += 1
    print(f"{tag} {msg}")
    if hint:
        for line in hint.splitlines():
            print(f"        → {line}")


def check_python() -> None:
    v = sys.version_info
    bits = struct.calcsize("P") * 8
    if v < (3, 10):
        say("err", f"Python {v.major}.{v.minor} — en az Python 3.10 gerekli ({sys.executable})",
            "python.org'dan Python 3.11 ya da 3.12 (64-bit) kurun, sonra backend\\.venv klasörünü silip start.bat'ı yeniden çalıştırın.")
    else:
        say("ok", f"Python {v.major}.{v.minor}.{v.micro} ({bits}-bit)")
    if bits != 64:
        say("warn", "Python 32-bit: SQL Server ODBC sürücüsü de 32-bit olmalı; genelde 64-bit Python önerilir.")


PACKAGES = [("fastapi", "fastapi"), ("uvicorn", "uvicorn[standard]"), ("pydantic", "pydantic"),
            ("pydantic_settings", "pydantic-settings"), ("openai", "openai"), ("sqlglot", "sqlglot"),
            ("multipart", "python-multipart"), ("PIL", "pillow"), ("pyodbc", "pyodbc"),
            ("openpyxl", "openpyxl"), ("pymysql", "pymysql")]


def check_packages() -> None:
    missing = []
    for mod, pkg in PACKAGES:
        try:
            importlib.import_module(mod)
        except Exception as e:  # noqa: BLE001
            missing.append(f"{pkg} ({type(e).__name__}: {e})")
    if sys.version_info < (3, 11):
        try:
            importlib.import_module("tomli")
        except Exception:  # noqa: BLE001
            missing.append("tomli")
    if missing:
        say("err", "Eksik / bozuk Python paketleri: " + "; ".join(missing),
            "backend\\.venv\\Scripts\\pip install -r backend\\requirements.txt\n"
            "Kurumsal proxy varsa: set HTTPS_PROXY=http://proxy:port  (ya da pip --index-url ile iç paket aynası)")
    else:
        say("ok", "Python paketleri kurulu")


def check_config() -> None:
    env = BACKEND / ".env"
    if not env.exists():
        say("warn", "backend\\.env yok — varsayılan ayarlar kullanılacak", "backend\\.env.example dosyasını .env olarak kopyalayıp düzenleyin.")
    try:
        from app.config import get_settings, load_toml
        s = get_settings()
        load_toml(s.dictionary_config)
        load_toml(s.policy_config)
        say("ok", f"Ayarlar okundu (.env, dictionary.toml, policy.toml) — LLM: {s.llm_model} @ {s.llm_base_url}")
    except Exception as e:  # noqa: BLE001
        msg = str(e).strip().splitlines()
        say("err", "Ayarlar okunamadı: " + (msg[0] if msg else type(e).__name__),
            "\n".join(msg[1:6]) + "\n.env içindeki değerleri (tırnak, JSON biçimi, yol) kontrol edin.")


def _port_owner(port: int) -> str:
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3].upper() in ("LISTENING", "DINLEME"):
                pid = parts[4]
                name = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                                      timeout=10).stdout.split(",")[0].strip('"')
                return f"PID {pid} ({name or '?'})"
    except Exception:  # noqa: BLE001
        pass
    return "bilinmeyen bir uygulama"


def _excluded(port: int) -> str | None:
    try:
        out = subprocess.run(["netsh", "interface", "ipv4", "show", "excludedportrange", "protocol=tcp"],
                             capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            p = line.replace("*", " ").split()
            if len(p) >= 2 and p[0].isdigit() and p[1].isdigit() and int(p[0]) <= port <= int(p[1]):
                return f"{p[0]}-{p[1]}"
    except Exception:  # noqa: BLE001
        pass
    return None


def port_free(port: int) -> tuple[bool, str]:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True, ""
    except OSError as e:
        return False, f"{e.__class__.__name__}: {e}"
    finally:
        s.close()


def check_port(port: int) -> None:
    ok, err = port_free(port)
    if ok:
        say("ok", f"Port {port} boş")
        return
    rng = _excluded(port)
    if "10013" in err or rng:
        say("err", f"Port {port} Windows tarafından ayrılmış / erişim engelli ({err})",
            (f"Ayrılmış aralık: {rng} (Hyper-V / WSL / Docker bu aralıkları ayırır).\n" if rng else "")
            + "start.bat otomatik olarak boş bir port seçer; elle: BI_PORT=8010 gibi başka bir port kullanın.")
    elif "10048" in err:
        say("err", f"Port {port} başka bir uygulama tarafından kullanılıyor: {_port_owner(port)}",
            "Eski bir BI Lens backend'i açıksa 'BI Lens - Backend' penceresini kapatın; değilse start.bat boş bir port seçer.")
    else:
        say("err", f"Port {port} açılamıyor: {err}")


def check_import() -> None:
    try:
        importlib.import_module("app.main")
        say("ok", "Uygulama kodu yüklendi (app.main)")
    except Exception:  # noqa: BLE001
        tb = traceback.format_exc().strip().splitlines()
        say("err", "Uygulama kodu yüklenemedi: " + tb[-1], "\n".join(tb[-8:-1]))


def check_database() -> None:
    try:
        from app.config import get_settings
        from app.data.connections import data_odbc, open_dictionary_reader, saved_dictionary
        from app.data.connector import SqlServerConnector
        from app.data.odbc import best_sql_server_driver, friendly_error, installed_drivers
    except Exception as e:  # noqa: BLE001
        say("warn", f"Veritabanı kontrolü atlandı: {e}")
        return
    drivers = [d for d in installed_drivers() if "SQL Server" in d]
    best = best_sql_server_driver(drivers)
    if best:
        say("ok", f"ODBC sürücüsü: {best}" + (f" (kurulu: {', '.join(drivers)})" if len(drivers) > 1 else ""))
    else:
        say("err", "SQL Server ODBC sürücüsü bulunamadı",
            "'Microsoft ODBC Driver 18 (ya da 17) for SQL Server' kurun (Python ile aynı bit: genelde x64).")
        return
    try:
        SqlServerConnector(data_odbc(get_settings()), 15).ping()
        say("ok", "Veri kaynağına (SQL Server) bağlanıldı")
    except Exception as e:  # noqa: BLE001
        say("warn", "Veri kaynağına bağlanılamadı (backend yine açılır; Bağlantı Ayarları'ndan düzeltilebilir)", friendly_error(str(e)))
    saved = saved_dictionary()
    if saved:
        try:
            open_dictionary_reader(saved[0])
            say("ok", f"Veri sözlüğü kaynağına bağlanıldı ({saved[0].get('kind', 'sqlserver')})")
        except Exception as e:  # noqa: BLE001
            say("warn", "Veri sözlüğü kaynağına bağlanılamadı", friendly_error(str(e)))


def check_llm() -> None:
    try:
        from app.config import get_settings
        from app.llm.gateway import LLMGateway
        h = LLMGateway(get_settings()).health()
        if h.get("reachable"):
            say("ok", f"Dil modeli erişilebilir: {h.get('model')}")
        else:
            say("warn", f"Dil modeline ulaşılamadı: {h.get('error') or h.get('base_url')}",
                "LLM_BASE_URL / LLM_API_KEY değerlerini, kurumsal proxy ve sertifika ayarlarını kontrol edin.")
    except Exception as e:  # noqa: BLE001
        say("warn", f"Dil modeli kontrol edilemedi: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="yalnız açılışı engelleyen kontroller")
    ap.add_argument("--port", type=int, default=int(os.environ.get("BI_PORT", "8000")))
    ap.add_argument("--skip-port", action="store_true")
    a = ap.parse_args()
    print("BI Lens ortam kontrolü\n" + "-" * 40)
    check_python()
    if sys.version_info >= (3, 10):
        check_packages()
        check_config()
        if not a.skip_port:
            check_port(a.port)
        check_import()
        if not a.quick:
            check_database()
            check_llm()
    print("-" * 40)
    print("Sonuç: " + ("sorun yok" if not errors else f"{errors} HATA — yukarıdaki açıklamalara bakın"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
