"""Yeni rapor önerileri — kullanıcının yetkili olduğu veriden, iki katman:

1. KURAL (anında, LLM yok): erişilebilir tablo / view'lardan domain başına öneri iskeleti:
     ölçü   = sözlükte rolü measure olan ya da sayısal tipte (decimal / money / int …) anahtar olmayan kolon
     kırılım = metin kolonları ve ilişkili boyut tabloları (sözlükteki iş adlarıyla)
     zaman  = tarih tipli kolon ya da tarih boyutuna ilişki → "aylık trend"
   Kişisel veri (PII) kolonları, boş tablolar (satır sayısı 0) ve erişimi olmayan nesneler kullanılmaz.
2. LLM (isteğe bağlı): iskeletler iş diline çevrilir (uydurma metrik eklemeden); sonuç önbellekte tutulur.
   LLM'e ulaşılamazsa kural önerileri olduğu gibi kalır.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("bi-agent")

NUMERIC = {"int", "bigint", "smallint", "tinyint", "decimal", "numeric", "money", "smallmoney", "float", "real", "double",
           "number", "currency", "integer"}
AMOUNT = {"decimal", "numeric", "money", "smallmoney", "float", "real", "double", "currency"}
DATE = {"date", "datetime", "datetime2", "smalldatetime", "datetimeoffset", "timestamp"}
TEXT = {"varchar", "nvarchar", "char", "nchar", "text", "ntext", "string"}
# anahtar / kod / bayrak kolonları ölçü ya da kırılım değildir
_KEY = re.compile(r"(^id$|id$|_id$|key$|_key$|kod$|_kod$|code$|_code$|^no$|_no$|number$|num$|flag$|bayrak$|guid$|uuid$|hash$)", re.I)
_TIME_PART = re.compile(r"^(year|yil|yıl|month|ay|day|gun|gün|quarter|ceyrek|çeyrek|week|hafta|calendar.*|fiscal.*)$", re.I)
# kişisel / demografik bilgiler: sözlükte PII işaretli olmasa da öneriye girmez (KVKK)
_PERSONAL = re.compile(r"(gender|cinsiyet|marital|medeni|birth|dogum|doğum|religion|din$|ethnic|children|cocuk|çocuk|"
                       r"education|egitim|eğitim|occupation|meslek|income|gelir_?duzeyi|salary|maas|maaş|^age$|^yas$|^yaş$|"
                       r"salutation|hitap|suffix|prefix|middle_?name|nickname)", re.I)
_LONG_TEXT = re.compile(r"(desc|aciklama|açıklama|note|not$|notes|comment|yorum|adres|address|url|path|email|e_?posta|phone|telefon)", re.I)
_DATE_NAME = re.compile(r"(date|tarih|zaman|time)", re.I)
_DATE_DIM = re.compile(r"(dimdate|dim_date|date$|tarih|calendar|takvim|time$)", re.I)
# ana ölçüler önce: satış / tutar / ciro …; indirim, maliyet, vergi gibi yan tutarlar sonra
_MAIN_MEASURE = re.compile(r"(sales|amount|tutar|revenue|ciro|gelir|satis|satış|total|toplam|bakiye|balance|adet|quantity|qty|count)", re.I)
_SIDE_MEASURE = re.compile(r"(discount|indirim|tax|vergi|freight|navlun|cost|maliyet|fee|komisyon|weight|agirlik|ağırlık|size)", re.I)
_NOT_EVENT_DATE = re.compile(r"(birth|dogum|doğum|start|end|bitis|bitiş|baslangic|başlangıç|modified|updated|created|insert|load|etl)", re.I)

_CAMEL = re.compile(r"(?<=[a-zçğıöşü0-9])(?=[A-ZÇĞİÖŞÜ])")


def _base_type(t: str) -> str:
    return re.sub(r"\(.*", "", (t or "").strip().lower())


def human(name: str) -> str:
    """'CollateralName' → 'Collateral Name', 'vw_satis_ozeti' / 'vTargetMail' → 'Satis Ozeti' / 'Target Mail' (iş adı yoksa)."""
    base = re.sub(r"^(vw_?|v_|v(?=[A-Z]))", "", name.split(".")[-1])
    s = _CAMEL.sub(" ", base).replace("_", " ").strip()
    return s[:1].upper() + s[1:] if s else name


def _label(c) -> str:
    bn = (c.business_name or "").strip()
    return bn if bn and bn.lower() != c.name.lower() else human(c.display_name or c.name)


def _table_label(t) -> str:
    bn = (t.business_name or "").strip()
    disp = t.display_name or t.name
    return bn if bn and bn.lower() not in (disp.lower(), t.name.lower()) else human(disp)


def _is_measure(c) -> bool:
    if c.is_pii or _KEY.search(c.name) or _TIME_PART.match(c.name) or _PERSONAL.search(c.name):
        return False
    if (c.role or "").lower() in ("measure", "olcu", "ölçü", "metric"):
        return True
    if (c.role or "").lower() in ("dimension", "key", "boyut", "anahtar"):
        return False
    return _base_type(c.data_type) in NUMERIC


def _is_dim(c) -> bool:
    if c.is_pii or _KEY.search(c.name) or _LONG_TEXT.search(c.name) or _PERSONAL.search(c.name):
        return False
    if (c.role or "").lower() in ("dimension", "boyut", "attribute"):
        return _base_type(c.data_type) in TEXT or not c.data_type
    return _base_type(c.data_type) in TEXT


def _is_date(c) -> bool:
    """İşlem / olay tarihi (doğum, başlangıç–bitiş, kayıt / yükleme tarihleri trend için sayılmaz)."""
    if _NOT_EVENT_DATE.search(c.name):
        return False
    return _base_type(c.data_type) in DATE or (not c.data_type and bool(_DATE_NAME.search(c.name)) and not _KEY.search(c.name))


def _measure_rank(c) -> tuple:
    name = f"{c.name} {c.business_name or ''}"
    return (not _MAIN_MEASURE.search(name) or bool(_SIDE_MEASURE.search(name)), bool(_SIDE_MEASURE.search(name)),
            (c.role or "").lower() != "measure", _base_type(c.data_type) not in AMOUNT)


def analyse(dd, accessible: Callable[[Any], bool]) -> list[dict[str, Any]]:
    """Her erişilebilir tablo / view için ölçü, kırılım ve zaman adayları + puan."""
    kinds = dd._table_kinds()
    rel_dims: dict[str, list[str]] = {}
    rel_time: set[str] = set()
    referenced: set[str] = set()   # başka bir tablonun işaret ettiği = boyut / sözlük tablosu (kendisi öneri olmaz)
    for r in dd.relationships:
        referenced.add(r.to_table)
        to = dd.tables.get(r.to_table)
        if to is None or not accessible(to):
            continue
        if _DATE_DIM.search(r.to_table.split(".")[-1]):
            rel_time.add(r.from_table)
            continue
        if any(_is_dim(c) for c in to.columns):   # kırılım: boyutun kendi iş adı (Ürün, Müşteri, Bölge …)
            rel_dims.setdefault(r.from_table, []).append(_table_label(to))
    out = []
    for t in dd.tables.values():
        if not accessible(t) or t.row_count == 0 or t.subject_area == "Onaylı rapor view'ları":
            continue
        if _DATE_DIM.search(t.name.split(".")[-1]) or t.name in referenced:
            continue
        measures = [c for c in t.columns if _is_measure(c)]
        measures.sort(key=_measure_rank)
        dims = [_label(c) for c in t.columns if _is_dim(c)]
        dims = list(dict.fromkeys(rel_dims.get(t.name, []) + dims))
        time = t.name in rel_time or any(_is_date(c) for c in t.columns)
        if not measures and len(dims) < 2:
            continue
        kind = kinds.get(t.name, "")
        score = (3 if kind == "fact" else 0) + min(len(measures), 3) + (2 if time else 0) + min(len(dims), 3) \
            + (1 if t.documented else 0) + (1 if t.description else 0)
        out.append({"table": t, "measures": [_label(c) for c in measures[:2]], "dims": dims[:2], "time": time, "score": score,
                    "domain": (t.subject_area or "").strip() or "Diğer"})
    out.sort(key=lambda x: -x["score"])
    return out


def _sentence(a: dict[str, Any]) -> str:
    t = _table_label(a["table"])
    m, d = a["measures"], a["dims"]
    mtxt = " ve ".join(m)
    dtxt = " ve ".join(d)
    if m and a["time"] and d:
        return f"{t}: {mtxt} aylık trendi, {dtxt} kırılımında"
    if m and a["time"]:
        return f"{t}: {mtxt} aylık trendi ve dönem karşılaştırması"
    if m and d:
        return f"{t}: {dtxt} bazında {mtxt} dağılımı"
    if m:
        return f"{t}: {mtxt} özet göstergeleri"
    if a["time"]:
        return f"{t}: {dtxt} bazında kayıt sayısı ve aylık trend"
    return f"{t}: {dtxt} bazında kayıt sayısı ve dağılım"


def rule_suggestions(dd, accessible: Callable[[Any], bool], reports: list[dict[str, Any]] | None = None,
                     offset: int = 0, limit: int = 4) -> list[dict[str, Any]]:
    """Domain'leri dolaşarak (her turda domain başına en iyi sıradaki tablo) çeşitli öneriler; offset 'Yenile' içindir."""
    items = analyse(dd, accessible)
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for a in items:
        by_domain.setdefault(a["domain"], []).append(a)
    undoc = dd.UNDOCUMENTED_AREA if hasattr(dd, "UNDOCUMENTED_AREA") else "Sözlükte tanımsız"
    order = sorted((k for k in by_domain if k != undoc), key=lambda k: -by_domain[k][0]["score"])
    ranked: list[dict[str, Any]] = []
    depth = 0
    total = sum(len(by_domain[k]) for k in order)
    while len(ranked) < total:   # domain'ler arasında sırayla: her turda domain başına bir öneri
        for dom in order:
            if depth < len(by_domain[dom]):
                ranked.append(by_domain[dom][depth])
        depth += 1
    ranked += by_domain.get(undoc, [])   # sözlükte tanımsız nesneler en sona
    if not ranked:
        return []
    start = (offset * limit) % len(ranked)
    pick = (ranked[start:] + ranked[:start])[:limit]
    out = []
    for a in pick:
        t = a["table"]
        similar = next((r for r in reports or [] if t.name in r.get("tables", [])), None)
        out.append({
            "id": hashlib.sha1(f"{t.name}|{a['measures']}|{a['dims']}".encode()).hexdigest()[:10],
            "domain": a["domain"], "table": t.display_name or t.name, "text": _sentence(a),
            "measures": a["measures"], "dims": a["dims"], "time": a["time"],
            "similar_report": {"id": similar["id"], "title": similar.get("title") or "Başlıksız"} if similar else None,
        })
    return out


