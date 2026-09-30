"""Çevrimdışı kurulum paketi: internete çıkamayan (pypi.org / npm kapalı) kurum bilgisayarı için.

İnternet olan bir bilgisayarda çalıştırın:

    make_offline_package.bat            (proje kökünde)
    # ya da: backend\\.venv\\Scripts\\python -X utf8 backend\\scripts\\make_offline_package.py

Oluşan  offline\\BI_Lens_offline_<tarih>.zip  içinde:
  wheelhouse\\            Python paketleri (Windows 64-bit, Python 3.10–3.13) — bu bilgisayardaki sürümlerin aynısı
  frontend\\dist\\         derlenmiş arayüz (backend sunar: Node.js / npm gerekmez)
  frontend\\dist-viewer\\  HTML export şablonu

Kurum bilgisayarında: zip'i proje klasörüne (start.bat'ın yanına) açıp start.bat'ı çalıştırın.
start.bat wheelhouse klasörünü görünce paketleri internetsiz kurar, frontend\\dist\\.prebuilt'i görünce Vite yerine
arayüzü backend'den sunar (tek port, tarayıcı http://127.0.0.1:<port>/ açılır).
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
OUT = ROOT / "offline"
PY_VERSIONS = ["3.10", "3.11", "3.12", "3.13"]


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("  $", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True, shell=(sys.platform == "win32" and cmd[0] in ("npm", "npx")))


def build_frontend() -> None:
    print("[1/3] Arayüz derleniyor…")
    run(["npm", "run", "build"], FRONTEND)
    run(["npm", "run", "build:viewer"], FRONTEND)


def download_wheels(wheelhouse: Path) -> None:
    print("[2/3] Python paketleri indiriliyor (Windows 64-bit)…")
    wheelhouse.mkdir(parents=True, exist_ok=True)
    # test edilen sürümler: bu bilgisayardaki sanal ortamın paket sürümleri
    freeze = subprocess.run([sys.executable, "-m", "pip", "freeze", "--exclude-editable"], capture_output=True, text=True,
                            check=True).stdout
    constraints = wheelhouse.parent / "constraints.txt"
    constraints.write_text("\n".join(l for l in freeze.splitlines() if "==" in l and not l.startswith("-")), encoding="utf-8")
    base = [sys.executable, "-m", "pip", "download", "-q", "-r", str(BACKEND / "requirements.txt"),
            "--platform", "win_amd64", "--implementation", "cp", "--only-binary=:all:", "-d", str(wheelhouse)]
    for v in PY_VERSIONS:
        try:
            run(base + ["--python-version", v, "-c", str(constraints)])
        except subprocess.CalledProcessError:
            # bir sürümün bu Python için hazır paketi yoksa: kısıtsız (uyumlu en yeni) sürümle dene
            print(f"  ! Python {v}: bazı sabit sürümler bulunamadı, uyumlu sürümlerle deneniyor")
            run(base + ["--python-version", v])
    # pip'in kendisi de (ilk kurulumda güncellemek için)
    run([sys.executable, "-m", "pip", "download", "-q", "pip", "--only-binary=:all:", "-d", str(wheelhouse)])
    constraints.unlink(missing_ok=True)


def make_zip(staging: Path) -> Path:
    print("[3/3] Paket oluşturuluyor…")
    OUT.mkdir(exist_ok=True)
    name = OUT / f"BI_Lens_offline_{dt.date.today():%Y%m%d}.zip"
    with zipfile.ZipFile(name, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted((staging / "wheelhouse").glob("*")):
            z.write(p, f"wheelhouse/{p.name}")
        for folder in ("dist", "dist-viewer"):
            for p in sorted((FRONTEND / folder).rglob("*")):
                if p.is_file() and p.name != ".prebuilt":
                    z.write(p, f"frontend/{folder}/{p.relative_to(FRONTEND / folder).as_posix()}")
        # işaret yalnız pakette: start.bat bunu görünce Vite yerine arayüzü backend'den sunar (geliştirme makinesi etkilenmez)
        z.writestr("frontend/dist/.prebuilt", dt.datetime.now().isoformat(timespec="seconds"))
        z.writestr("OFFLINE_KURULUM.txt", (
            "BI Lens — çevrimdışı kurulum\n\n"
            "1. Bu zip'i proje klasörüne (start.bat'ın bulunduğu yere) açın; wheelhouse ve frontend klasörleri yerine otursun.\n"
            "2. Python 3.10–3.13 (64-bit) kurulu olmalı. Node.js GEREKMEZ.\n"
            "3. start.bat'ı çalıştırın: paketler internetsiz kurulur, arayüz backend'den açılır.\n"
            "4. Sorun olursa check.bat çıktısına bakın.\n"))
    return name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-frontend", action="store_true", help="arayüzü yeniden derleme (mevcut dist kullanılır)")
    a = ap.parse_args()
    staging = OUT / "_staging"
    shutil.rmtree(staging, ignore_errors=True)
    if not a.skip_frontend:
        build_frontend()
    elif not (FRONTEND / "dist" / "index.html").exists():
        print("frontend/dist yok: --skip-frontend olmadan çalıştırın.")
        return 1
    download_wheels(staging / "wheelhouse")
    path = make_zip(staging)
    n = len(list((staging / "wheelhouse").glob("*")))
    shutil.rmtree(staging, ignore_errors=True)
    print(f"\nHazır: {path}  ({path.stat().st_size / 1024 / 1024:.1f} MB, {n} Python paketi)")
    print("Kurum bilgisayarında proje klasörüne açıp start.bat'ı çalıştırın.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
