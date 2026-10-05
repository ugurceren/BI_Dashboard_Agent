# Veri ambarı ortak kuralları ve SQL kullanım standartları

<!--
Veri kaynağında dictionary.toml'daki nolock_databases listesinden (varsayılan EDWDM, EDW) bir veritabanı seçiliyken
yerel LLM'in talimatına eklenir ("Her aşama" tüm aşamalarda, "Veri hazırlığı" yalnız veri aşamasında); aynı listedeki
veritabanlarının sorgularına WITH (NOLOCK) otomatik eklenir.
Kod değiştirmeden düzenlenebilir.
-->

## Her aşama — müşteri anahtarları (EDW / EDWDM)

- **`CustomerPartyId` vekil (surrogate) anahtardır:** müşteri düzeyinde tabloları / view'ları birleştirirken JOIN'i
  bunun üzerinden yap (EDW ↔ EDWDM arası dahil): `ON b.CustomerPartyId = a.CustomerPartyId`. Tekil müşteri sayısı
  için de `COUNT(DISTINCT CustomerPartyId)` kullan. Maskeli view'larda da maskelenmez.
- **Gerçek müşteri numarası `CustomerId` ya da `AccountNumber`'dır** (view'a göre biri ya da ikisi bulunur):
  * kullanıcı "müşteri numarası / müşteri no / hesap numarası" dediğinde bu kolonları anla;
  * raporda müşteriyi tanıtmak gerektiğinde (ör. top 100 müşteri listesi) bunları göster; gruplamayı yine
    `CustomerPartyId`'ye göre yap, numarayı yanına ekle;
  * tek müşteri ya da müşteri listesi filtresi bu kolonlarla yazılır (veri tipine dikkat: varchar ise tırnaklı);
  * JOIN anahtarı olarak KULLANMA (vekil anahtar değildir; maskeli view'larda maskelenmiş olabilir).
- Hangi view'da hangisinin bulunduğunu get_table_details ile kontrol et; olmayan kolonu varsayma.

## Her aşama — tarih alanı seçimi (DataDate olmayan nesneler)

- `DataDate` kolonu olmayan tablo / view'lar günlük anlık görüntü DEĞİLDİR: her kayıt bir kez yer alır, günlük resim
  tutmaz. get_table_details bunlar için "date_choice" bilgisi ve aday tarih kolonlarını verir.
- Dönem filtresi, "son tarih", trend ya da karşılaştırma gerekiyorsa adaylar arasından anlama en uygun tarih alanını
  seç (ör. işlem / valör / açılış / vade tarihi; iş adı ve açıklamasına bak).
- Seçtiğin alanı ve nedenini kullanıcıya kısa ve net söyle, **devam etmek isteyip istemediğini sor** ve yanıtını bekle.
  Ör.: "Mevduat view'ında günlük görüntü yok; dönem için `ValueDate` (valör tarihi) alanını kullanmayı öneriyorum.
  Bu alanla devam edeyim mi, yoksa başka bir tarih mi kullanalım? (Adaylar: ValueDate, OpenDate, MaturityDate)"
- Kullanıcı onay verene ya da başka bir alan seçene kadar o nesneyle dataset kaydetme. Kullanıcı başka bir alan
  söylerse onu kullan; aday yoksa bunu söyle ve tarih filtresi olmadan devam etmeyi öner.
- Bu kararı (seçilen tarih alanı) ihtiyaç aşamasında gereksinimlere not et; veri aşamasında aynı soruyu tekrar sorma.
- Bu nesnelerde tutar / sayı için gün seçmek gerekmez (kayıtlar tekrarlanmaz); "son tarih itibarıyla" isteniyorsa
  seçilen tarih alanında `<=` ile filtrele.

## Veri hazırlığı — SQL kullanım standartları

Kaynak: Veri Ambarı Kullanım Kılavuzu, bölüm 8 (Veri Yönetimi Servisi). Yazdığın HER sorguda (veri aralığı kontrolü,
örnek veri, test ve dataset sorguları dahil) bu kurallara uy; sistemin tüm kullanıcılar için performanslı çalışmasına
ve veri güvenliğine doğrudan katkı sağlar.

Temel kurallar (çok önemli):
- **WHERE koşulu zorunludur.** Hiçbir sorgu WHERE koşulu olmadan çalıştırılmamalıdır; koşulsuz sorgular milyonlarca
  satırı tarar. Anlık görüntü view'larında DataDate koşulu bunu karşılar; diğer tablolarda da anlamlı bir koşul ekle
  (en azından `WHERE <anahtar> IS NOT NULL`).
- **Müşteri bazlı filtreleme:** Müşteriye özel veri sorgulanırken mutlaka `PartyId` ya da `AccountNumber` WHERE
  koşuluna eklenmelidir (view'larda `CustomerPartyId`; kullanıcı müşteri numarası verdiyse `CustomerId` /
  `AccountNumber` — bkz. müşteri anahtarları). Raporlar toplulaştırılmıştır; tek müşteri sorgusu yalnız kullanıcı açıkça isterse.
- **Sayısal olmayan alanlarda sayısal işlem yapma:** metin alanlarda sıralama, ortalama alma gibi sayısal işlemler
  yapma; bunun için sayısal tipe dönüştürülmüş alanları kullan.
- **Veri tipi uyumu:** filtre, koşul ve karşılaştırmaları kolonun veri tipine uygun yaz. Ör. `FECId` varchar'dır:
  `FECId = '1'` (tırnaklı), `FECId = 1` değil. Kolon tipini get_table_details'teki "type" alanından kontrol et.
- **WITH (NOLOCK):** sorgudaki her tablo / view referansına `WITH (NOLOCK)` ekle (alt sorgular ve CTE'ler dahil):
  `FROM CLT.vRepurchaseGuarantee v WITH (NOLOCK)`, `JOIN COR.vCalendar c WITH (NOLOCK) ON …`.
  (Unutursan sistem sorguyu çalıştırmadan önce otomatik ekler; yine de kendin yaz.)
- **Maskelenen alanlarla JOIN yapma:** Datamart katmanındaki bazı view'lar maskeli versiyonlar içerir; maskeli
  alanlarla join ciddi performans sorunu ve hatalı veri üretir. Maskelenmeyen anahtarları (ör. CustomerPartyId) kullan;
  emin değilsen o alanı join'de kullanma ve Veri Yönetimi servisinden teyit alınmasını öner.

Performansa etki eden kullanımlar (önemli):
- **SELECT * kullanma:** yalnızca ihtiyaç duyduğun kolonları listele (örnek veri bakarken de: `SELECT TOP 10 a, b, c …`).
- **Gereksiz DISTINCT kullanma:** yalnızca gerçekten tekil kayıt gerektiğinde.
- **COUNT(1) tercih et:** satır sayımında `COUNT(*)` yerine `COUNT(1)`; tekil sayım için `COUNT(DISTINCT anahtar)`.
- **UNION yerine UNION ALL:** yinelenen kayıt kontrolü gerekmiyorsa `UNION ALL` kullan.
- **JOIN sıralaması:** önce bütün INNER JOIN'leri yaz, ardından LEFT JOIN'leri; LEFT → INNER → LEFT gibi karışık
  sıralama yapma.
- **JOIN kolonlarında veri tipi uyumu:** birleştirilen kolonların veri tipleri aynı olmalı; uyumsuzluk gizli
  dönüşümlere ve ciddi performans kaybına neden olur.
- **JOIN kolonlarında fonksiyon kullanma:** JOIN koşullarında `DATEADD`, `FORMAT`, `CAST`, `CONVERT` gibi dönüşümler
  kullanma (index kullanımını engeller). Dönüşüm gerekiyorsa önce CTE'de hesapla ya da hazır kolonu kullan.
- **Tarih / periyot için vCalendar:** gün ve dönem (hafta, ay, çeyrek …) bilgisi için `COR.vCalendar` view'ını kullan.
  Kılavuzda `EDWDM.COR.vCalendar` yazar: EDWDM bağlı (birincil) veritabanıysa `COR.vCalendar`, ek veritabanıysa
  `EDWDM.COR.vCalendar` yaz (araç sonuçlarındaki adı kullan).
