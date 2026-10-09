// Tanıtım içeriği (tek kaynak): yöneticilere sunum slaytları ve rol rol kullanım kılavuzu.
// Ekran görüntüleri: public/tanitim/*.jpg (npm run shots ile demo verisinden üretilir). Metinde **kalın** desteklenir.

export type Role = "herkes" | "tasarimci" | "yonetici";

export const ROLE_LABEL: Record<Role, string> = { herkes: "Herkes", tasarimci: "Rapor tasarımcısı", yonetici: "Yönetici" };

export type Art = "flow" | "arch" | "roles" | "security" | "value" | "roadmap" | "end";

export type Slide = {
  id: string;
  kicker?: string;
  title: string;
  lead?: string;
  bullets?: string[];
  shot?: string;            // public/tanitim/<shot>.jpg
  art?: Art;
  notes: string;            // konuşmacı notu (N)
};

export type Block =
  | { kind: "p"; text: string }
  | { kind: "steps"; items: string[] }
  | { kind: "list"; items: string[] }
  | { kind: "examples"; title?: string; items: string[] }
  | { kind: "shot"; src: string; caption: string }
  | { kind: "tip" | "warn"; text: string }
  | { kind: "table"; head: string[]; rows: string[][] };

export type GuideSection = { id: string; title: string; roles: Role[]; summary: string; blocks: Block[] };

export const shotUrl = (name: string) => `${import.meta.env.BASE_URL}tanitim/${name}.jpg`;

