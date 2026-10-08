# Testler

## 1. Backend (pytest)
```
cd backend
.venv\Scripts\python.exe -m pytest -q
```
Veritabanı gerektirmeyen birim ve uç nokta testleri (sahte bağlantı + AdventureWorks sözlük anlık görüntüsü).

## 2. Tarayıcı (uçtan uca) testleri — Playwright
Gerçek arayüz, gerçek backend ve bu bilgisayardaki **AdventureWorksDW** ile kritik akışları tarayıcıda adım adım dener.
LLM yerine **senaryolu sahte LLM** (`frontend/e2e/fake_llm.py`) kullanılır. Böylece her koşu aynı sonucu verir ve çevrimdışı çalışır.
Araçlar, SQL doğrulaması, yetkiler ve veritabanı ise gerçektir.

### Kurulum (bir kez)
```
cd frontend
npm install
npx playwright install chromium
```

### Çalıştırma
| Komut | Ne yapar |
|---|---|
| `npm run e2e` | Önyüzü derler, tüm testleri çalıştırır (yaklaşık 3–6 dk) |
| `npx playwright test e2e/query.spec.ts` | Tek dosya |
| `npm run e2e:ui` | Playwright arayüzü: adım adım izleme / hata ayıklama |
| `npm run e2e:report` | Son koşunun HTML raporu (başarısız testlerde ekran görüntüsü + iz) |
| `npm run e2e:llm` | **Gerçek LLM** duman testi: bu PC'nin Bağlantı Ayarları'ndaki model (ör. Spark) ile; yavaş, isteğe bağlı |

### Ne test ediliyor
| Dosya | Akış |
|---|---|
| `e2e/query.spec.ts` | Sorgu Çalıştır: nesne ağacı ve arama, SELECT ve sonuç tablosu, Ctrl+Enter, yazma sorgusu reddi, kişisel veri kontrolü, "ilk 100 satır" |
| `e2e/dashboard.spec.ts` | Sohbetle dashboard: İhtiyaç → Veri (tablo ve ilişki önerisi, **onay olmadan kayıt yok**) → rapora özel ilişki → SQL / dataset → Tasarım → 4 görsel gerçek veriyle → Bölge Grubu filtresi KPI'yı değiştirir → HTML dışa aktarma → Canlı görünüm → "KPI'yı yeşil yap" |
| `e2e/demo.spec.ts` | LLM'siz: demo dashboard, görselden çapraz filtre, temizle |
| `e2e/vitrin.spec.ts` | Sunucu modu: kimliksiz 401 → admin rol atar → tasarımcı yayınlar ve **Satis** grubuyla paylaşır → izleyici yalnız Vitrin'i görür, raporu SQL'siz ve kendi yetkisiyle açar, tasarım adresine gidemez → grupta olmayan göremez → denetim kaydı |
| `e2e/llm-smoke.spec.ts` | (`npm run e2e:llm`) gerçek LLM ile ihtiyaçtan model onayına |

### Nasıl çalışıyor
- `playwright.config.ts` testlerden önce üç sunucu başlatır:
  - **sahte LLM** (8091)
  - **masaüstü modunda backend** (8090)
  - **sunucu modunda backend** (8092; kimlik `X-Remote-User` / `X-Remote-Groups` başlığından gelir, her kullanıcı ayrı tarayıcı bağlamıdır)
- `e2e/run_backend.py` her backend için ayrı geçici klasör kullanır (`frontend/e2e/.tmp`). Oturumlar, platform kayıtları,
  ilişkiler, view kaydı ve bağlantı dosyası buradadır. Bilgisayardaki gerçek raporlara ve ayarlara (`connections.json`,
  `platform.db`) **dokunulmaz**. Veri bağlantısı `backend/.env` içindeki `SQLSERVER_ODBC` ayarıdır.
- **Soğuk veritabanı:** bu PC'deki SQL Server bir sorgunun ilk çalışmasında 10–70 sn bekletebiliyor. Bu yüzden:
  - `run_backend.py` sunucuyu açmadan önce senaryo ve demo sorgularını bir kez çalıştırır (ısınma);
  - test backend'lerinde sorgu zaman aşımı 90 sn'dir.
- Seçiciler önce görünen metin ve erişilebilir adlarla yazılır. Gerekli yerlerde şu test nitelikleri kullanılır:
  - `data-visual-id` (dashboard görseli),
  - `data-testid="sql-editor"` / `"query-result"`,
  - `data-tool` / `data-ok` (sohbetteki araç adımları),
  - `data-role` (mesajlar).

### Sahte LLM senaryosunu genişletmek
`e2e/fake_llm.py` içindeki `decide()` fonksiyonu, faz (sistem talimatındaki "Şu anki faz: n/3") ve konuşmada daha önce
çağrılan araçlara bakıp sıradaki yanıtı döner. Yeni bir adım için bir koşul ve `call("araç", {...})` ya da `say("metin")` ekleyin.
Araç argümanlarında gerçek tablo ve kolon adlarını kullanın; backend bunları gerçekten doğrular ve çalıştırır.

### Playwright MCP (yeni test yazarken)
Bu bilgisayarda Claude için Playwright MCP kurulu (kullanıcı düzeyi, `~/.claude.json` → `mcpServers.playwright`).
Claude Code'da "Playwright MCP ile şu ekranı aç, adımları dene" diyerek sayfayı birlikte keşfedebilir, sonra kalıcı testi
`frontend/e2e/*.spec.ts` olarak yazdırabilirsiniz. Kalıcı testler MCP'ye bağlı değildir: yalnız `@playwright/test` ile çalışır.
