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
| `npm run e2e:llm` | **Gerçek LLM** testleri: bu PC'nin Bağlantı Ayarları'ndaki model (ör. Spark) ile; yavaş, isteğe bağlı. `E2E_LLM_SOURCE=env` ile LLM `.env`'den alınır (ör. EVREN), `E2E_LLM_MODEL` modeli seçer; yalnız geçici test backend'i etkilenir. Tasarım fazı en az ~16k token bağlam ister |
| `npm run shots` | Tanıtım / kılavuz ekran görüntülerini yeniden üretir (`frontend/public/tanitim/*.jpg`): izole backend (sunucu modu, sahte kullanıcı KURUMadmin, sahte LLM), AdventureWorks demo verisi. Önce `npm run build`, sonra tekrar `npm run build` (görseller `dist`e kopyalanır) |

### Ne test ediliyor
| Dosya | Akış |
|---|---|
| `e2e/landing.spec.ts` | Giriş sayfası: seçim yokken açılış, Vitrin / Tasarım / Kontrol Paneli kutuları (Yönetim ve Bağlantı Ayarları, seçim hatırlanmaz), seçimin hatırlanması, logodan dönüş, klavye ile seçim, dar ekranda kutular alt alta (sunucu modunda izleyici için Tasarım kutusu kilitli: `vitrin.spec.ts`) |
| `e2e/tour.spec.ts` | Tanıtım: açılış sayfasından sunum (tüm slaytlar klavyeyle, görseller yüklenir, slayt adresi, konuşmacı notu, büyütülen görselde Esc) ve kullanım kılavuzu (rol filtresi, içerikte arama, bölüm adresi, sol menüden Yardım), dar ekran |
| `e2e/inventory.spec.ts` | Rapor envanteri akıllı tablo: başlıktan sıralama (ad, görsel sayısı, statü sırası), kolon filtreleri (statü değer listesi, ad içinde arama), sayaç, filtreleri temizle, kolon göster-gizle ve sıralamanın sayfa yenilenince korunması, CSV (Excel için ; ayraç) |
| `e2e/query.spec.ts` | Sorgu Çalıştır: nesne ağacı ve arama, SELECT ve sonuç tablosu, Ctrl+Enter, yazma sorgusu reddi, kişisel veri kontrolü, "ilk 100 satır" |
| `e2e/dashboard.spec.ts` | Sohbetle dashboard: İhtiyaç → Veri (tablo ve ilişki önerisi, **onay olmadan kayıt yok**) → rapora özel ilişki → SQL / dataset → Tasarım → 4 görsel gerçek veriyle → Bölge Grubu filtresi KPI'yı değiştirir → HTML dışa aktarma → Canlı görünüm → "KPI'yı yeşil yap" |
| `e2e/demo.spec.ts` | LLM'siz: demo dashboard, görselden çapraz filtre, temizle |
| `e2e/flow.spec.ts` | Hata ve ret yolları (sahte LLM'in "[detay]" senaryosu, model bilerek hata yapar): KPI'sız gereksinim reddedilir ve faz ilerlemez (API 409), kullanıcının verdiği rapor adı agent tarafından değiştirilmez, onaysız ilişki kaydı reddedilir, kullanıcı reddedince yeni öneri, ortak modele kayıt, hatalı kolon ve DELETE sorgusu reddedilir, kısmen hatalı dataset kaydında faz ilerlemez, Veri sekmesi (taslak, SQL, önizleme, sözlük araması), Model sekmesi (onaylı ve SQL JOIN'den ilişkiler), faz adımlarıyla geri / ileri, bilinmeyen kolonlu spec reddedilir, tasarımda dataset + görsel + filtre eklenir, sayfa yenilenince her şey korunur |
| `e2e/design.spec.ts` | Dashboard tasarımı: 14 görsel türünün hepsi gerçek veriyle ve tablo görünümüyle, geniş / dar ekranda ve ekleme / silme sonrası görseller üst üste binmez, sayfa sekmeleri (her sayfa üstten başlar, filtre korunur, Canlı ve HTML), Spec sekmesi (hatalı JSON, şemaya aykırı spec, ızgaraya sığdırma, Ctrl+S), koyu tema, KPI zemininde okunur yazı, Türkçe sayı biçimleri; sohbetle düzenleme: grafik türü, yeni sayfa ve taşıma, koyu tema, desteklenmeyen stil alanı bildirilir, KPI kart stili, görsel kaldırma. Her testte tarayıcı konsol hatası testi düşürür |
| `e2e/vitrin.spec.ts` | Sunucu modu: kimliksiz 401 → admin rol atar → tasarımcı yayınlar ve **Satis** grubuyla paylaşır → izleyici yalnız Vitrin'i görür, raporu SQL'siz ve kendi yetkisiyle açar, tasarım adresine gidemez → grupta olmayan göremez → denetim kaydı |
| `e2e/llm-smoke.spec.ts` | (`npm run e2e:llm`) gerçek LLM ile ihtiyaçtan model onayına |
| `e2e/llm-design.spec.ts` | (`npm run e2e:llm`) gerçek LLM ile demo dashboard üzerinde doğal dilde istekler: pasta, alan, yığılmış çubuk, ısı haritası, gösterge (hedefli), huni, dağılım, ağaç haritası, combo, metin; tablo (ilk 10, sıralı, kolon seçimi); sayfa ekleme / taşıma / ad değiştirme ve sekme geçişleri; KPI kart stili (lacivert zemin, mavi tonları), desteklenmeyen istek "yaptım" diye anlatılmaz; koyu tema. Yumuşak doğrulama: tutmayan istekler raporda listelenir |

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