// ---------------------------------------------------------------- sunum
export const SLIDES: Slide[] = [
  {
    id: "giris", kicker: "BI Lens", title: "Raporu anlatın, BI Lens tasarlasın.",
    lead: "Doğal dille rapor tasarlayan, kurumun verisine güvenle bağlanan ve raporları yetkiye göre paylaştıran rapor platformu.",
    shot: "01-giris",
    notes: "Açılış: BI Lens'in üç yüzü var — Vitrin (yayınlanmış raporlar), Tasarım (agent ile rapor üretimi) ve Kontrol Paneli (yönetim). Sunum boyunca bu üçünü gezeceğiz.",
  },
  {
    id: "deger", kicker: "Neden", title: "Rapor talebinden yayına: haftalar değil, bir oturum.",
    art: "value",
    bullets: [
      "Rapor talepleri kuyrukta bekliyor; aynı SQL farklı ekiplerde tekrar yazılıyor.",
      "Her rapor ayrı tasarlanıyor: tutarsız tanımlar, tutarsız görünüm.",
      "Yetki, kişisel veri (KVKK) ve veri tarihi kuralları kişiden kişiye uygulanıyor.",
      "BI Lens: ihtiyaç konuşulur, **onaylı veri sözlüğünden** SQL üretilir, kurallar **sistemde** uygulanır, rapor Vitrin'de yetkiye göre paylaşılır.",
    ],
    notes: "Değer önerisi: hız (tasarım süresi), tutarlılık (aynı sözlük, aynı kurallar), güvenlik (kurallar kişiye değil sisteme bağlı).",
  },
  {
    id: "akis", kicker: "Nasıl çalışır", title: "Üç faz: İhtiyaç → Veri → Tasarım",
    art: "flow", shot: "02-ihtiyac-model",
    bullets: [
      "**İhtiyaç:** kullanıcı ne görmek istediğini anlatır; agent KPI, kırılım ve dönemi netleştirir.",
      "**Veri:** agent veri sözlüğünden tabloları bulur, ilişkileri önerir; **kullanıcı onaylamadan** SQL yazılmaz.",
      "**Tasarım:** dashboard oluşur; değişiklikler sohbetle yapılır, her adım kayıtlı ve geri dönülebilir.",
    ],
    notes: "Ekranda veri fazı: agent tabloları ve yeni ilişki önerisini sunup onay soruyor. Onay olmadan kayıt yok — bu, kontrolün kullanıcıda kaldığını gösterir.",
  },
  {
    id: "tasarim", kicker: "Tasarım", title: "Sohbetle dashboard",
    shot: "03-tasarim",
    bullets: [
      "Gerçek veriyle KPI kartları, grafikler, tablolar; filtreler tüm görsellere model üzerinden yayılır.",
      "\"Bölge grafiğini halka yap\", \"KPI kartını lacivert yap\", \"ürün tablosunu yeni sayfaya taşı\" gibi isteklerle düzenlenir.",
      "Yapılamayan istekler açıkça söylenir; sistem \"yaptım\" denip yapılmayan değişikliği engeller.",
    ],
    notes: "Solda konuşma ve agent'ın attığı her adım (araç satırları), sağda canlı dashboard. Her araç adımı tıklanınca ayrıntısı görülebilir.",
  },
  {
    id: "guvenlik", kicker: "Güvenlik", title: "Kurallar kişide değil, sistemde",
    art: "security",
    bullets: [
      "**Yalnız okuma:** her SQL doğrulanır; yazma / şema değiştirme sorguları reddedilir.",
      "**Rol bazlı veri erişimi** ve **kişisel veri (PII)** koruması; izleyici raporu kendi veri rolüyle görür.",
      "**Veri tarihi (T-1) kuralları:** günlük anlık görüntülerde gün seçilmeden toplama yapılmaz.",
      "**Denetim kaydı:** kim, ne zaman, hangi raporu açtı / yayınladı.",
      "**Dil modeli kurum içinde** (yerel sunucu / kurum ağ geçidi): veri dışarı çıkmaz.",
    ],
    notes: "Yöneticiler için kritik slayt: güvenlik kuralları prompt'a güvenmiyor, kodda uygulanıyor. LLM yanılsa bile SQL doğrulayıcı ve yetki katmanı engelliyor.",
  },
  {
    id: "model", kicker: "Veri modeli", title: "Onaylı sözlük ve ilişkiler",
    shot: "05-model",
    bullets: [
      "Tablolar ve kolonlar kurumsal **veri sözlüğünden** gelir (iş adı, açıklama, rol).",
      "İlişkiler: **onaylı ortak model**, yalnız bu rapora özel ya da SQL JOIN'lerinden çıkarılan.",
      "Filtreler Power BI gibi model üzerinden tüm görsellere yayılır.",
    ],
    notes: "Model sekmesi: rapordaki tablolar ve ilişkileri. Yeni ilişkiyi agent önerir, kullanıcı ortak modele mi yalnız rapora mı kaydedileceğine karar verir.",
  },
  {
    id: "gorseller", kicker: "Görseller", title: "14 görsel türü, akıllı kurallar",
    shot: "10-koyu-tema-sayfalar",
    bullets: [
      "KPI, çizgi, alan, çubuk, combo, pasta / halka, ısı haritası, huni, gösterge, ağaç haritası, dağılım, tablo, metin.",
      "Grafik türü **veriye göre** seçilir: 8'den fazla dilim pasta olmaz, zaman ekseni sıralanmaz, \"ilk 10\" otomatik sınırlanır.",
      "Sayfalar, koyu / açık tema, KPI kart stili; Türkçe sayı biçimleri.",
    ],
    notes: "Bu pano iki sayfalı ve koyu temalı; Satış Tutarı kartı sohbetle 'lacivert zemin, beyaz yazı, büyük değer' yapıldı.",
  },
  {
    id: "vitrin", kicker: "Vitrin", title: "Yayınla, paylaş, yetkiye göre gör",
    shot: "12-vitrin-rapor",
    bullets: [
      "Tasarımcı raporu **sürüm** olarak yayınlar; kişiye ya da **AD grubuna** paylaşır.",
      "İzleyici raporu **kendi veri rolüyle** görür; SQL ve konuşma geçmişi izleyiciye gitmez.",
      "Raporlar iş alanlarına göre gruplanır; HTML olarak indirilebilir.",
    ],
    notes: "Vitrin, rapor tüketicilerinin tek girişi. Tasarımda yapılan değişiklik yeniden yayınlanana kadar izleyiciye yansımaz.",
  },
  {
    id: "roller", kicker: "Roller", title: "Kim neyi görür?",
    art: "roles",
    notes: "Platform rolü (ne yapabilir) ile veri rolü (hangi veriyi görür) ayrı. Atama kişiye ya da AD grubuna yapılır; kişi ataması grubunkinden önce gelir.",
  },
  {
    id: "kontrol", kicker: "Kontrol Paneli", title: "Yönetim tek yerde",
    shot: "16-yonetim",
    bullets: [
      "**Roller:** kişi / AD grubu → platform rolü ve veri rolü.",
      "**Yayınlar** ve **denetim kaydı**.",
      "**Bağlantı ayarları:** veri kaynağı, veri sözlüğü, dil modeli; şifreler Windows DPAPI ile şifreli saklanır.",
    ],
    notes: "Açılış sayfasındaki Kontrol Paneli kutusu bağlantıların canlı durumunu da gösterir.",
  },
  {
    id: "mimari", kicker: "Mimari", title: "Kurum içinde, tek sunucu",
    art: "arch",
    notes: "Tarayıcı → BI Lens (FastAPI + React) → SQL Server (salt-okunur) ve veri sözlüğü. LLM kurum içindeki sunucuda. Meta veri (roller, yayınlar) ayrı bir SQL Server veritabanında ya da SQLite'ta.",
  },
  {
    id: "yol", kicker: "Yol haritası", title: "Kalite ve üretime geçiş",
    art: "roadmap",
    bullets: [
      "**Kalite:** 300+ birim testi, tarayıcıda uçtan uca testler, gerçek dil modeliyle tasarım testleri.",
      "**Üretim:** ortak sunucu (IIS + Windows kimlik doğrulaması), AD / LDAP grupları, meta veritabanı yedeği.",
      "**Yaygınlaştırma:** tasarımcı eğitimi, onaylı sözlüğün genişletilmesi, rapor envanterinin taşınması.",
    ],
    notes: "Sonraki adımlar: pilot birim, sunucu kurulumu, AD entegrasyonu, eğitim.",
  },
  {
    id: "son", kicker: "Teşekkürler", title: "Sorular?",
    art: "end",
    lead: "Kullanım kılavuzu her zaman açılış sayfasından ve uygulamanın sol menüsündeki Yardım'dan açılır.",
    notes: "Kapanış: canlı demo istenirse açılış sayfasından Tasarım'a geçip 'Demo dashboard yükle' ile hızlı bir örnek gösterilebilir.",
  },
];