# ------------------------------------------------------------------ LLM katmanı
_PROMPT = """Aşağıda bir BI uygulamasında kullanıcıya gösterilecek rapor önerisi iskeletleri var (JSON). Her birini, iş
kullanıcısının yeni rapor isterken yazacağı TEK, doğal Türkçe cümleye çevir.
Kurallar:
- Yalnız verilen tablo, ölçü ve kırılımları kullan; yeni metrik, oran, hedef ya da tablo UYDURMA.
- "time": true ise zaman trendini anabilirsin; false ise zaman/trend sözü etme.
- En fazla 18 kelime; teknik ad (şema, tablo adı, alt çizgi) yazma; iş adlarını kullan.
- İngilizce adları doğru Türkçeleştir (Amount → Tutar, Quantity → Adet, Rate → Kur / Oran, Sales → Satış,
  Region → Bölge, Category → Kategori); iki farklı ölçüyü ya da kırılımı aynı kelimeye çevirme.
- Cümle "… istiyorum" / "… görmek istiyorum" / "… analiz et" gibi bir istekle bitsin.
Yanıt olarak YALNIZ JSON dizi ver: aynı sırada, her öğe bir metin (string). Başka hiçbir şey yazma.

İskeletler:
"""

_lock = threading.Lock()


def _cache_file(cache_dir: Path) -> Path:
    return cache_dir / "suggestions.json"


