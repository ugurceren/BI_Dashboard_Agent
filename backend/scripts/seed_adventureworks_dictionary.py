"""AdventureWorksDW için SQL Server'da Türkçe bir veri sözlüğü oluşturur.

Sözlük ayrı bir veritabanına (varsayılan BI_Meta) yazılır; AdventureWorks'e dokunulmaz.
Tablolar/kolonlar INFORMATION_SCHEMA'dan, JOIN ilişkileri sys.foreign_keys'ten otomatik okunur;
aşağıdaki sözlükler iş adı, açıklama, eş anlamlı ve kişisel veri (PII) bilgisini ekler.

    python scripts/seed_adventureworks_dictionary.py --server localhost --source-db AdventureWorksDW2025
"""

from __future__ import annotations

import argparse
import re

import pyodbc

DRIVER = "ODBC Driver 18 for SQL Server"

# (iş adı, açıklama, konu alanı, granülarite)
TABLES: dict[str, tuple[str, str, str, str]] = {
    "FactInternetSales": ("İnternet Satışları", "Web sitesi üzerinden bireysel müşterilere yapılan satışlar (sipariş satırı bazında). Ciro, adet, maliyet, kâr analizleri için ana tablo.", "Satış", "Bir satır = bir sipariş satırı"),
    "FactResellerSales": ("Bayi Satışları", "Bayilere (reseller) yapılan satışlar, satış temsilcisi bilgisiyle.", "Satış", "Bir satır = bir sipariş satırı"),
    "FactSalesQuota": ("Satış Kotaları", "Satış temsilcilerinin çeyreklik satış hedefleri.", "Satış", "Bir satır = temsilci × çeyrek"),
    "FactInternetSalesReason": ("İnternet Satış Nedenleri", "İnternet siparişlerinin satın alma nedenleri (köprü tablo).", "Satış", "Bir satır = sipariş satırı × neden"),
    "FactFinance": ("Finans", "Genel muhasebe tutarları: hesap, organizasyon, departman ve senaryo (gerçekleşen/bütçe/tahmin) bazında.", "Finans", "Bir satır = hesap × organizasyon × departman × senaryo × ay"),
    "FactCallCenter": ("Çağrı Merkezi", "Çağrı merkezi günlük vardiya performansı: çağrı, sipariş, sorun, servis seviyesi.", "Operasyon", "Bir satır = gün × vardiya"),
    "FactProductInventory": ("Ürün Stok Hareketleri", "Ürün bazında günlük stok giriş/çıkış ve bakiye.", "Operasyon", "Bir satır = ürün × gün"),
    "FactSurveyResponse": ("Anket Yanıtları", "Müşteri anketlerinde ilgilenilen ürün kategorileri.", "Pazarlama", "Bir satır = bir anket yanıtı"),
    "FactCurrencyRate": ("Döviz Kurları", "Günlük ortalama ve gün sonu döviz kurları (USD bazlı).", "Finans", "Bir satır = para birimi × gün"),
    "DimDate": ("Tarih", "Takvim ve mali takvim boyutu.", "Ortak", "Bir satır = bir gün"),
    "DimProduct": ("Ürün", "Ürün kartı: ad, renk, boyut, model, fiyat, maliyet.", "Ürün", "Bir satır = bir ürün versiyonu"),
    "DimProductSubcategory": ("Ürün Alt Kategorisi", "Ürün alt kategorileri (ör. Mountain Bikes, Helmets).", "Ürün", "Bir satır = bir alt kategori"),
    "DimProductCategory": ("Ürün Kategorisi", "Ana ürün kategorileri: Bikes, Components, Clothing, Accessories.", "Ürün", "Bir satır = bir kategori"),
    "DimCustomer": ("Müşteri", "Bireysel (internet) müşteriler: demografi ve iletişim. Kimlik/iletişim alanları kişisel veridir.", "Müşteri", "Bir satır = bir müşteri"),
    "DimGeography": ("Coğrafya", "Şehir, eyalet/il, ülke ve posta kodu.", "Ortak", "Bir satır = bir şehir/posta kodu"),
    "DimSalesTerritory": ("Satış Bölgesi", "Satış bölgesi, ülke ve bölge grubu (North America, Europe, Pacific).", "Ortak", "Bir satır = bir satış bölgesi"),
    "DimReseller": ("Bayi", "Bayi firmalar: iş tipi, çalışan sayısı, yıllık satış.", "Satış", "Bir satır = bir bayi"),
    "DimPromotion": ("Promosyon", "Kampanya ve indirim tanımları.", "Pazarlama", "Bir satır = bir promosyon"),
    "DimEmployee": ("Çalışan", "Çalışanlar ve satış temsilcileri. Kişisel bilgiler (kimlik, doğum tarihi, iletişim, ücret) hassastır.", "İK", "Bir satır = bir çalışan kaydı"),
    "DimCurrency": ("Para Birimi", "Para birimi kodları ve adları.", "Ortak", "Bir satır = bir para birimi"),
    "DimAccount": ("Hesap Planı", "Muhasebe hesap planı (hiyerarşik).", "Finans", "Bir satır = bir hesap"),
    "DimOrganization": ("Organizasyon", "Şirket organizasyon birimleri (hiyerarşik).", "Finans", "Bir satır = bir birim"),
    "DimDepartmentGroup": ("Departman Grubu", "Departman grupları (hiyerarşik).", "Finans", "Bir satır = bir departman grubu"),
    "DimScenario": ("Senaryo", "Finans senaryosu: Actual (gerçekleşen), Budget (bütçe), Forecast (tahmin).", "Finans", "Bir satır = bir senaryo"),
    "DimSalesReason": ("Satış Nedeni", "Müşterinin satın alma nedeni (fiyat, kalite, promosyon ...).", "Satış", "Bir satır = bir neden"),
}