export const ROLE_TABLE = {
  head: ["", "İzleyici", "Rapor tasarımcısı", "Yönetici"],
  rows: [
    ["Vitrin'de paylaşılan raporları görme", "✓", "✓", "✓"],
    ["Rapor tasarlama, sorgu çalıştırma", "", "✓", "✓"],
    ["Yayınlama ve paylaşma", "", "✓", "✓"],
    ["Rol atama, bağlantı ayarları, denetim", "", "", "✓"],
    ["Gördüğü veri", "Kendi veri rolü", "Kendi veri rolü", "Kendi veri rolü"],
  ],
};

// ---------------------------------------------------------------- kılavuz
export const GUIDE: GuideSection[] = [
  {
    id: "baslarken", title: "Başlarken", roles: ["herkes"], summary: "Açılış sayfası, üç bölüm ve gezinme.",
    blocks: [
      { kind: "shot", src: "01-giris", caption: "Açılış sayfası: Vitrin, Tasarım ve Kontrol Paneli." },
      { kind: "list", items: [
        "**Vitrin:** size paylaşılmış, yayınlanmış raporlar.",
        "**Tasarım:** agent ile yeni rapor tasarlama, rapor envanteri, sorgu ve veri modeli (tasarımcılar).",
        "**Kontrol Paneli:** roller, yayınlar, denetim kaydı ve bağlantı ayarları (yöneticiler).",
      ] },
      { kind: "tip", text: "Vitrin ya da Tasarım seçiminiz hatırlanır; uygulama bir sonraki açılışta doğrudan o bölümle açılır. Açılış sayfasına sol üstteki **BI Lens** logosundan dönebilirsiniz." },
      { kind: "p", text: "Yetkiniz olmayan bölümler kilitli görünür. Erişim için yöneticinizden rol isteyin." },
    ],
  },
  {
    id: "vitrin", title: "Vitrin'de rapor bulma ve açma", roles: ["herkes"], summary: "Yayınlanmış raporları bulun, filtreleyin, indirin.",
    blocks: [
      { kind: "shot", src: "11-vitrin", caption: "Vitrin: size paylaşılan ve kendi yayınlarınız, iş alanlarına göre." },
      { kind: "steps", items: [
        "Açılış sayfasında **Vitrin**'e tıklayın.",
        "Sol menüden kapsamı seçin (Tümü, Bana paylaşılanlar, Son açılanlar) ya da bir **iş alanına** tıklayın.",
        "Arama kutusuna rapor adı, açıklama ya da sahip yazın.",
        "Rapor kartında **Aç**'a tıklayın.",
      ] },
      { kind: "shot", src: "12-vitrin-rapor", caption: "Rapor görünümü: üstte filtreler, sayfa sekmeleri." },
      { kind: "list", items: [
        "**Filtreler** (dilimleyiciler) tüm görselleri etkiler; birden çok değer seçilebilir.",
        "Bir çubuğa / dilime tıklamak diğer görselleri o değere göre süzer (çapraz filtre).",
        "Görselin sağ üstündeki **Tablo görünümü** düğmesi veriyi tablo olarak gösterir.",
        "**HTML** düğmesi raporu bağımsız bir dosya olarak indirir (yetkiniz varsa).",
      ] },
      { kind: "tip", text: "Rapordaki veri **sizin veri rolünüzle** getirilir: aynı raporu iki kişi farklı kapsamla görebilir." },
    ],
  },
  {
    id: "yeni-rapor", title: "Yeni rapor tasarlama", roles: ["tasarimci"], summary: "İhtiyaç → Veri → Tasarım adımlarıyla rapor oluşturun.",
    blocks: [
      { kind: "steps", items: [
        "**Tasarım → Rapor Envanteri → Yeni rapor**.",
        "**İhtiyaç:** ne görmek istediğinizi anlatın (KPI'lar, kırılımlar, dönem). Agent eksikleri sorar.",
        "**Veri:** agent veri sözlüğünden tabloları bulur ve ilişkileri önerir. Önerdiği tabloları ve ilişkileri onaylayın; ilişkinin **ortak modele** mi **yalnız bu rapora** mı kaydedileceğini söyleyin.",
        "Agent SQL'leri doğrular ve veri kümelerini kaydeder (Veri sekmesinde önizleme).",
        "**Tasarım:** dashboard oluşur. İsterseniz önce tasarımı tarif edin ya da örnek bir dashboard görseli yapıştırın.",
      ] },
      { kind: "shot", src: "02-ihtiyac-model", caption: "Veri fazı: agent tabloları ve ilişki önerisini sunup onay ister." },
      { kind: "examples", title: "İyi bir başlangıç cümlesi", items: [
        "Bayi satışlarını bölge grubu ve aya göre gösteren bir yönetim raporu istiyorum; satış tutarı ve sipariş sayısı yeterli, son iki yıl.",
        "Şube bazında mevduat bakiyelerini son gün itibarıyla, müşteri segmentine göre görmek istiyorum.",
      ] },
      { kind: "tip", text: "Faz adımlarına (İhtiyaç / Veri / Tasarım) tıklayarak geri dönebilir, kayıtlı içerikle ileri geçebilirsiniz." },
      { kind: "shot", src: "04-veri", caption: "Veri sekmesi: veri kümeleri, alanları, SQL'i ve önizlemesi." },
      { kind: "warn", text: "Günlük anlık görüntü tablolarında (DataDate) gün seçilmeden toplama yapılmaz; agent 'son gün' kuralını kendisi uygular." },
    ],
  },
  {
    id: "tasarim-sohbet", title: "Tasarımı sohbetle düzenleme", roles: ["tasarimci"], summary: "Örnek cümleler, desteklenen stiller, grafik kuralları.",
    blocks: [
      { kind: "shot", src: "03-tasarim", caption: "Solda konuşma ve agent adımları, sağda canlı dashboard." },
      { kind: "examples", title: "Örnek istekler", items: [
        "Kategori dağılımını pasta grafik yap.",
        "Aylık trendi alan grafiği olarak göster.",
        "En çok satan 10 ürünü satışa göre sıralı bir tablo olarak ekle.",
        "Satış KPI kartının zeminini lacivert yap, yazılar beyaz olsun, değer büyük görünsün.",
        "Tüm KPI kartlarını mavi tonlarında yap.",
        "'Ürün Detayı' adında yeni sayfa ekle ve ürün tablosunu oraya taşı.",
        "Bayi türü filtresi ekle.",
        "Dashboard'u koyu temaya çevir.",
      ] },
      { kind: "table", head: ["Desteklenen stil", "Nasıl söylenir"], rows: [
        ["KPI zemin / yazı / değer rengi", "\"zemini lacivert, yazılar beyaz\" (renk adı ya da #hex)"],
        ["KPI değer boyutu", "küçük / orta / büyük / çok büyük (sm–xl)"],
        ["KPI sol şerit", "\"solda renkli şerit ekle\""],
        ["Tema", "açık / koyu, vurgu rengi, kart stili, köşe yuvarlaklığı"],
        ["Grafik", "tür, yatay / yığılmış, etiketler, lejant, sıralama, ilk N"],
      ] },
      { kind: "warn", text: "Desteklenmeyen istekler (ör. \"yazı tipini 40 punto yap\") uygulanmaz ve bu açıkça söylenir; en yakın desteklenen seçenek önerilir." },
      { kind: "list", items: [
        "**Grafik kuralları:** 8'den fazla dilimli pasta sıralı çubuğa çevrilir; çok kategorili çubuk yatay olur, 20'den fazlasında ilk 15 gösterilir; zaman ekseni sıralanmaz; farklı birimler (tutar + adet) üst üste yığılmaz.",
        "Kural uygulandığında agent bunu size söyler.",
      ] },
    ],
  },
  {
    id: "sayfalar-filtreler", title: "Sayfalar, filtreler ve Spec", roles: ["tasarimci"], summary: "Çok sayfalı rapor, dilimleyiciler, ileri düzey düzenleme.",
    blocks: [
      { kind: "shot", src: "10-koyu-tema-sayfalar", caption: "İki sayfalı, koyu temalı pano; filtreler tüm sayfalarda geçerlidir." },
      { kind: "list", items: [
        "Görsel sayısı arttıkça raporu sayfalara bölün: ilk sayfa özet (KPI + ana trend), diğerleri detay.",
        "Filtreler model üzerinden tüm görsellere uygulanır; bir görselin filtreden etkilenmemesi yalnız açıkça istenirse ayarlanır.",
        "Rapordaki **Canlı** düğmesi tam sayfa görünümü açar.",
      ] },
      { kind: "shot", src: "06-filtre", caption: "Dilimleyici: birden çok değer seçilebilir." },
      { kind: "shot", src: "07-spec", caption: "Spec sekmesi: rapor tanımını JSON olarak düzenleme (ileri düzey)." },
      { kind: "tip", text: "Spec'te hatalı alan girerseniz kayıt reddedilir ve hata gösterilir; ızgaraya sığmayan konumlar otomatik düzeltilir. Kaydetmek için Ctrl+S." },
    ],
  },
  {
    id: "yayinlama", title: "Yayınlama ve paylaşma", roles: ["tasarimci"], summary: "Raporu Vitrin'e sürüm olarak yayınlayın.",
    blocks: [
      { kind: "shot", src: "08-yayinla", caption: "Yayınla: açıklama, sürüm notu ve kimlerin görebileceği." },
      { kind: "steps", items: [
        "Rapor açıkken üstteki **Yayınla**'ya tıklayın.",
        "Vitrin kartında görünecek **açıklamayı** yazın; isterseniz sürüm notu ekleyin.",
        "**Kimler görebilir?** bölümünde AD grubu ya da kullanıcı ekleyin (ör. BI_Satis_Ekibi, KURUM\\ayse.yilmaz).",
        "**Yayınla**'ya tıklayın. Rapor Vitrin'de yeni sürüm olarak görünür.",
      ] },
      { kind: "tip", text: "Tasarımda sonradan yaptığınız değişiklikler yeniden yayınlayana kadar izleyicilere yansımaz. Yayından kaldırmak için aynı diyaloğu kullanın." },
    ],
  },
  {
    id: "envanter", title: "Rapor envanteri", roles: ["tasarimci"], summary: "Raporlarınızı bulun, statülerini yönetin, dışa aktarın.",
    blocks: [
      { kind: "shot", src: "13-envanter", caption: "Liste görünümü: akıllı tablo." },
      { kind: "list", items: [
        "Üstteki çipler statüye göre süzer: **Fikir → Tasarım → Test → Canlıda**. \"Canlıda\" yalnız Yayınla ile verilir.",
        "Kart / liste görünümü arasında geçiş yapın.",
        "Listede başlığa tıklayarak sıralayın; huni simgesiyle kolon filtresi açın (statü, domain, hedef kitle, Vitrin …).",
        "**Kolonlar** menüsünden ek kolonları (Faz, Sahip, KPI'lar, Oluşturuldu) açın; tercihleriniz hatırlanır.",
        "**CSV indir** görünen satır ve kolonları Excel'e aktarır.",
      ] },
    ],
  },
  {
    id: "sorgu-model", title: "Sorgu Çalıştır ve Veri Modeli", roles: ["tasarimci"], summary: "Yetkili nesneleri sorgulayın, modeli inceleyin.",
    blocks: [
      { kind: "shot", src: "14-sorgu", caption: "Sorgu Çalıştır: yetkili nesneler, SQL editörü, sonuç tablosu." },
      { kind: "list", items: [
        "Soldaki ağaçta yalnız **yetkili** tablo ve view'lar görünür; tıklayınca SELECT editöre eklenir.",
        "Çalıştırmak için **Ctrl+Enter**. Yazma sorguları ve yetkiniz olmayan kişisel veri kolonları reddedilir.",
        "Çalıştırılan SQL (NOLOCK, satır sınırı eklenmiş hali) sonuçla birlikte gösterilir.",
      ] },
      { kind: "shot", src: "05-model", caption: "Veri Modeli: tablolar, ilişkiler ve kaynakları (onaylı / rapora özel / SQL JOIN'den)." },
    ],
  },
  {
    id: "baglanti", title: "Bağlantı ayarları", roles: ["yonetici"], summary: "Veri kaynağı, veri sözlüğü ve dil modeli.",
    blocks: [
      { kind: "shot", src: "15-ayarlar", caption: "Bağlantı Ayarları: veri kaynağı ve veri sözlüğü." },
      { kind: "list", items: [
        "**Veri kaynağı:** SQL Server sunucusu, kimlik doğrulama (Windows ya da SQL kullanıcısı) ve veritabanları. Salt-okunur hesap önerilir.",
        "**Veri sözlüğü:** SQL Server, Excel, MySQL / MariaDB ya da PostgreSQL; tablo / kolon / ilişki tabloları.",
        "**Dil modeli:** adres, model ve anahtar; görsel model (örnek dashboard görselinden tasarım) isteğe bağlı.",
        "Şifre ve anahtarlar Windows **DPAPI** ile şifrelenir ve arayüze geri gönderilmez.",
        "**Ayarları dışa / içe aktar** ile kurulumu başka makineye taşıyın.",
      ] },
      { kind: "warn", text: "Dil modelinin **bağlam uzunluğu en az 32.768 token** olmalı (tasarım fazının talimatları ~11 bin token). LM Studio'da değeri değiştirdikten sonra modeli **yeniden yükleyin**; aksi halde eski değer geçerli kalır." },
    ],
  },
  {
    id: "roller-denetim", title: "Roller, yayınlar ve denetim", roles: ["yonetici"], summary: "Kim neye erişir, kim ne yaptı.",
    blocks: [
      { kind: "shot", src: "16-yonetim", caption: "Yönetim → Roller: kişi ya da AD grubuna platform ve veri rolü." },
      { kind: "table", head: ["Platform rolü", "Ne yapabilir"], rows: [
        ["İzleyici", "Yalnız Vitrin: kendisine paylaşılan raporlar"],
        ["Rapor tasarımcısı", "Tasarım, sorgu, veri modeli, yayınlama ve paylaşma"],
        ["Yönetici", "Her şey: roller, bağlantılar, tüm yayınlar, denetim kaydı"],
      ] },
      { kind: "list", items: [
        "**Veri rolü** (policy.toml) hangi şema / tabloların, kişisel verinin ve kaç satırın görüleceğini belirler.",
        "Kişiye yapılan atama, grubunkinden önce gelir; atama yoksa varsayılan rol İzleyici'dir.",
        "**Yayınlar** sekmesi tüm Vitrin raporlarını; **Denetim kaydı** yayın, açılış ve rol değişikliklerini listeler.",
      ] },
    ],
  },
  {
    id: "prod", title: "Üretime (prod) kurulum kontrol listesi", roles: ["yonetici"], summary: "Ortak sunucuya geçiş adımları.",
    blocks: [
      { kind: "steps", items: [
        "**Sunucu:** Windows Server'a BI Lens'i kurun (`start.bat` çevrimdışı paketlerle kurar); NSSM ya da Görev Zamanlayıcı ile hizmet olarak çalıştırın. Uygulama yalnız 127.0.0.1'e bağlanır, dışarıya IIS açılır.",
        "**Kimlik:** IIS'i (URL Rewrite + ARR) ters proxy olarak Windows kimlik doğrulamasıyla yapılandırın; kullanıcı adı `X-Remote-User`, gruplar `X-Remote-Groups` başlığıyla iletilir. `.env`: `PLATFORM_MODE=server`. Ayrıntı: docs/SERVER.md.",
        "**Sabit yöneticiler:** `.env` → `PLATFORM_ADMINS=KURUM\\kullanici1, KURUM\\kullanici2` (virgülle; ilk kurulumda kilitlenmemek için).",
        "**Meta veritabanı:** roller ve yayınlar için ayrı bir SQL Server veritabanı (Bağlantı Ayarları → Platform veritabanı); düzenli yedek alın.",
        "**Veri erişimi:** salt-okunur servis hesabı; `config/policy.toml`'da veri rollerini ve AD grup eşlemelerini tanımlayın.",
        "**Dil modeli:** kurum içi sunucu adresi ve modeli; bağlam uzunluğu ≥ 32.768.",
        "**Doğrulama:** her rolden bir kullanıcıyla açılış, Vitrin, yayın ve paylaşım akışını deneyin.",
      ] },
      { kind: "tip", text: "Masaüstü (tek kullanıcı) kurulumunda herkes yöneticidir; roller yalnız sunucu modunda uygulanır." },
    ],
  },
  {
    id: "sss", title: "Sorun giderme ve SSS", roles: ["herkes"], summary: "Sık karşılaşılan mesajlar ve çözümleri.",
    blocks: [
      { kind: "table", head: ["Mesaj / durum", "Ne yapmalı"], rows: [
        ["\"Modelin bağlam penceresi çok küçük\"", "Dil modelini daha büyük bağlamla (≥ 32.768) yeniden yükletin; yöneticinize iletin."],
        ["\"Değişiklik uygulanmadı (desteklenmeyen alan)\"", "İstek desteklenmiyor; agent'ın önerdiği desteklenen seçeneği kullanın (Tasarımı sohbetle düzenleme bölümü)."],
        ["İlk sorgular çok yavaş", "Veritabanı soğuk başlıyor olabilir; ilk açılışta 30–90 sn normaldir, sonra hızlanır."],
        ["\"Aynı adımı tekrar tekrar denediğimi fark ettim\"", "İsteği daha somut yazın (görsel adı, alan adı) ya da adımlara bölün."],
        ["Vitrin'de rapor görünmüyor", "Rapor size paylaşılmamış olabilir; rapor sahibinden ya da yöneticinizden isteyin."],
        ["Tasarım kutusu kilitli", "Tasarım yetkiniz yok; yöneticinizden 'Rapor tasarımcısı' rolü isteyin."],
      ] },
    ],
  },
];
