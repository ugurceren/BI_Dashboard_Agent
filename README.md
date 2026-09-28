# BI Rapor Agent

Kurum içinde (on-prem) çalışan, modelden bağımsız bir "Claude Code for BI" harness'i.
Veri kaynağı ve veri sözlüğü **Microsoft SQL Server**'dadır (örnek: AdventureWorksDW2025 + Türkçe sözlük `BI_Meta`).

1. **İhtiyaç** — rapordan ne beklediğinizi anlatırsınız; agent netleştirici sorular sorar.
2. **Veri** — agent veri sözlüğünde tabloları bulur, SQL yazar, doğrulatır, çalıştırır ve dashboard dataset'lerini hazırlar.
3. **Tasarım** — tasarımı tarif edersiniz ya da örnek bir dashboard görseli yüklersiniz; agent bir Report Spec (JSON) üretir,
   arayüz bunu modern bir dashboard olarak çizer. "Bölge grafiğini donut yap" gibi isteklerle iterasyon yaparsınız.

LLM hiçbir zaman HTML/JS ya da doğrudan veritabanı erişimi üretmez: yalnızca araç çağırır ve JSON spec yazar.
Her şeyi harness doğrular.

```
 React UI ──SSE──► FastAPI ──► Agent döngüsü (faz bazlı) ──► LLM Gateway ──► vLLM / Ollama (Qwen, Llama …)
                                   │
                                   ├─ araçlar: search_dictionary · get_table_details · find_metrics · run_sql
                                   │           save_requirements · save_datasets · create_report_spec · update_visual …
                                   ├─ SQL validator (sqlglot): yalnız SELECT, izinli şema, sözlükte olan tablo, PII engeli
                                   ├─ Spec validator (pydantic + anlamsal kontrol, otomatik yerleşim düzeltme)
                                   ├─ Vision: piksel renk analizi + (varsa) VL model → tasarım özeti
                                   └─ audit log (logs/audit.jsonl)
```

## Hızlı başlatma (Windows)

Proje klasöründeki **`start.bat`** dosyasına çift tıklayın. İlk çalıştırmada eksik kurulumu (Python ortamı, npm paketleri,
`.env`, HTML export şablonu) kendisi yapar; sonra backend ve frontend'i ayrı pencerelerde başlatıp tarayıcıda
http://localhost:5173 adresini açar. Kapatmak için "BI Agent - Backend" ve "BI Agent - Frontend" pencerelerini kapatın.
Veri sözlüğü bat dosyası tarafından oluşturulmaz; aşağıdaki adımlarla bir kez oluşturulmalıdır.

## Kurulum

Ön koşul: SQL Server (örnek veri için AdventureWorksDW2025) ve **Microsoft ODBC Driver 18 for SQL Server**.

```bash
# backend
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # Linux: .venv/bin/pip
copy .env.example .env                                 # SQLSERVER_ODBC, LLM_BASE_URL / LLM_MODEL'i düzenleyin
.venv/Scripts/python scripts/seed_adventureworks_dictionary.py --server localhost --source-db AdventureWorksDW2025
.venv/Scripts/python scripts/check_llm.py             # sunucu tool calling destekliyor mu?
.venv/Scripts/python -m uvicorn app.main:app --port 8000

# frontend (ayrı terminal)
cd frontend
npm install
npm run dev              # http://localhost:5173
npm run build:viewer     # "HTML indir" için tek dosyalık viewer
```

LLM olmadan denemek için arayüzde **Demo dashboard yükle** butonunu kullanın (`docs/demo_spec.json`, AdventureWorks verisiyle).

## Model sunucusu

OpenAI uyumlu her endpoint çalışır. Önerilen (GPU sunucusunda):

```bash
# vLLM — native tool calling
python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen3-32B \
  --enable-auto-tool-choice --tool-call-parser hermes --max-model-len 32768
# görsel model (ayrı port)
python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2.5-VL-7B-Instruct --port 8002
```

- `LLM_TOOL_MODE=auto`: native tool calling dener, sunucu desteklemiyorsa `<tool_call>` prompt moduna düşer.
- Model araç çağırmada ne kadar iyiyse agent o kadar iyi çalışır. 7-8B modeller basit akışlarda iş görür;
  güvenilir sonuç için 30B+ (Qwen3-32B, Qwen2.5-72B, Llama-3.3-70B) önerilir.
- `VISION_MODEL` boşsa örnek görsellerden yalnızca renkler (piksel analizi) çıkarılır.

## Veri kaynağı ve sözlük

| | Nerede | Ayar |
|---|---|---|
| Veri | SQL Server / `AdventureWorksDW2025` | `backend/.env` → `SQLSERVER_ODBC` |
| Sözlük | SQL Server / `BI_Meta.meta.dd_*` | `backend/config/dictionary.toml` |
| Erişim politikası | izinli şema `dbo`, PII kapalı | `backend/config/policy.toml` |
| Demo dashboard | `docs/demo_spec.json` | `DEMO_SPEC` (isteğe bağlı) |

AdventureWorks sözlüğünü (yeniden) oluşturmak — AdventureWorks'e dokunmaz, ayrı `BI_Meta` veritabanına yazar:

