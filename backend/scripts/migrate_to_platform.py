"""Mevcut raporları Vitrin platformuna taşır (bir kez çalıştırılır; tekrar çalıştırmak güvenlidir).

  - 'Canlıda' (live) statüsündeki ve dashboard'u olan her tasarım oturumu için Vitrin'de ilk yayın (sürüm 1) oluşturulur.
    Paylaşım başlangıçta boştur: rapor yalnız sahibine ve yöneticilere görünür; sahibi Vitrin → Paylaş ile açar.
  - Sahibi olmayan eski oturumlara --owner ile sahip atanabilir (ör. --owner "KURUM\\ali").
  - Zaten yayında olan oturum atlanır.

Kullanım (backend klasöründe):  .venv\\Scripts\\python.exe -m scripts.migrate_to_platform [--owner KURUM\\kullanici] [--dry-run]
Platform veritabanı: Bağlantı Ayarları → Platform Veritabanı / .env META_ODBC; ikisi de yoksa yerel SQLite.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.data import connections as conns  # noqa: E402
from app.harness.session import SessionStore  # noqa: E402
from app.meta.store import MetaStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", help="sahibi olmayan oturumlara atanacak kullanıcı (DOMAIN\\kullanici)")
    ap.add_argument("--dry-run", action="store_true", help="yalnız ne yapılacağını göster")
    args = ap.parse_args()

    settings = get_settings()
    odbc = conns.meta_odbc(settings)
    meta = MetaStore.sqlserver(odbc) if odbc else MetaStore.sqlite(settings.meta_sqlite)
    store = SessionStore(settings.sessions_dir)
    print(f"Platform veritabanı: {meta.kind} · oturumlar: {settings.sessions_dir}")

    published = skipped = 0
    for item in store.list():
        s = store.get(item["id"])
        if not s.owner and args.owner:
            print(f"  sahip atanıyor: {s.title} → {args.owner}")
            if not args.dry_run:
                s.owner, s.owner_name = args.owner, args.owner
                store.save(s)
        if s.status != "live" or not s.spec:
            continue
        if meta.report_by_session(s.id):
            skipped += 1
            continue
        if not s.owner:
            print(f"  ATLANDI (sahibi yok, --owner verin): {s.title}")
            skipped += 1
            continue
        print(f"  yayınlanıyor: {s.title} ({s.owner})")
        if not args.dry_run:
            rep = meta.publish(session_id=s.id, owner=s.owner, owner_name=s.owner_name, title=s.title, description=None,
                               domains=item.get("domains") or [], spec=s.spec.model_dump(mode="json"),
                               datasets=[d.model_dump(mode="json") for d in s.datasets], notes="Platforma taşındı",
                               by="migrate_to_platform")
            meta.audit("migrate_to_platform", "report_publish", rep["report_id"], version=1, session=s.id)
        published += 1
    print(f"Tamam: {published} rapor yayınlandı, {skipped} atlandı{' (deneme: hiçbir şey yazılmadı)' if args.dry_run else ''}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