# Sözlüğe alınmayan kolonlar: çok dilli kopyalar ve görseller
SKIP_COL = re.compile(r"^(Spanish|French|Chinese|Arabic|Hebrew|Thai|German|Japanese)|Photo$|Image$|^LargePhoto$|Description$", re.I)
KEEP_DESC = {"EnglishDescription", "TurkishDescription", "AccountDescription"}

# (tablo, kolon) -> (iş adı, açıklama, rol, varsayılan toplama, eş anlamlılar, pii)
C = dict[tuple[str, str], tuple[str, str, str | None, str | None, str, bool]]
COLUMNS: C = {
    # ---- İnternet satışları (bayi satışlarında da aynı kolonlar var)
    **{(t, "SalesAmount"): ("Satış Tutarı (USD)", "İndirim sonrası net satış tutarı", "measure", "sum", "ciro,satış,gelir,hasılat,revenue,tutar,hacim", False) for t in ("FactInternetSales", "FactResellerSales")},
    **{(t, "OrderQuantity"): ("Sipariş Adedi (ürün)", "Satılan ürün adedi", "measure", "sum", "adet,miktar,satış adedi,quantity", False) for t in ("FactInternetSales", "FactResellerSales")},
    **{(t, "TotalProductCost"): ("Toplam Ürün Maliyeti", "Satılan ürünlerin standart maliyeti", "measure", "sum", "maliyet,cost,smm", False) for t in ("FactInternetSales", "FactResellerSales")},
    **{(t, "ProductStandardCost"): ("Birim Standart Maliyet", "", "measure", "avg", "birim maliyet", False) for t in ("FactInternetSales", "FactResellerSales")},
    **{(t, "UnitPrice"): ("Birim Fiyat", "", "measure", "avg", "fiyat", False) for t in ("FactInternetSales", "FactResellerSales")},
    **{(t, "ExtendedAmount"): ("Brüt Tutar", "İndirim öncesi tutar (adet × birim fiyat)", "measure", "sum", "brüt satış", False) for t in ("FactInternetSales", "FactResellerSales")},
    **{(t, "DiscountAmount"): ("İndirim Tutarı", "", "measure", "sum", "indirim,iskonto", False) for t in ("FactInternetSales", "FactResellerSales")},
    **{(t, "UnitPriceDiscountPct"): ("İndirim Oranı", "0-1 arası", "measure", "avg", "indirim yüzdesi", False) for t in ("FactInternetSales", "FactResellerSales")},
    **{(t, "TaxAmt"): ("Vergi Tutarı", "", "measure", "sum", "vergi,kdv", False) for t in ("FactInternetSales", "FactResellerSales")},
    **{(t, "Freight"): ("Kargo Ücreti", "", "measure", "sum", "nakliye,kargo", False) for t in ("FactInternetSales", "FactResellerSales")},
    **{(t, "SalesOrderNumber"): ("Sipariş No", "Aynı siparişin satırları aynı numarayı taşır; sipariş sayısı için COUNT(DISTINCT)", "key", "count_distinct", "sipariş sayısı,sipariş adedi,order", False) for t in ("FactInternetSales", "FactResellerSales")},
    **{(t, "OrderDate"): ("Sipariş Tarihi", "", "date", None, "tarih,sipariş tarihi", False) for t in ("FactInternetSales", "FactResellerSales")},
    **{(t, "OrderDateKey"): ("Sipariş Tarihi Anahtarı", "DimDate.DateKey ile ilişkili (YYYYMMDD)", "key", None, "", False) for t in ("FactInternetSales", "FactResellerSales")},
    **{(t, "ShipDate"): ("Sevk Tarihi", "", "date", None, "kargo tarihi,sevkiyat", False) for t in ("FactInternetSales", "FactResellerSales")},
    **{(t, "DueDate"): ("Vade Tarihi", "", "date", None, "teslim tarihi", False) for t in ("FactInternetSales", "FactResellerSales")},
    **{(t, "CarrierTrackingNumber"): ("Kargo Takip No", "", "attribute", None, "", False) for t in ("FactInternetSales", "FactResellerSales")},
    **{(t, "CustomerPONumber"): ("Müşteri PO No", "", "attribute", None, "", False) for t in ("FactInternetSales", "FactResellerSales")},
    ("FactInternetSales", "CustomerKey"): ("Müşteri Anahtarı", "DimCustomer ile ilişkili; tekil müşteri için COUNT(DISTINCT)", "key", "count_distinct", "müşteri sayısı,aktif müşteri", False),
    ("FactResellerSales", "ResellerKey"): ("Bayi Anahtarı", "DimReseller ile ilişkili", "key", "count_distinct", "bayi sayısı", False),
    ("FactResellerSales", "EmployeeKey"): ("Satış Temsilcisi Anahtarı", "DimEmployee ile ilişkili", "key", None, "temsilci,satışçı", False),
    # ---- kotalar / finans / çağrı merkezi / stok
    ("FactSalesQuota", "SalesAmountQuota"): ("Satış Kotası", "Temsilcinin çeyrek hedefi", "measure", "sum", "hedef,kota,bütçe", False),
    ("FactSalesQuota", "CalendarYear"): ("Yıl", "", "dimension", None, "", False),
    ("FactSalesQuota", "CalendarQuarter"): ("Çeyrek", "", "dimension", None, "", False),
    ("FactFinance", "Amount"): ("Tutar", "Hesap tutarı (senaryoya göre gerçekleşen/bütçe)", "measure", "sum", "tutar,gider,gelir,bütçe", False),
    ("FactCallCenter", "Calls"): ("Çağrı Sayısı", "", "measure", "sum", "çağrı", False),
    ("FactCallCenter", "Orders"): ("Sipariş Sayısı", "Çağrı merkezinden alınan sipariş", "measure", "sum", "sipariş", False),
    ("FactCallCenter", "IssuesRaised"): ("Sorun Sayısı", "", "measure", "sum", "şikayet,sorun", False),
    ("FactCallCenter", "ServiceGrade"): ("Servis Seviyesi", "0-1 arası servis skoru", "measure", "avg", "servis kalitesi,sla", False),
    ("FactCallCenter", "AverageTimePerIssue"): ("Sorun Başına Ortalama Süre (dk)", "", "measure", "avg", "çözüm süresi", False),
    ("FactCallCenter", "TotalOperators"): ("Toplam Operatör", "", "measure", "avg", "operatör", False),
    ("FactCallCenter", "Shift"): ("Vardiya", "", "dimension", None, "vardiya", False),
    ("FactCallCenter", "WageType"): ("Gün Tipi", "weekday / holiday", "dimension", None, "", False),
    ("FactProductInventory", "UnitsBalance"): ("Stok Bakiyesi", "Gün sonu stok adedi (toplanmaz; dönem sonu değeri alınır)", "measure", "last", "stok,envanter", False),
    ("FactProductInventory", "UnitsIn"): ("Stok Girişi", "", "measure", "sum", "giriş", False),
    ("FactProductInventory", "UnitsOut"): ("Stok Çıkışı", "", "measure", "sum", "çıkış", False),
    ("FactProductInventory", "UnitCost"): ("Birim Maliyet", "", "measure", "avg", "", False),
    ("FactCurrencyRate", "AverageRate"): ("Ortalama Kur", "", "measure", "avg", "kur", False),
    ("FactCurrencyRate", "EndOfDayRate"): ("Gün Sonu Kur", "", "measure", "last", "kur", False),
    # ---- tarih
    ("DimDate", "DateKey"): ("Tarih Anahtarı", "YYYYMMDD", "key", None, "", False),
    ("DimDate", "FullDateAlternateKey"): ("Tarih", "Takvim günü (date)", "date", None, "gün,tarih", False),
    ("DimDate", "CalendarYear"): ("Yıl", "Takvim yılı", "dimension", None, "yıl,yıllık", False),
    ("DimDate", "CalendarQuarter"): ("Çeyrek", "1-4", "dimension", None, "çeyrek,dönem", False),
    ("DimDate", "CalendarSemester"): ("Yarıyıl", "1-2", "dimension", None, "", False),
    ("DimDate", "MonthNumberOfYear"): ("Ay No", "1-12", "dimension", None, "ay,aylık", False),
    ("DimDate", "EnglishMonthName"): ("Ay Adı (İng.)", "", "dimension", None, "ay", False),
    ("DimDate", "WeekNumberOfYear"): ("Hafta", "", "dimension", None, "haftalık", False),
    ("DimDate", "EnglishDayNameOfWeek"): ("Gün Adı (İng.)", "", "dimension", None, "gün", False),
    ("DimDate", "FiscalYear"): ("Mali Yıl", "", "dimension", None, "mali yıl", False),
    ("DimDate", "FiscalQuarter"): ("Mali Çeyrek", "", "dimension", None, "", False),
    # ---- ürün
    ("DimProduct", "EnglishProductName"): ("Ürün Adı", "", "dimension", None, "ürün", False),
    ("DimProduct", "Color"): ("Renk", "", "dimension", None, "renk", False),
    ("DimProduct", "Size"): ("Beden/Boyut", "", "dimension", None, "beden", False),
    ("DimProduct", "ModelName"): ("Model", "", "dimension", None, "model", False),
    ("DimProduct", "ProductLine"): ("Ürün Hattı", "R=Road, M=Mountain, T=Touring, S=Standard", "dimension", None, "hat", False),
    ("DimProduct", "Class"): ("Sınıf", "H=High, M=Medium, L=Low", "dimension", None, "", False),
    ("DimProduct", "ListPrice"): ("Liste Fiyatı", "", "measure", "avg", "fiyat", False),
    ("DimProduct", "StandardCost"): ("Standart Maliyet", "", "measure", "avg", "maliyet", False),
    ("DimProductSubcategory", "EnglishProductSubcategoryName"): ("Alt Kategori", "", "dimension", None, "alt kategori,ürün grubu", False),
    ("DimProductCategory", "EnglishProductCategoryName"): ("Kategori", "Bikes, Components, Clothing, Accessories", "dimension", None, "kategori,ürün kategorisi", False),
    # ---- müşteri (PII)
    ("DimCustomer", "CustomerAlternateKey"): ("Müşteri No", "Müşteriyi tekil tanımlar", "attribute", None, "", True),
    ("DimCustomer", "Title"): ("Unvan", "", "attribute", None, "", True),
    ("DimCustomer", "FirstName"): ("Ad", "Kişisel veri", "attribute", None, "", True),
    ("DimCustomer", "MiddleName"): ("İkinci Ad", "Kişisel veri", "attribute", None, "", True),
    ("DimCustomer", "LastName"): ("Soyad", "Kişisel veri", "attribute", None, "", True),
    ("DimCustomer", "Suffix"): ("Ek", "", "attribute", None, "", True),
    ("DimCustomer", "BirthDate"): ("Doğum Tarihi", "Kişisel veri", "attribute", None, "yaş", True),
    ("DimCustomer", "EmailAddress"): ("E-posta", "Kişisel veri", "attribute", None, "", True),
    ("DimCustomer", "Phone"): ("Telefon", "Kişisel veri", "attribute", None, "", True),
    ("DimCustomer", "AddressLine1"): ("Adres 1", "Kişisel veri", "attribute", None, "", True),
    ("DimCustomer", "AddressLine2"): ("Adres 2", "Kişisel veri", "attribute", None, "", True),
    ("DimCustomer", "Gender"): ("Cinsiyet", "M / F", "dimension", None, "cinsiyet", False),
    ("DimCustomer", "MaritalStatus"): ("Medeni Durum", "M=Evli, S=Bekar", "dimension", None, "medeni hal", False),
    ("DimCustomer", "YearlyIncome"): ("Yıllık Gelir", "", "measure", "avg", "gelir grubu,gelir", False),
    ("DimCustomer", "TotalChildren"): ("Çocuk Sayısı", "", "measure", "avg", "", False),
    ("DimCustomer", "NumberCarsOwned"): ("Araç Sayısı", "", "measure", "avg", "", False),
    ("DimCustomer", "EnglishEducation"): ("Eğitim", "", "dimension", None, "eğitim durumu", False),
    ("DimCustomer", "EnglishOccupation"): ("Meslek", "", "dimension", None, "meslek", False),
    ("DimCustomer", "HouseOwnerFlag"): ("Ev Sahibi mi", "1/0", "dimension", None, "", False),
    ("DimCustomer", "CommuteDistance"): ("İşe Uzaklık", "", "dimension", None, "", False),
    ("DimCustomer", "DateFirstPurchase"): ("İlk Alışveriş Tarihi", "", "date", None, "yeni müşteri,kazanım", False),
    # ---- coğrafya / bölge
    ("DimGeography", "City"): ("Şehir", "", "dimension", None, "şehir,il", False),
    ("DimGeography", "StateProvinceName"): ("Eyalet/İl", "", "dimension", None, "eyalet,il", False),
    ("DimGeography", "EnglishCountryRegionName"): ("Ülke", "", "dimension", None, "ülke", False),
    ("DimGeography", "PostalCode"): ("Posta Kodu", "", "attribute", None, "", False),
    ("DimGeography", "IpAddressLocator"): ("IP Adresi", "Kişisel veri sayılabilir", "attribute", None, "", True),
    ("DimSalesTerritory", "SalesTerritoryRegion"): ("Satış Bölgesi", "ör. Northwest, Canada, France", "dimension", None, "bölge,satış bölgesi,region", False),
    ("DimSalesTerritory", "SalesTerritoryCountry"): ("Bölge Ülkesi", "", "dimension", None, "ülke", False),
    ("DimSalesTerritory", "SalesTerritoryGroup"): ("Bölge Grubu", "North America / Europe / Pacific", "dimension", None, "kıta,bölge grubu,coğrafya", False),
    # ---- bayi
    ("DimReseller", "ResellerName"): ("Bayi Adı", "", "dimension", None, "bayi", False),
    ("DimReseller", "BusinessType"): ("İş Tipi", "Specialty Bike Shop / Value Added Reseller / Warehouse", "dimension", None, "bayi tipi", False),
    ("DimReseller", "NumberEmployees"): ("Çalışan Sayısı", "", "measure", "avg", "", False),
    ("DimReseller", "AnnualSales"): ("Yıllık Satış", "Bayinin beyan ettiği yıllık satış", "measure", "sum", "", False),
    ("DimReseller", "AnnualRevenue"): ("Yıllık Gelir", "", "measure", "sum", "", False),
    ("DimReseller", "Phone"): ("Telefon", "", "attribute", None, "", True),
    ("DimReseller", "AddressLine1"): ("Adres 1", "", "attribute", None, "", True),
    ("DimReseller", "AddressLine2"): ("Adres 2", "", "attribute", None, "", True),
    ("DimReseller", "BankName"): ("Banka", "", "attribute", None, "", True),
    # ---- promosyon
    ("DimPromotion", "EnglishPromotionName"): ("Promosyon Adı", "", "dimension", None, "kampanya,promosyon", False),
    ("DimPromotion", "EnglishPromotionType"): ("Promosyon Tipi", "", "dimension", None, "kampanya tipi", False),
    ("DimPromotion", "EnglishPromotionCategory"): ("Promosyon Kategorisi", "Customer / Reseller / No Discount", "dimension", None, "", False),
    ("DimPromotion", "DiscountPct"): ("İndirim Oranı", "0-1", "measure", "avg", "indirim", False),
    # ---- çalışan (kişisel/İK verisi PII; ad-soyad satış raporlarında kullanılabilir)
    ("DimEmployee", "FirstName"): ("Ad", "Satış temsilcisi raporlarında kullanılabilir", "dimension", None, "temsilci adı", False),
    ("DimEmployee", "LastName"): ("Soyad", "Satış temsilcisi raporlarında kullanılabilir", "dimension", None, "temsilci soyadı", False),
    ("DimEmployee", "Title"): ("Görev", "", "dimension", None, "unvan,pozisyon", False),
    ("DimEmployee", "DepartmentName"): ("Departman", "", "dimension", None, "departman,birim", False),
    ("DimEmployee", "SalesPersonFlag"): ("Satış Temsilcisi mi", "1 = satış temsilcisi", "dimension", None, "satışçı", False),
    ("DimEmployee", "HireDate"): ("İşe Giriş Tarihi", "", "date", None, "kıdem", False),
    ("DimEmployee", "EmployeeNationalIDAlternateKey"): ("Kimlik No", "Kişisel veri", "attribute", None, "", True),
    ("DimEmployee", "ParentEmployeeNationalIDAlternateKey"): ("Yönetici Kimlik No", "Kişisel veri", "attribute", None, "", True),
    ("DimEmployee", "MiddleName"): ("İkinci Ad", "", "attribute", None, "", True),
    ("DimEmployee", "BirthDate"): ("Doğum Tarihi", "Kişisel veri", "attribute", None, "", True),
    ("DimEmployee", "LoginID"): ("Kullanıcı Adı", "", "attribute", None, "", True),
    ("DimEmployee", "EmailAddress"): ("E-posta", "Kişisel veri", "attribute", None, "", True),
    ("DimEmployee", "Phone"): ("Telefon", "Kişisel veri", "attribute", None, "", True),
    ("DimEmployee", "MaritalStatus"): ("Medeni Durum", "Kişisel veri", "attribute", None, "", True),
    ("DimEmployee", "EmergencyContactName"): ("Acil Durum Kişisi", "Kişisel veri", "attribute", None, "", True),
    ("DimEmployee", "EmergencyContactPhone"): ("Acil Durum Telefonu", "Kişisel veri", "attribute", None, "", True),
    ("DimEmployee", "Gender"): ("Cinsiyet", "Kişisel veri (İK)", "attribute", None, "", True),
    ("DimEmployee", "BaseRate"): ("Saatlik Ücret", "Ücret bilgisi — hassas", "attribute", None, "maaş,ücret", True),
    ("DimEmployee", "VacationHours"): ("İzin Saatleri", "", "attribute", None, "", True),
    ("DimEmployee", "SickLeaveHours"): ("Hastalık İzni Saatleri", "", "attribute", None, "", True),
    # ---- diğer boyutlar
    ("DimCurrency", "CurrencyName"): ("Para Birimi", "", "dimension", None, "döviz", False),
    ("DimCurrency", "CurrencyAlternateKey"): ("Para Birimi Kodu", "USD, EUR ...", "dimension", None, "", False),
    ("DimAccount", "AccountDescription"): ("Hesap Adı", "", "dimension", None, "hesap", False),
    ("DimAccount", "AccountType"): ("Hesap Tipi", "Assets, Liabilities, Revenue, Expenditures ...", "dimension", None, "", False),
    ("DimOrganization", "OrganizationName"): ("Organizasyon", "", "dimension", None, "şirket,birim", False),
    ("DimDepartmentGroup", "DepartmentGroupName"): ("Departman Grubu", "", "dimension", None, "departman", False),
    ("DimScenario", "ScenarioName"): ("Senaryo", "Actual / Budget / Forecast", "dimension", None, "bütçe,gerçekleşen,tahmin", False),
    ("DimSalesReason", "SalesReasonName"): ("Satış Nedeni", "", "dimension", None, "neden", False),
    ("DimSalesReason", "SalesReasonReasonType"): ("Neden Tipi", "", "dimension", None, "", False),
}

