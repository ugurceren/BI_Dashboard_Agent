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

2. **Günlük anlık görüntü (DataDate).** View'ların çoğu takvim tablosuyla joinlenmiştir: her kayıt, geçerli olduğu
   HER GÜN için ayrı bir satır olarak tekrarlanır. Hangi günün verisi olduğu `DataDate` kolonundadır.
   Gün seçmeden toplanan tutar ya da sayı, gün sayısıyla çarpılmış (ör. 1 yılda ~365 kat) ve YANLIŞ olur.
   `DataDate` kolonu OLMAYAN view'lar günlük resim tutmaz (her kayıt bir kez yer alır); bunlarda tarih alanı
   seçimi için "Tarih alanı seçimi" kuralına uy (veri ambarı ortak kuralları).

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
- Maskeli kolonlarla JOIN yapma (ciddi performans sorunu ve hatalı veri üretir; bkz. SQL kullanım standartları). Maskeli seviyede join için
  maskelenmeyen anahtarları kullan (ör. CustomerPartyId); bir kolonun maskelenip maskelenmediğinden emin değilsen
  join'de kullanma ve kullanıcıya Veri Yönetimi servisinden teyit almasını öner.

DataDate kuralları (sistem bunu HER sorguda otomatik kontrol eder ve uymayanı reddeder):
- DataDate'i sabitlemeden hiçbir sorgu atma: SUM / COUNT kadar MAX / MIN / AVG / COUNT(DISTINCT), `TOP n *` örnek
  satır, `DISTINCT` / `GROUP BY` ile değer listesi de YASAK — takvimle çoğaltılmış view'ın tamamını tarar ve günleri
  tekrarlar. Keşif / örnek veri için bile `WHERE v.DataDate = (SELECT MAX(DataDate) FROM <aynı view> WITH (NOLOCK))` ekle.
  Tek istisna yalnız tarih kolonunu okuyan sorgu: `SELECT MIN(DataDate), MAX(DataDate) …`.
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

## Tasarım

- Dashboard alt başlığında verinin hangi gün itibarıyla olduğunu yaz (ör. "04.10.2026 itibarıyla"); trend
  görsellerinde "ay sonu değerleri" olduğunu belirt.
- Kullanıcı "Veri tarihi" seçiciyle başka bir gün seçebilir; başlıkta sabit tarih yerine dataset'teki tarih kolonunu
  kullanmak daha doğrudur.
- Maskeli seviye kullanıldıysa (Masked / PersonnelMasked) bunu raporun açıklamasında ya da alt başlıkta belirt.