```bash
backend/.venv/Scripts/python backend/scripts/seed_adventureworks_dictionary.py --server localhost --source-db AdventureWorksDW2025
```

Script tabloları/kolonları ve foreign key'leri otomatik okur; Türkçe iş adları, eş anlamlılar ve PII işaretleri
scriptin içindeki `TABLES` / `COLUMNS` sözlüklerinde. Müşteri kimlik/iletişim bilgileri ile çalışan kimlik, doğum tarihi,
iletişim ve ücret bilgileri PII olarak işaretli (analyst rolü sorgulayamaz); satış temsilcisi ad-soyadı raporlanabilir.

## Kendi SQL Server sözlüğünüze bağlamak

1. `backend/config/dictionary.toml` → sorguları kendi sözlük tablolarınıza göre yazın (mantıksal kolon adlarıyla `AS ...`).
   Sözlük ayrı bir veritabanındaysa `source = "odbc"` + `odbc = "..."`.
2. `.env` → `SQLSERVER_ODBC=...` (salt-okunur kullanıcı!).
3. `backend/config/policy.toml` → rol başına izinli şemalar, yasak tablolar, PII izni, satır limiti.
4. `POST /api/dictionary/reload` ile sözlüğü yeniden yükleyin.

## Güvenlik katmanları

| Katman | Ne yapar |
|---|---|
| Faz bazlı araçlar | Her fazda yalnızca o faza ait araçlar açık; model başka işe kalkışamaz |
| SQL validator | Tek ifade; yalnız SELECT/WITH/UNION; DML/DDL/INTO/EXEC yasak; OPENROWSET gibi tablo fonksiyonları, sistem fonksiyonları ve @/@@ değişkenleri yasak; başka veritabanı adıyla erişim yasak; sadece sözlükteki + izinli şemadaki tablolar |
| PII | Sözlükte `is_pii` olan kolonlar ve PII içeren tablolarda `SELECT *` engellenir (rol izni yoksa) |
| Model filtreleri | Dilimleyiciler bir model kolonuna bağlanır (ör. `dbo.DimSalesTerritory.SalesTerritoryGroup`); seçim, aktif ilişkiler üzerinden (boyut → fact, çok adımlı yollar dahil) her dataset'in SQL'ine eklenir. Görsellerde bir çubuğa/dilime tıklamak da diğer görselleri filtreler (Power BI çapraz filtresi) |
| JOIN / kardinalite | Birleştirmeler sözlükteki ilişkilerle (N:1, 1:1, N:N, bileşik anahtar, rol) eşlenir. Eksik bileşik anahtar ve satır çoğalması (fan-out, iki fact'in ortak boyut üzerinden birleşmesi — chasm trap) reddedilir; sözlükte olmayan birleştirmeler uyarı olarak döner. Kardinalite sözlükte yoksa unique index / COUNT DISTINCT ile otomatik çıkarılır |
| Bağlantı | SQL Server'a salt-okunur (db_datareader) bir kullanıcıyla bağlanın; sorgu zaman aşımı ve satır limiti uygulanır. Yazma koruması veritabanı yetkisine dayanır, validator ek katmandır |
| Spec | Pydantic şema + dataset/alan referans kontrolü; model hiçbir zaman kod üretmez |
| Denetim | Her LLM çağrısı ve araç çalıştırması `logs/audit.jsonl`'a yazılır |

## Testler

```bash
cd backend && .venv/Scripts/python -m pytest -q
```

Mantık testleri SQL Server olmadan da çalışır: AdventureWorks sözlüğünün sabit bir kopyası (`tests/fixtures/aw_dictionary.json`)
ve sorgunun kolonlarına göre sahte satır üreten bir bağlantı kullanılır. Harness uçtan uca, senaryolu sahte bir modelle test
edilir (`tests/test_harness.py`). Gerçek SQL Server'da çalışan entegrasyon testleri (filtrelenmiş toplamların elle yazılmış
JOIN ile karşılaştırılması, kardinalite tespiti) sunucuya erişilemezse otomatik atlanır.

Sözlük değişince sabit kopyayı yenilemek için:
`python -c "import json; from app.main import build_services; json.dump(build_services().dictionary.to_snapshot(), open('tests/fixtures/aw_dictionary.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)"`

## Klasörler

```
backend/app/llm/gateway.py        modelden bağımsız LLM katmanı (native / prompt tool calling, <think> temizleme)
backend/app/harness/agent.py      agent döngüsü, olay akışı, audit
backend/app/harness/phases.py     faz prompt'ları ve durum aktarımı
backend/app/harness/tools.py      araçlar ve doğrulamalar
backend/app/harness/vision.py     örnek görselden tasarım özeti
backend/app/data/validator.py     SQL güvenlik doğrulayıcı
backend/app/data/join_guard.py    JOIN / kardinalite / satır çoğalması kontrolü
backend/app/data/model_filters.py model tabanlı filtre yayılımı ve alan kökeni
backend/app/dictionary/           veri sözlüğü yükleme + Türkçe arama
backend/app/spec/models.py        Report Spec şeması
frontend/src/dashboard/           Report Spec → dashboard renderer (ECharts)
docs/CONTRACT.md                  API + Report Spec sözleşmesi
```