# Rol yapan boyut ilişkilerinin Türkçe adları (FactXxx.<kolon> → DimDate)
ROLES = {"OrderDateKey": "Sipariş tarihi", "DueDateKey": "Vade tarihi", "ShipDateKey": "Sevk tarihi"}

METRICS = [
    ("internet_sales_amount", "İnternet Satış Tutarı", "İnternet kanalı net satış", "SUM(f.SalesAmount)", "dbo.FactInternetSales", "currency", "internet ciro,online satış"),
    ("reseller_sales_amount", "Bayi Satış Tutarı", "Bayi kanalı net satış", "SUM(r.SalesAmount)", "dbo.FactResellerSales", "currency", "bayi ciro"),
    ("order_count", "Sipariş Sayısı", "Tekil sipariş sayısı", "COUNT(DISTINCT f.SalesOrderNumber)", "dbo.FactInternetSales", "number", "sipariş adedi"),
    ("gross_profit", "Brüt Kâr", "Satış - ürün maliyeti", "SUM(f.SalesAmount - f.TotalProductCost)", "dbo.FactInternetSales", "currency", "kâr,kar,marj"),
    ("gross_margin_pct", "Brüt Kâr Marjı", "(Satış - maliyet) / satış", "SUM(f.SalesAmount - f.TotalProductCost) / NULLIF(SUM(f.SalesAmount), 0)", "dbo.FactInternetSales", "percent", "kâr marjı,kar oranı"),
    ("avg_order_value", "Ortalama Sipariş Tutarı", "Sipariş başına satış", "SUM(f.SalesAmount) / NULLIF(COUNT(DISTINCT f.SalesOrderNumber), 0)", "dbo.FactInternetSales", "currency", "sepet,ortalama sepet"),
    ("active_customers", "Aktif Müşteri", "Dönemde alışveriş yapan tekil müşteri", "COUNT(DISTINCT f.CustomerKey)", "dbo.FactInternetSales", "number", "müşteri sayısı"),
    ("quota_attainment", "Kota Gerçekleşme Oranı", "Bayi satışı / satış kotası (temsilci × çeyrek)", "SUM(r.SalesAmount) / NULLIF(SUM(q.SalesAmountQuota), 0)", "dbo.FactSalesQuota", "percent", "hedef gerçekleşme"),
    ("yoy_growth", "Yıllık Büyüme (YoY)", "(bu yıl - geçen yıl) / geçen yıl", "(cur - prev) / NULLIF(prev, 0)", "", "percent", "büyüme,geçen yıla göre"),
]


