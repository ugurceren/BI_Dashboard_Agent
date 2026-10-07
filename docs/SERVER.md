# BI Lens — ortak sunucu kurulumu (Vitrin platformu)

Masaüstü kurulumda (varsayılan) her kullanıcı uygulamayı kendi bilgisayarında çalıştırır ve tek kullanıcı olarak **admin** sayılır.
Ortak sunucuda ise birden çok kişi aynı uygulamaya bağlanır. Bu durumda roller, paylaşım ve denetim kaydı devreye girer.

## Roller

| Platform rolü | Ne yapabilir |
|---|---|
| **Yönetici (admin)** | Her şey: rol atama, tüm yayınlar ve paylaşımlar, bağlantı ayarları, denetim kaydı |
| **Rapor tasarımcısı (builder)** | Rapor tasarlar (İhtiyaç → Veri → Tasarım), Vitrin'e yayınlar, kendi yayınlarını paylaşır; Sorgu Çalıştır, Veri Erişimim, Veri Modeli |
| **İzleyici (viewer)** | Yalnız Vitrin: kendisine ya da grubuna açılan raporları görür (SQL ve sohbet görünmez) |

**Veri rolü** ayrı bir eksendir (`config/policy.toml [roles.*]`): şema / tablo izinleri, kişisel veri, satır limiti.
Vitrin'de her izleyici raporu **kendi veri rolüyle** görür. Yetkisi olmayan görselde "Bu veriye erişim yetkiniz yok" yazar,
raporun geri kalanı çalışır.

Rol çözümü (ilk bulunan kazanır):
1. `.env` `PLATFORM_ADMINS`: her zaman admin. İlk kurulumda kendinizi buraya yazın.
2. Yönetim sayfasındaki atamalar: önce kullanıcı, sonra AD grubu.
3. `policy.toml [platform]` içindeki users / groups.
4. Varsayılan: izleyici.

## Kurulum adımları

1. **Platform veritabanı**: SQL Server'da boş bir veritabanı oluşturun (ör. `BI_Lens_Meta`).
   - Hizmet hesabına `db_datareader` ve `db_datawriter` verin. Tabloların ilk açılışta otomatik oluşması için `db_ddladmin` de verin.
   - Ya da DBA `backend/meta/schema.sql` dosyasını çalıştırır.
   - Bağlantı Ayarları → **Platform Veritabanı** kartından ya da `.env` içinde `META_ODBC=...` ile tanımlanır.
2. **`.env`**:
   ```
   PLATFORM_MODE=server
   PLATFORM_ADMINS=KURUM\ali
   ```
3. **Uygulamayı yalnız yerelde dinlet**: `uvicorn app.main:app --host 127.0.0.1 --port 8000`.
   Kimlik başlığı yalnız ters proxy'den gelmelidir. Ağdan doğrudan 8000'e erişilirse başlık sahte olabilir.
4. **IIS (ters proxy + Windows kimlik doğrulaması)**:
   - IIS'e *URL Rewrite* ve *Application Request Routing (ARR)* kurun; ARR'de proxy'yi etkinleştirin.
   - Site için **Windows Authentication** açık, **Anonymous** kapalı olmalı.
   - Yeniden yazma kuralı: tüm istekleri `http://127.0.0.1:8000/{R:1}` adresine yönlendirin.
     Sunucu değişkeni `HTTP_X_REMOTE_USER` = `{LOGON_USER}` olsun (*Allowed Server Variables* listesine ekleyin).
   - İsteğe bağlı: AD grupları `X-Remote-Groups` başlığıyla virgülle iletilebilir. Önerilen yol ise LDAP'tır (`.env` `LDAP_URL` vb.).
     LDAP grup bilgileri 15 dakika önbellekte tutulur.
   - Uzun süren sohbet yanıtları (SSE) için ARR'de *response buffering* kapalı, zaman aşımı en az 5 dk olmalı.
5. **Hizmet olarak çalıştırma**: NSSM ya da Görev Zamanlayıcı ile `start.bat`. Uvicorn yalnız 127.0.0.1'e bağlanmalı.
6. **Mevcut raporları taşıma**: oturum dosyalarını (`backend/sessions/...`) sunucuya kopyalayın, sonra şunu çalıştırın:
   ```
   .venv\Scripts\python.exe -m scripts.migrate_to_platform --owner "KURUM\ali" --dry-run
   ```
   Çıktı doğruysa `--dry-run` olmadan tekrar çalıştırın. "Canlıda" raporlar Vitrin'de sürüm 1 olur, paylaşım boş başlar.

## Akış

- Tasarımcı raporu tasarlar ve üst çubuktaki **Yayınla**'ya basar. Açıklama, sürüm notu ve paylaşım (kullanıcı ya da AD grubu,
  isteğe bağlı "Dışa aktarabilir") girilir.
- Yayın, tasarımın **değişmez bir kopyasıdır**. Tasarımda sonradan yapılan değişiklikler yeniden yayınlanana kadar izleyiciye gitmez.
  Üst çubuk, yayında olmayan değişiklik olduğunu gösterir.
- Yayından kaldırılan rapor izleyicilere görünmez. Sürüm geçmişi saklanır, tekrar yayınlanabilir.
- Yayındaki bir raporun tasarımı silinemez; önce yayından kaldırılmalıdır.
- Denetim kaydı (Yönetim → Denetim kaydı) şu olayları tutar: yayın, kaldırma, paylaşım değişikliği, rol ataması, rapor açma,
  dışa aktarma, sahiplik devri.