PROMPT_VERSION = "2"   # istem değişince önbellekteki eski LLM metinleri kullanılmaz


def cache_key(model: str, items: list[dict[str, Any]]) -> str:
    raw = json.dumps([PROMPT_VERSION, model] + [[i["text"], i["measures"], i["dims"], i["time"]] for i in items], ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def cached(cache_dir: Path, key: str) -> list[str] | None:
    try:
        return json.loads(_cache_file(cache_dir).read_text(encoding="utf-8")).get(key)
    except (OSError, ValueError):
        return None


def _store(cache_dir: Path, key: str, texts: list[str]) -> None:
    with _lock:
        f = _cache_file(cache_dir)
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data[key] = texts
        if len(data) > 200:   # eski kayıtlar
            data = dict(list(data.items())[-200:])
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def polish(llm, items: list[dict[str, Any]], cache_dir: Path) -> list[str] | None:
    """İskeletleri LLM ile iş diline çevirir; önbellekte varsa onu döner. Başarısızsa None (kural metni kalır)."""
    if not items:
        return []
    key = cache_key(llm.s.llm_model, items)
    hit = cached(cache_dir, key)
    if hit is not None:
        return hit
    skel = [{"tablo": i["text"].split(":")[0], "ölçüler": i["measures"], "kırılımlar": i["dims"], "time": i["time"],
             "domain": i["domain"]} for i in items]
    try:
        turn = llm.chat([{"role": "user", "content": _PROMPT + json.dumps(skel, ensure_ascii=False, indent=1)}])
    except Exception as e:  # noqa: BLE001 — LLM yoksa kural önerileri yeterli
        log.info("Rapor önerileri LLM ile düzenlenemedi: %s", e)
        return None
    m = re.search(r"\[.*\]", turn.content or "", re.S)
    try:
        texts = json.loads(m.group(0)) if m else None
    except ValueError:
        texts = None
    if not (isinstance(texts, list) and len(texts) == len(items) and all(isinstance(x, str) and x.strip() for x in texts)):
        log.info("Rapor önerileri: LLM yanıtı beklenen biçimde değil.")
        return None
    texts = [re.sub(r"\s+", " ", x).strip().strip('"') for x in texts]
    _store(cache_dir, key, texts)
    return texts