def humanize(name: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name.replace("English", "")).strip()


def auto_role(table: str, col: str, dtype: str) -> tuple[str, str | None]:
    if col.endswith("Key") or col.endswith("ID"):
        return "key", None
    if dtype in ("date", "datetime", "datetime2", "smalldatetime"):
        return "date", None
    if table.startswith("Fact") and dtype in ("money", "float", "int", "smallint", "tinyint", "decimal", "numeric", "bigint"):
        return "measure", "sum"
    if dtype in ("nvarchar", "varchar", "nchar", "char", "bit"):
        return "dimension", None
    return "attribute", None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="localhost")
    ap.add_argument("--source-db", default="AdventureWorksDW2025")
    ap.add_argument("--meta-db", default="BI_Meta")
    args = ap.parse_args()
    base = f"DRIVER={{{DRIVER}}};SERVER={args.server};Trusted_Connection=yes;TrustServerCertificate=yes;"

    master = pyodbc.connect(base + "DATABASE=master;", autocommit=True)
    master.execute(f"IF DB_ID(N'{args.meta_db}') IS NULL CREATE DATABASE [{args.meta_db}]")
    master.close()

    src = pyodbc.connect(base + f"DATABASE={args.source_db};").cursor()
    meta_con = pyodbc.connect(base + f"DATABASE={args.meta_db};", autocommit=False)
    meta = meta_con.cursor()
    meta.execute("IF SCHEMA_ID('meta') IS NULL EXEC('CREATE SCHEMA meta')")
    for t in ("dd_tables", "dd_columns", "dd_relationships", "dd_metrics"):
        meta.execute(f"IF OBJECT_ID('meta.{t}') IS NOT NULL DROP TABLE meta.{t}")
    meta.execute("""CREATE TABLE meta.dd_tables (table_name NVARCHAR(256) PRIMARY KEY, business_name NVARCHAR(256),
        description NVARCHAR(2000), subject_area NVARCHAR(128), grain NVARCHAR(256), row_count BIGINT, source_db NVARCHAR(128))""")
    meta.execute("""CREATE TABLE meta.dd_columns (table_name NVARCHAR(256), column_name NVARCHAR(256), business_name NVARCHAR(256),
        description NVARCHAR(2000), data_type NVARCHAR(64), column_role NVARCHAR(32), default_aggregation NVARCHAR(32),
        synonyms NVARCHAR(1000), is_pii BIT, sample_values NVARCHAR(1000), PRIMARY KEY (table_name, column_name))""")
    meta.execute("""CREATE TABLE meta.dd_relationships (relationship_id NVARCHAR(256), from_table NVARCHAR(256),
        from_column NVARCHAR(256), to_table NVARCHAR(256), to_column NVARCHAR(256),
        cardinality NVARCHAR(8) NULL, role NVARCHAR(256) NULL, is_active BIT NULL)""")
    meta.execute("""CREATE TABLE meta.dd_metrics (metric_name NVARCHAR(128) PRIMARY KEY, business_name NVARCHAR(256),
        description NVARCHAR(2000), expression_sql NVARCHAR(2000), base_table NVARCHAR(256), value_format NVARCHAR(32), synonyms NVARCHAR(1000))""")

    n_cols = 0
    for table, (bn, desc, area, grain) in TABLES.items():
        full = f"dbo.{table}"
        rows = src.execute(f"SELECT COUNT_BIG(*) FROM {full}").fetchone()[0]
        meta.execute("INSERT INTO meta.dd_tables VALUES (?, ?, ?, ?, ?, ?, ?)", full, bn, desc, area, grain, rows, args.source_db)
        cols = src.execute("SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME=? ORDER BY ORDINAL_POSITION", table).fetchall()
        for col, dtype in cols:
            if dtype == "varbinary" or (SKIP_COL.search(col) and col not in KEEP_DESC):
                continue
            cur = COLUMNS.get((table, col))
            if cur:
                cbn, cdesc, role, agg, syn, pii = cur
            else:
                role, agg = auto_role(table, col, dtype)
                cbn, cdesc, syn, pii = humanize(col), "", "", False
            sample = ""
            if role == "dimension" and not pii and dtype != "bit":
                vals = src.execute(f"SELECT DISTINCT TOP 8 CAST([{col}] AS NVARCHAR(100)) FROM {full} WHERE [{col}] IS NOT NULL ORDER BY 1").fetchall()
                sample = ", ".join(v[0].strip() for v in vals)[:1000]
            meta.execute("INSERT INTO meta.dd_columns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         full, col, cbn, cdesc, dtype, role, agg, syn, pii, sample)
            n_cols += 1

    # Bileşik FK'lar aynı constraint adını taşır → tek ilişki. Kardinalite boş bırakılır: backend
    # yüklerken unique index metadatası / COUNT DISTINCT ile kendisi çıkarır (gerçek sözlükte de aynı yol).
    fks = src.execute("""
        SELECT fk.name, SCHEMA_NAME(tp.schema_id) + '.' + tp.name, cp.name, SCHEMA_NAME(tr.schema_id) + '.' + tr.name, cr.name
        FROM sys.foreign_key_columns fkc
        JOIN sys.foreign_keys fk ON fk.object_id = fkc.constraint_object_id
        JOIN sys.tables tp  ON tp.object_id = fkc.parent_object_id
        JOIN sys.columns cp ON cp.object_id = fkc.parent_object_id AND cp.column_id = fkc.parent_column_id
        JOIN sys.tables tr  ON tr.object_id = fkc.referenced_object_id
        JOIN sys.columns cr ON cr.object_id = fkc.referenced_object_id AND cr.column_id = fkc.referenced_column_id""").fetchall()
    known = {f"dbo.{t}" for t in TABLES}
    rels = [tuple(r) for r in fks if r[1] in known and r[3] in known and r[1] != r[3]]
    if not any(r[1] == "dbo.FactSalesQuota" and r[2] == "EmployeeKey" for r in rels):
        rels.append(("FK_FactSalesQuota_DimEmployee_manual", "dbo.FactSalesQuota", "EmployeeKey", "dbo.DimEmployee", "EmployeeKey"))
    # Aynı iki tablo arasında birden çok ilişki varsa (rol yapan boyut: sipariş/vade/sevk tarihi) rol adı ver
    per_pair: dict[tuple[str, str], set[str]] = {}
    for rid, ft, _, tt, _ in rels:
        per_pair.setdefault((ft, tt), set()).add(rid)
    for rid, ft, fc, tt, tc in dict.fromkeys(rels):
        role = ""
        if len(per_pair[(ft, tt)]) > 1:
            role = ROLES.get(fc) or humanize(fc).replace(" Key", "")
        # Power BI'daki gibi: rol yapan tarih ilişkilerinden yalnız sipariş tarihi aktif
        active = None if not role else (1 if fc == "OrderDateKey" or len(per_pair[(ft, tt)]) == 1 else 0)
        meta.execute("INSERT INTO meta.dd_relationships VALUES (?, ?, ?, ?, ?, NULL, ?, ?)", rid, ft, fc, tt, tc, role, active)
    rels = {r[0] for r in rels}
    for m in METRICS:
        meta.execute("INSERT INTO meta.dd_metrics VALUES (?, ?, ?, ?, ?, ?, ?)", *m)
    meta_con.commit()
    print(f"OK -> {args.meta_db}: {len(TABLES)} tablo, {n_cols} kolon, {len(set(rels))} ilişki, {len(METRICS)} metrik")


if __name__ == "__main__":
    main()
