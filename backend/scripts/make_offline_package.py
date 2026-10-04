r"""Kurum bilgisayarı (internetsiz: pypi.org / npm / GitHub kapalı olabilir) için kurulum ve güncelleme paketleri.

İnternet olan bir bilgisayarda (proje kökünde) çalıştırın:

    make_offline_package.bat            GÜNCELLEME paketi  (~5 MB)
    make_offline_package.bat --full     TAM paket          (~55 MB; ilk kurulum / yeni bilgisayar)

Her iki paket de uygulamanın KENDİSİNİ içerir (GitHub'dan ZIP indirmek gerekmez):
  backend\ , frontend\ kaynakları, start.bat …   kod (bu bilgisayardaki güncel hali; .env / ayarlar / oturumlar HARİÇ)
  frontend\dist\ , frontend\dist-viewer\          derlenmiş arayüz (Node.js gerekmez)
  wheelhouse\                                       Python paketleri — tam pakette her zaman; güncelleme paketinde
                                                     yalnız requirements.txt son tam paketten beri değiştiyse

Güncelleme: zip mevcut kurulum klasörünün ÜZERİNE açılır. backend\.env, backend\config\connections.json, raporlar
(backend\sessions), onaylı view'lar ve kurulu Python ortamı (backend\.venv) pakette olmadığı için korunur; start.bat
requirements.txt değişmediyse paket kurmaz, doğrudan başlar.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
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
# son wheelhouse içeren paketin requirements.txt özeti: güncelleme paketine wheelhouse gerekip gerekmediği
LAST_WHEELS = OUT / ".wheels_requirements.sha1"
# pakete girmeyenler: kullanıcı ayarları / verisi (git'te de yok) ve geliştirme dosyaları
_SKIP_PREFIXES = ("backend/tests/", "offline/", "wheelhouse/", "frontend/dist/", "frontend/dist-viewer/")  # arayüz ayrıca eklenir
_SKIP_NAMES = {".gitignore", ".gitattributes"}


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


def _requirements_sha() -> str:
    return hashlib.sha1((BACKEND / "requirements.txt").read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def needs_wheels() -> bool:
    """Güncelleme paketine wheelhouse eklensin mi: requirements.txt son tam pakettekinden farklıysa (ya da kayıt yoksa)."""
    try:
        return LAST_WHEELS.read_text(encoding="utf-8").strip() != _requirements_sha()
    except OSError:
        return True


def source_files() -> list[str]:
    """Pakete girecek kod: git'in izlediği + henüz eklenmemiş (yoksayılmayan) dosyalar — .env, connections.json,
    sessions, .venv, node_modules gibi .gitignore'dakiler hiçbir zaman girmez."""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
                         capture_output=True, check=True).stdout.decode("utf-8")
    files = []
    for f in sorted(x for x in out.split("\0") if x):
        if f.startswith(_SKIP_PREFIXES) or Path(f).name in _SKIP_NAMES or not (ROOT / f).is_file():
            continue
        files.append(f)
    return files


