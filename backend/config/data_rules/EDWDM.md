# EDWDM veri kuralları

<!--
Bu dosya, veri kaynağı EDWDM veritabanına bağlıyken yerel LLM'in sistem talimatına eklenir
(dosya adı = veritabanı adı; başka bir veritabanı için aynı biçimde <VERITABANI>.md eklenebilir).
"## " ile başlayan bölümler aşamaya göre kullanılır:
  "Her aşama" → tüm aşamalar · "İhtiyaç analizi" → 1. aşama · "Veri hazırlığı" → 2. aşama · "Tasarım" → 3. aşama
Kod değiştirmeden düzenlenebilir; değişiklik bir sonraki LLM adımında geçerli olur.
-->

## Her aşama

EDWDM katmanındaki view'ların iki özelliği var; ikisini de her zaman dikkate al:

1. **Yetki seviyeleri (view son ekleri).** Her veri seti için, personel ve müşteri tanımlayıcı verilerinin
   görünürlüğüne göre 4 farklı view vardır. Son ek, view'ın hangi yetki seviyesi için tasarlandığını gösterir:
   | Son ek | Açıklama | Hedef kullanıcı |
   |---|---|---|
   | (ek yok) | Personel dahil tüm müşteriler açık görünür. | Yetki verilmiş iş birimleri |
   | Masked | Personel dahil tüm müşteriler maskeli görünür. | YZ ekipleri, 3. taraf firmalar, Veri Bilimi ve Nimet projeleri |
   | PersonnelExcluded | Personel kayıtları filtrelenmiş, diğer müşteri verileri açık görünür. | Yetki verilmiş iş birimleri |
   | PersonnelMasked | Personeli ayırt edici veriler maskeli, müşteri verileri açık görünür. | Yetki verilmiş iş birimleri |
   Dört view'ın kolonları ve anlamı aynıdır; yalnız görünürlük farklıdır.

2. **Günlük anlık görüntü (DataDate).** View'lar takvim tablosuyla joinlenmiştir: her kayıt, geçerli olduğu HER GÜN
   için ayrı bir satır olarak tekrarlanır. Hangi günün verisi olduğu `DataDate` kolonundadır.
   Gün seçmeden toplanan tutar ya da sayı, gün sayısıyla çarpılmış (ör. 1 yılda ~365 kat) ve YANLIŞ olur.

## İhtiyaç analizi

- Rapor bir durum ölçüsü içeriyorsa (bakiye, teminat tutarı, limit, müşteri / sözleşme sayısı …) kullanıcıya
  **hangi tarih itibarıyla** istediğini sor; varsayılan öner: "en son veri günü itibarıyla, ay sonu trendiyle".
  Kullanıcı dashboard'da tarihi sonradan da değiştirebilir ("Veri tarihi" seçici); bunu da belirt.
- Trend isteniyorsa dönem sonu (ay sonu) değerlerinin mi, dönem ortalamasının mı istendiğini netleştir;
  varsayılan: ay sonu.
- Personel kayıtlarının rapora dahil olup olmadığı önemliyse sor (PersonnelExcluded view'ları personeli içermez).
  Kullanıcıya yetkisinin üstünde bir seviye önerme; hangi seviyelere erişebildiği aşağıda "Erişilebilir yetki
  seviyeleri" bölümünde yazar.
- Gereksinimlere (save_requirements) veri tarihi kararını ve kullanılacak yetki seviyesini not olarak ekle.

## Veri hazırlığı

Yetki seviyesi seçimi:
- search_dictionary ve get_table_details yalnızca bu kullanıcının SELECT yetkisi olan view'ları gösterir; başka
  bir seviyenin view adını kendin türetip (ör. ada "Masked" ekleyerek) sorgulama.
- Aynı veri setinin birden çok seviyesine erişim varsa ihtiyaca en uygun ve en az kişisel veri açan seviyeyi seç:
  toplulaştırılmış raporlarda maskeli seviye yeterlidir; personel hariç isteniyorsa PersonnelExcluded.
  Seçtiğin view'ı ve neden seçtiğini kullanıcıya bir cümleyle söyle.
- Bir raporun bütün dataset'lerinde aynı yetki seviyesini kullan (ör. hepsi Masked); seviyeleri karıştırma.
- Maskeli kolonları (ad, numara vb.) kırılım ya da filtre olarak kullanma; maskeli değerler gruplanınca anlamsızdır.
- Maskeli kolonlarla JOIN yapma (ciddi performans sorunu ve hatalı veri üretir). Maskeli seviyede join için
  maskelenmeyen anahtarları kullan (ör. CustomerPartyId); bir kolonun maskelenip maskelenmediğinden emin değilsen
  join'de kullanma ve kullanıcıya Veri Yönetimi servisinden teyit almasını öner.

DataDate kuralları (sistem SUM / COUNT sorgularında bunu otomatik kontrol eder ve uymayanı reddeder):
- Güncel durum / KPI: tek gün seç →
  `WHERE v.DataDate = (SELECT MAX(DataDate) FROM <aynı view> WITH (NOLOCK))` ve kullanıcıya hangi gün itibarıyla olduğunu söyle.
- Önce `SELECT MIN(DataDate), MAX(DataDate) FROM <view> WITH (NOLOCK) WHERE DataDate IS NOT NULL` ile veri aralığını
  öğren; "bu yıl" gibi ifadeleri buna göre yorumla.
- Aylık trend: her ayın tek gününü al → `WHERE v.DataDate = EOMONTH(v.DataDate)` (ay sonu) ve
  `GROUP BY EOMONTH(v.DataDate)`. Henüz bitmemiş ayın ay sonu yoktur; gerekirse son ay için MAX(DataDate) kullan.
  Günlük seri için `GROUP BY v.DataDate`. Dönem ortalaması isteniyorsa AVG kullan.
  Hafta / ay / çeyrek gibi dönem bilgisi gerekiyorsa `COR.vCalendar` view'ını kullan (önce get_table_details ile
  kolonlarına bak); view'ın tarih kolonuyla doğrudan birleştir: `JOIN COR.vCalendar c WITH (NOLOCK) ON c.<tarih kolonu> = v.DataDate`.
