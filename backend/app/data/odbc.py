"""SQL Server ODBC sürücüsünü otomatik seçer.

Bağlantı cümlesinde yazan sürücü (ör. "ODBC Driver 18 for SQL Server") bilgisayarda kurulu değilse,
kurulu olan en yeni "ODBC Driver N for SQL Server" kullanılır (18 → 17 → 13 …). Hiçbiri yoksa eski
"SQL Server Native Client" / "SQL Server" sürücüleri son çare olarak denenir. Böylece aynı .env hem
Driver 18 hem Driver 17 kurulu makinede değiştirmeden çalışır.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

_DRIVER_RE = re.compile(r"DRIVER\s*=\s*(\{[^}]*\}|[^;]*)", re.IGNORECASE)
_MODERN = re.compile(r"^ODBC Driver (\d+) for SQL Server$", re.IGNORECASE)
_LEGACY = ["SQL Server Native Client 11.0", "SQL Server Native Client 10.0", "SQL Server"]
_warned: set[tuple[str, str]] = set()


def installed_drivers() -> list[str]:
    try:
        import pyodbc
    except ImportError:  # pragma: no cover
        return []
    return list(pyodbc.drivers())


def best_sql_server_driver(drivers: list[str]) -> str | None:
    """Kurulu sürücüler içinden en uygun SQL Server sürücüsü."""
    modern = sorted(((int(m.group(1)), d) for d in drivers if (m := _MODERN.match(d.strip()))), reverse=True)
    if modern:
        return modern[0][1]
    return next((d for d in _LEGACY if d in drivers), None)


def resolve_driver(conn_str: str, drivers: list[str] | None = None) -> str:
    """conn_str'deki DRIVER kurulu değilse en iyi kurulu SQL Server sürücüsüyle değiştirir."""
    m = _DRIVER_RE.search(conn_str or "")
    if not m:
        return conn_str
    wanted = m.group(1).strip().strip("{}").strip()
    drivers = installed_drivers() if drivers is None else drivers
    if not drivers or wanted in drivers:
        return conn_str
    best = best_sql_server_driver(drivers)
    if not best:
        return conn_str  # kurulu SQL Server sürücüsü yok: pyodbc'nin kendi hatası görünsün
    if (wanted, best) not in _warned:
        _warned.add((wanted, best))
        log.warning("ODBC sürücüsü '%s' kurulu değil; '%s' kullanılıyor.", wanted, best)
    out = conn_str[:m.start(1)] + "{" + best + "}" + conn_str[m.end(1):]
    if best in _LEGACY:  # eski sürücüler bu anahtarları tanımıyor
        out = re.sub(r"TrustServerCertificate\s*=\s*[^;]*;?", "", out, flags=re.IGNORECASE)
    elif (mm := _MODERN.match(best)) and int(mm.group(1)) >= 18 and not re.search(r"\b(Encrypt|TrustServerCertificate)\s*=", out, re.IGNORECASE):
        # Driver 18 varsayılan olarak şifreli bağlanıp sertifikayı doğrular; Driver 17 için yazılmış cümle
        # (şifreleme ayarı yok) kendi imzalı sertifikalı sunucuda kopmasın: şifreli kal, sertifikaya güven.
        out = out.rstrip(";") + ";TrustServerCertificate=yes;"
    return out


_HINTS: list[tuple[str, str]] = [
    (r"Error Locating Server/Instance|server was not found or was not accessible|SQL Server does not exist",
     "Sunucu / instance bulunamadı. Adı doğru mu (SUNUCU\\INSTANCE veya SUNUCU,port)? Adlandırılmış instance için "
     "SQL Server Browser servisi çalışmalı ve güvenlik duvarı UDP 1434'e izin vermeli."),
    (r"Login failed for user|18456", "Oturum açılamadı: kullanıcı adı / şifre hatalı ya da bu hesabın sunucuya erişim izni yok."),
    (r"Cannot open database|4060", "Veritabanı açılamadı: adı doğru mu, bu hesabın o veritabanında yetkisi var mı?"),
    (r"certificate chain|SSL Provider|certificate verify", "Sunucu sertifikası doğrulanamadı: 'Sunucu sertifikasına güven' seçeneğini açın "
     "ya da sunucuya kurumsal sertifika tanımlayın."),
    (r"Data source name not found|IM002|Can't open lib", "ODBC sürücüsü bulunamadı: 'ODBC Driver 18 (veya 17) for SQL Server' kurulu olmalı."),
    (r"TCP Provider|Named Pipes Provider|network-related|Login timeout expired",
     "Sunucuya ağ üzerinden ulaşılamadı: sunucu açık mı, TCP/IP etkin mi, güvenlik duvarı portu (varsayılan 1433) açık mı?"),
]
_ODBC_MSG = re.compile(r"\[(?:Microsoft)\]\[[^\]]+\](?:\[SQL Server\])?([^\[]+)")


def friendly_error(msg: str) -> str:
    """ODBC bağlantı hatasını anlaşılır Türkçe açıklama + kısa özgün mesaja çevirir."""
    raw = [re.sub(r"\s*\((-?\d+|SQL\w+)\).*$", "", m).strip().rstrip(".;") for m in _ODBC_MSG.findall(msg or "")]
    detail = "; ".join(dict.fromkeys(r for r in raw if r))[:300] or (msg or "")[:300]
    for pat, hint in _HINTS:
        if re.search(pat, msg or "", re.IGNORECASE):
            return f"{hint} (Ayrıntı: {detail})"
    return detail