def _version() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        return sha + (" + commit edilmemiş değişiklikler" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "?"


def make_zip(staging: Path, full: bool, wheels: bool) -> Path:
    print("[3/3] Paket oluşturuluyor…")
    OUT.mkdir(exist_ok=True)
    stamp = dt.datetime.now()
    name = OUT / f"BI_Lens_{'tam' if full else 'guncelleme'}_{stamp:%Y%m%d_%H%M}.zip"
    files = source_files()
    with zipfile.ZipFile(name, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(ROOT / f, f)
        if wheels:
            for p in sorted((staging / "wheelhouse").glob("*")):
                z.write(p, f"wheelhouse/{p.name}")
        for folder in ("dist", "dist-viewer"):
            for p in sorted((FRONTEND / folder).rglob("*")):
                if p.is_file() and p.name != ".prebuilt":
                    z.write(p, f"frontend/{folder}/{p.relative_to(FRONTEND / folder).as_posix()}")
        # işaret yalnız pakette: start.bat bunu görünce Vite yerine arayüzü backend'den sunar (geliştirme makinesi etkilenmez)
        z.writestr("frontend/dist/.prebuilt", stamp.isoformat(timespec="seconds"))
        z.writestr("SURUM.txt", f"BI Lens\npaket: {name.name}\ntarih: {stamp:%d.%m.%Y %H:%M}\nkod: {_version()}\n"
                                f"python paketleri: {'pakette' if wheels else 'pakette yok (değişmedi; kurulu olanlar kullanılır)'}\n")
        if full:
            z.writestr("KURULUM.txt", (
                "BI Lens — ilk kurulum (internetsiz)\n\n"
                "1. Bu zip'i boş bir klasöre açın (ör. C:\\BI_Lens). Uygulama bu klasörde kalacak; güncellemeler buraya açılacak.\n"
                "2. Python 3.10–3.13 (64-bit) kurulu olmalı. Node.js ve internet GEREKMEZ.\n"
                "3. start.bat'ı çalıştırın: Python paketleri wheelhouse klasöründen kurulur, tarayıcı açılır.\n"
                "4. Bağlantı Ayarları sayfasından veri kaynağı, sözlük ve LLM bağlantılarını girin.\n"
                "5. Sorun olursa check.bat çıktısına bakın.\n"))
        else:
            z.writestr("GUNCELLEME.txt", (
                "BI Lens — güncelleme\n\n"
                "1. Uygulamayı kapatın (start.bat penceresini kapatın).\n"
                "2. Bu zip'i MEVCUT kurulum klasörünün (start.bat'ın olduğu yer) üzerine açın; sorulursa 'Dosyaları değiştir'.\n"
                "   Ayarlarınız (backend\\.env, Bağlantı Ayarları), raporlarınız ve kurulu Python ortamı korunur.\n"
                "3. start.bat'ı çalıştırın.\n\n"
                + ("Bu pakette Python paketleri de var (gereken paketler değişti); start.bat ilk açılışta bunları kurar.\n" if wheels
                   else "Python paketleri değişmedi; kurulum yapılmaz, uygulama doğrudan açılır.\n")
                + "\nNot: GitHub'dan ZIP indirmenize gerek yok — kod bu paketin içinde.\n"))
    if wheels:
        LAST_WHEELS.write_text(_requirements_sha(), encoding="utf-8")
    return name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="tam paket: Python paketleri her zaman dahil (ilk kurulum / yeni bilgisayar)")
    ap.add_argument("--skip-frontend", action="store_true", help="arayüzü yeniden derleme (mevcut dist kullanılır)")
    a = ap.parse_args()
    staging = OUT / "_staging"
    shutil.rmtree(staging, ignore_errors=True)
    if not a.skip_frontend:
        build_frontend()
    elif not (FRONTEND / "dist" / "index.html").exists():
        print("frontend/dist yok: --skip-frontend olmadan çalıştırın.")
        return 1
    wheels = a.full or needs_wheels()
    if wheels:
        if not a.full:
            print("  ! requirements.txt son tam paketten beri değişti: Python paketleri de eklenecek.")
        download_wheels(staging / "wheelhouse")
    else:
        print("[2/3] Python paketleri değişmedi: pakete eklenmiyor.")
    path = make_zip(staging, a.full, wheels)
    n = len(list((staging / "wheelhouse").glob("*"))) if wheels else 0
    shutil.rmtree(staging, ignore_errors=True)
    kind = "TAM paket (ilk kurulum)" if a.full else "GÜNCELLEME paketi"
    print(f"\nHazır — {kind}: {path}  ({path.stat().st_size / 1024 / 1024:.1f} MB"
          + (f", {n} Python paketi" if wheels else ", Python paketleri yok") + ")")
    print("Kurum bilgisayarında: " + ("boş bir klasöre açıp start.bat'ı çalıştırın." if a.full
                                      else "mevcut kurulum klasörünün üzerine açıp start.bat'ı çalıştırın."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