- Önceki dönemle karşılaştırma: aynı günün geçen yılki / geçen ayki değeri →
  `DATEADD(year, -1, (SELECT MAX(DataDate) FROM <view>))` gibi tek bir gün seç.
- İki EDWDM view'ını birleştirirken anahtar kolonlarla birlikte `DataDate` kolonlarını da eşle
  (`a.DataDate = b.DataDate`); yoksa satırlar gün × gün çoğalır.
- "Kaç müşteri / kaç sözleşme" sorularında tek gün seç ve `COUNT(DISTINCT anahtar)` kullan (satır sayısı için
  `COUNT(1)`); gün seçmeden
  COUNT(DISTINCT) "dönem boyunca en az bir gün var olan" kayıtları sayar — bunu isteniyorsa açıkça söyle.
- Dataset'lerde DataDate'i (ya da ay sonu tarihini) bir kolon olarak döndür ki dashboard hangi günün verisini
  gösterdiğini yazabilsin.

## Veri hazırlığı — SQL kullanım standartları

Kaynak: Veri Ambarı Kullanım Kılavuzu, bölüm 8 (Veri Yönetimi Servisi). Yazdığın HER sorguda (veri aralığı kontrolü,
örnek veri, test ve dataset sorguları dahil) bu kurallara uy; sistemin tüm kullanıcılar için performanslı çalışmasına
ve veri güvenliğine doğrudan katkı sağlar.

Temel kurallar (çok önemli):
- **WHERE koşulu zorunludur.** Hiçbir sorgu WHERE koşulu olmadan çalıştırılmamalıdır; koşulsuz sorgular milyonlarca
  satırı tarar. Anlık görüntü view'larında DataDate koşulu bunu karşılar; diğer tablolarda da anlamlı bir koşul ekle
  (en azından `WHERE <anahtar> IS NOT NULL`).
- **Müşteri bazlı filtreleme:** Müşteriye özel veri sorgulanırken mutlaka `PartyId` ya da `AccountNumber` WHERE
  koşuluna eklenmelidir. (Raporlar toplulaştırılmıştır; tek müşteri sorgusu yalnız kullanıcı açıkça isterse.)
- **Sayısal olmayan alanlarda sayısal işlem yapma:** metin alanlarda sıralama, ortalama alma gibi sayısal işlemler
  yapma; bunun için sayısal tipe dönüştürülmüş alanları kullan.
- **Veri tipi uyumu:** filtre, koşul ve karşılaştırmaları kolonun veri tipine uygun yaz. Ör. `FECId` varchar'dır:
  `FECId = '1'` (tırnaklı), `FECId = 1` değil. Kolon tipini get_table_details'teki "type" alanından kontrol et.
- **WITH (NOLOCK):** sorgudaki her tablo / view referansına `WITH (NOLOCK)` ekle (alt sorgular ve CTE'ler dahil):
  `FROM CLT.vRepurchaseGuarantee v WITH (NOLOCK)`, `JOIN COR.vCalendar c WITH (NOLOCK) ON …`.
  (Unutursan sistem sorguyu çalıştırmadan önce otomatik ekler; yine de kendin yaz.)
- **Maskelenen alanlarla JOIN yapma** (yukarıdaki yetki seviyesi kurallarına bak).

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
  Not: kılavuzda `EDWDM.COR.vCalendar` yazar; bu uygulamada veritabanı adı yazılmaz (zaten EDWDM'e bağlısın) —
  `COR.vCalendar` yaz.

## Tasarım

- Dashboard alt başlığında verinin hangi gün itibarıyla olduğunu yaz (ör. "04.10.2026 itibarıyla"); trend
  görsellerinde "ay sonu değerleri" olduğunu belirt.
- Kullanıcı "Veri tarihi" seçiciyle başka bir gün seçebilir; başlıkta sabit tarih yerine dataset'teki tarih kolonunu
  kullanmak daha doğrudur.
- Maskeli seviye kullanıldıysa (Masked / PersonnelMasked) bunu raporun açıklamasında ya da alt başlıkta belirt.
