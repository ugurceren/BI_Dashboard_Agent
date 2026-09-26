"""Sentetik banka verisi + veri sözlüğü üretir (DuckDB).

Veri sözlüğü `meta` şemasında, gerçekte SQL Server'da duran sözlük tablolarını taklit eder.
Çalıştırma:  python scripts/seed_synthetic.py [--path data/demo.duckdb] [--transactions 400000]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]

REGIONS = {
    "Marmara": ["İstanbul", "Bursa", "Kocaeli", "Tekirdağ"],
    "Ege": ["İzmir", "Manisa", "Aydın", "Denizli"],
    "İç Anadolu": ["Ankara", "Konya", "Kayseri", "Eskişehir"],
    "Akdeniz": ["Antalya", "Adana", "Mersin"],
    "Karadeniz": ["Samsun", "Trabzon", "Zonguldak"],
    "Doğu Anadolu": ["Erzurum", "Malatya", "Van"],
    "Güneydoğu Anadolu": ["Gaziantep", "Diyarbakır", "Şanlıurfa"],
}
REGION_WEIGHT = {
    "Marmara": 3.2, "Ege": 1.5, "İç Anadolu": 1.6, "Akdeniz": 1.2,
    "Karadeniz": 0.8, "Doğu Anadolu": 0.5, "Güneydoğu Anadolu": 0.7,
}

PRODUCTS = [
    (1, "Classic Kart", "Bireysel", "Classic", 0),
    (2, "Gold Kart", "Bireysel", "Gold", 450),
    (3, "Platinum Kart", "Bireysel", "Platinum", 1200),
    (4, "Genç Kart", "Bireysel", "Classic", 0),
    (5, "Business Kart", "Ticari", "Business", 900),
    (6, "KOBİ Kart", "Ticari", "Business", 600),
]

MCC = [
    (1, "Market", 0.24), (2, "Akaryakıt", 0.12), (3, "Giyim", 0.10), (4, "Elektronik", 0.08),
    (5, "Restoran & Kafe", 0.11), (6, "Seyahat", 0.06), (7, "E-ticaret", 0.17),
    (8, "Sağlık", 0.05), (9, "Eğitim", 0.03), (10, "Diğer", 0.04),
]

CHANNELS = [(1, "Fiziki POS"), (2, "Sanal POS"), (3, "Mobil Ödeme"), (4, "Temassız")]

SEGMENTS = ["Bireysel", "Affluent", "Private", "KOBİ"]
AGE_BANDS = ["18-25", "26-35", "36-45", "46-55", "56+"]

MONTHS_TR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos",
             "Eylül", "Ekim", "Kasım", "Aralık"]
DAYS_TR = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]


def build(con: duckdb.DuckDBPyConnection, n_tx: int, n_customers: int) -> None:
    con.execute("SELECT setseed(0.42)")
    con.execute("CREATE SCHEMA IF NOT EXISTS dwh")
    con.execute("CREATE SCHEMA IF NOT EXISTS meta")

    # ---- dim_date
    months = ", ".join(f"'{m}'" for m in MONTHS_TR)
    days = ", ".join(f"'{d}'" for d in DAYS_TR)
    con.execute(f"""
        CREATE OR REPLACE TABLE dwh.dim_date AS
        SELECT
            CAST(strftime(d, '%Y%m%d') AS INTEGER) AS date_key,
            CAST(d AS DATE)                        AS full_date,
            year(d)                                AS year,
            quarter(d)                             AS quarter,
            month(d)                               AS month,
            ([{months}])[month(d)]                 AS month_name,
            strftime(d, '%Y-%m')                   AS year_month,
            weekofyear(d)                          AS week_of_year,
            ([{days}])[isodow(d)]                  AS day_name,
            isodow(d) >= 6                         AS is_weekend
        FROM range(DATE '2024-01-01', DATE '2026-09-01', INTERVAL 1 DAY) t(d)
    """)

    # ---- dim_branch
    rows, bid = [], 1
    for region, cities in REGIONS.items():
        for city in cities:
            for k in range(1, 3 if REGION_WEIGHT[region] < 1 else 4):
                rows.append((bid, f"{city} Şube {k}", city, region))
                bid += 1
    con.execute("CREATE OR REPLACE TABLE dwh.dim_branch (branch_id INTEGER, branch_name VARCHAR, city VARCHAR, region VARCHAR)")
    con.executemany("INSERT INTO dwh.dim_branch VALUES (?, ?, ?, ?)", rows)
    con.execute("CREATE OR REPLACE TABLE _region_weight (region VARCHAR, w DOUBLE)")
    con.executemany("INSERT INTO _region_weight VALUES (?, ?)", list(REGION_WEIGHT.items()))

    # ---- dim_product / dim_merchant_category / dim_channel
    con.execute("CREATE OR REPLACE TABLE dwh.dim_product (product_id INTEGER, product_name VARCHAR, card_type VARCHAR, card_tier VARCHAR, annual_fee_try DECIMAL(10,2))")
    con.executemany("INSERT INTO dwh.dim_product VALUES (?, ?, ?, ?, ?)", PRODUCTS)
    con.execute("CREATE OR REPLACE TABLE dwh.dim_merchant_category (mcc_id INTEGER, category_name VARCHAR)")
    con.executemany("INSERT INTO dwh.dim_merchant_category VALUES (?, ?)", [(i, n) for i, n, _ in MCC])
    con.execute("CREATE OR REPLACE TABLE _mcc_weight (mcc_id INTEGER, w DOUBLE, cum DOUBLE)")
    cum = 0.0
    for i, _, w in MCC:
        cum += w
        con.execute("INSERT INTO _mcc_weight VALUES (?, ?, ?)", [i, w, cum])
    con.execute("CREATE OR REPLACE TABLE dwh.dim_channel (channel_id INTEGER, channel_name VARCHAR)")
    con.executemany("INSERT INTO dwh.dim_channel VALUES (?, ?)", CHANNELS)

    # ---- dim_customer (PII kolonları sahte ama "hassas" olarak işaretli)
    segs = ", ".join(f"'{s}'" for s in SEGMENTS)
    ages = ", ".join(f"'{a}'" for a in AGE_BANDS)
    con.execute(f"""
        CREATE OR REPLACE TABLE dwh.dim_customer AS
        WITH b AS (SELECT branch_id, city, row_number() OVER () AS rn FROM dwh.dim_branch)
        SELECT
            c AS customer_id,
            ([{segs}])[CASE WHEN r < 0.72 THEN 1 WHEN r < 0.90 THEN 2 WHEN r < 0.95 THEN 3 ELSE 4 END] AS segment,
            ([{ages}])[1 + CAST(floor(random() * 5) AS INTEGER)] AS age_band,
            CASE WHEN random() < 0.52 THEN 'Erkek' ELSE 'Kadın' END AS gender,
            (SELECT branch_id FROM b WHERE b.rn = 1 + (c * 7919) % (SELECT count(*) FROM b)) AS home_branch_id,
            'Müşteri ' || c AS customer_name,
            lpad(CAST(10000000000 + c * 7 AS VARCHAR), 11, '0') AS national_id,
            '05' || lpad(CAST((c * 104729) % 1000000000 AS VARCHAR), 9, '0') AS phone_number,
            DATE '2015-01-01' + CAST(floor(random() * 3800) AS INTEGER) AS customer_since
        FROM (SELECT c, random() AS r FROM range(1, {n_customers + 1}) t(c))
    """)

    # ---- fact_card_transaction
    # Mevsimsellik: Aralık/Kasım yüksek, Şubat düşük; yıllık ~%35 nominal büyüme; bölge ağırlıkları.
    con.execute(f"""
        CREATE OR REPLACE TABLE dwh.fact_card_transaction AS
        WITH days AS (
            SELECT date_key, full_date, month, year,
                   (1 + 0.35 * (year - 2024) + 0.35 * (month - 1) / 12.0)
                   * CASE month WHEN 12 THEN 1.30 WHEN 11 THEN 1.22 WHEN 2 THEN 0.86 WHEN 7 THEN 1.08 WHEN 8 THEN 1.10 ELSE 1.0 END
                   * CASE WHEN is_weekend THEN 1.15 ELSE 1.0 END AS day_w
            FROM dwh.dim_date
        ),
        days_cum AS (SELECT *, sum(day_w) OVER (ORDER BY date_key) / sum(day_w) OVER () AS cum FROM days),
        br AS (
            SELECT b.branch_id, w.w, sum(w.w) OVER (ORDER BY b.branch_id) / sum(w.w) OVER () AS cum
            FROM dwh.dim_branch b JOIN _region_weight w USING (region)
        ),
        seed AS (
            SELECT i AS transaction_id, random() AS r_day, random() AS r_br, random() AS r_mcc,
                   random() AS r_prod, random() AS r_ch, random() AS r_amt, random() AS r_inst,
                   1 + CAST(floor(random() * {n_customers}) AS INTEGER) AS customer_id
            FROM range(1, {n_tx + 1}) t(i)
        )
        SELECT
            s.transaction_id,
            (SELECT min(date_key) FROM days_cum d WHERE d.cum >= s.r_day) AS date_key,
            s.customer_id,
            CASE WHEN s.r_prod < 0.38 THEN 1 WHEN s.r_prod < 0.62 THEN 2 WHEN s.r_prod < 0.72 THEN 3
                 WHEN s.r_prod < 0.84 THEN 4 WHEN s.r_prod < 0.93 THEN 5 ELSE 6 END AS product_id,
            (SELECT min(branch_id) FROM br WHERE br.cum >= s.r_br) AS branch_id,
            (SELECT min(mcc_id) FROM _mcc_weight m WHERE m.cum >= s.r_mcc) AS mcc_id,
            CASE WHEN s.r_ch < 0.42 THEN 1 WHEN s.r_ch < 0.66 THEN 2 WHEN s.r_ch < 0.78 THEN 3 ELSE 4 END AS channel_id,
            s.r_amt AS _r_amt,
            s.r_inst AS _r_inst
        FROM seed s
    """)
    # Tutarı kategoriye/ürüne/zamana göre ölçekle (ayrı adımda: korelasyonlu alt sorgular hızlı kalsın)
    con.execute("""
        CREATE OR REPLACE TABLE dwh.fact_card_transaction AS
        SELECT
            f.transaction_id, f.date_key, f.customer_id, f.product_id, f.branch_id, f.mcc_id, f.channel_id,
            CAST(round(
                exp(ln(CASE f.mcc_id WHEN 1 THEN 650 WHEN 2 THEN 1400 WHEN 3 THEN 1600 WHEN 4 THEN 5200
                                     WHEN 5 THEN 750 WHEN 6 THEN 6800 WHEN 7 THEN 1300 WHEN 8 THEN 1500
                                     WHEN 9 THEN 3500 ELSE 900 END) + 0.8 * (f._r_amt * 2 - 1) * 1.6)
                * CASE f.product_id WHEN 3 THEN 1.9 WHEN 2 THEN 1.35 WHEN 5 THEN 2.4 WHEN 6 THEN 2.0 WHEN 4 THEN 0.6 ELSE 1.0 END
                * (1 + 0.35 * (d.year - 2024) + 0.35 * (d.month - 1) / 12.0)
            , 2) AS DECIMAL(14,2)) AS amount_try,
            CASE WHEN f.mcc_id IN (4, 6, 9) AND f._r_inst < 0.55 THEN 3 + CAST(floor(f._r_inst * 17) AS INTEGER) % 10
                 WHEN f._r_inst < 0.12 THEN 2 + CAST(floor(f._r_inst * 40) AS INTEGER) % 4
                 ELSE 1 END AS installment_count,
            (f.mcc_id = 6 AND f._r_inst > 0.6) OR (f.channel_id = 2 AND f._r_inst > 0.93) AS is_international
        FROM dwh.fact_card_transaction f
        JOIN dwh.dim_date d USING (date_key)
    """)

    # ---- fact_card_application (kart başvuruları)
    con.execute("""
        CREATE OR REPLACE TABLE dwh.fact_card_application AS
        WITH s AS (
            SELECT i AS application_id, random() AS r1, random() AS r2, random() AS r3, random() AS r4, random() AS r5
            FROM range(1, 60001) t(i)
        ), d AS (SELECT date_key, row_number() OVER (ORDER BY date_key) AS rn, count(*) OVER () AS n FROM dwh.dim_date),
        b AS (SELECT branch_id, row_number() OVER (ORDER BY branch_id) AS rn, count(*) OVER () AS n FROM dwh.dim_branch)
        SELECT
            s.application_id,
            (SELECT date_key FROM d WHERE d.rn = 1 + CAST(floor(sqrt(s.r1) * (d.n - 1)) AS INTEGER)) AS date_key,
            (SELECT branch_id FROM b WHERE b.rn = 1 + CAST(floor(s.r2 * b.n) AS INTEGER)) AS branch_id,
            1 + CAST(floor(s.r3 * 6) AS INTEGER) AS product_id,
            CASE WHEN s.r4 < 0.55 THEN 3 WHEN s.r4 < 0.80 THEN 2 ELSE 1 END AS channel_id,
            CASE WHEN s.r5 < 0.64 THEN 'Onaylandı' WHEN s.r5 < 0.90 THEN 'Reddedildi' ELSE 'Beklemede' END AS application_status,
            CAST(round(5000 + s.r5 * s.r3 * 150000, -2) AS DECIMAL(14,2)) AS requested_limit_try
        FROM s
    """)

    # ---- fact_loan_disbursement (kredi kullandırımları)
    con.execute(f"""
        CREATE OR REPLACE TABLE dwh.fact_loan_disbursement AS
        WITH s AS (
            SELECT i AS loan_id, random() AS r1, random() AS r2, random() AS r3, random() AS r4,
                   1 + CAST(floor(random() * {n_customers}) AS INTEGER) AS customer_id
            FROM range(1, 45001) t(i)
        ), d AS (SELECT date_key, row_number() OVER (ORDER BY date_key) AS rn, count(*) OVER () AS n FROM dwh.dim_date),
        b AS (SELECT branch_id, row_number() OVER (ORDER BY branch_id) AS rn, count(*) OVER () AS n FROM dwh.dim_branch)
        SELECT
            s.loan_id,
            (SELECT date_key FROM d WHERE d.rn = 1 + CAST(floor(s.r1 * (d.n - 1)) AS INTEGER)) AS date_key,
            s.customer_id,
            (SELECT branch_id FROM b WHERE b.rn = 1 + CAST(floor(s.r2 * b.n) AS INTEGER)) AS branch_id,
            CASE WHEN s.r3 < 0.55 THEN 'İhtiyaç' WHEN s.r3 < 0.72 THEN 'Taşıt' WHEN s.r3 < 0.82 THEN 'Konut' ELSE 'KOBİ' END AS loan_type,
            CAST(round(CASE WHEN s.r3 < 0.55 THEN 20000 + s.r4 * 180000 WHEN s.r3 < 0.72 THEN 150000 + s.r4 * 900000
                            WHEN s.r3 < 0.82 THEN 800000 + s.r4 * 4000000 ELSE 250000 + s.r4 * 2500000 END, -2) AS DECIMAL(16,2)) AS amount_try,
            CASE WHEN s.r3 < 0.55 THEN 12 + 12 * CAST(floor(s.r4 * 3) AS INTEGER) WHEN s.r3 < 0.72 THEN 24 + 12 * CAST(floor(s.r4 * 3) AS INTEGER)
                 WHEN s.r3 < 0.82 THEN 60 + 60 * CAST(floor(s.r4 * 2) AS INTEGER) ELSE 12 + 12 * CAST(floor(s.r4 * 4) AS INTEGER) END AS term_months,
            CAST(round(2.2 + s.r4 * 2.8, 2) AS DECIMAL(5,2)) AS monthly_interest_rate
        FROM s
    """)

    con.execute("DROP TABLE _region_weight")
    con.execute("DROP TABLE _mcc_weight")
    build_dictionary(con)


# --------------------------------------------------------------------------- sözlük
# (table, business_name, description, subject_area, grain)
DD_TABLES = [
    ("dwh.fact_card_transaction", "Kredi Kartı İşlemleri", "Kredi kartlarıyla yapılan her harcama işlemi (satış). Ciro, işlem adedi, taksit analizleri için ana tablo.", "Kartlı Ödemeler", "Bir satır = bir kart işlemi"),
    ("dwh.fact_card_application", "Kart Başvuruları", "Yeni kredi kartı başvuruları ve sonuçları (onay/red).", "Kartlı Ödemeler", "Bir satır = bir başvuru"),
    ("dwh.fact_loan_disbursement", "Kredi Kullandırımları", "Bireysel ve KOBİ kredilerinin kullandırım kayıtları.", "Krediler", "Bir satır = bir kredi kullandırımı"),
    ("dwh.dim_date", "Tarih", "Takvim boyutu: yıl, çeyrek, ay, hafta, gün.", "Ortak", "Bir satır = bir gün"),
    ("dwh.dim_branch", "Şube", "Şube, il ve bölge bilgisi.", "Ortak", "Bir satır = bir şube"),
    ("dwh.dim_product", "Kart Ürünü", "Kredi kartı ürünleri: kart tipi (bireysel/ticari) ve seviyesi.", "Kartlı Ödemeler", "Bir satır = bir ürün"),
    ("dwh.dim_merchant_category", "İşyeri Kategorisi (MCC)", "Harcamanın yapıldığı işyeri sektörü.", "Kartlı Ödemeler", "Bir satır = bir kategori"),
    ("dwh.dim_channel", "Kanal", "İşlemin/başvurunun gerçekleştiği kanal.", "Ortak", "Bir satır = bir kanal"),
    ("dwh.dim_customer", "Müşteri", "Müşteri demografisi ve segmenti. Kimlik bilgileri kişisel veridir (KVKK/GDPR).", "Ortak", "Bir satır = bir müşteri"),
]

# (table, column, business_name, description, data_type, role, default_agg, synonyms, is_pii)
DD_COLUMNS = [
    ("dwh.fact_card_transaction", "transaction_id", "İşlem No", "İşlem benzersiz anahtarı", "BIGINT", "key", "count", "işlem sayısı,işlem adedi", False),
    ("dwh.fact_card_transaction", "date_key", "Tarih Anahtarı", "dim_date.date_key ile ilişkili", "INTEGER", "key", None, "tarih", False),
    ("dwh.fact_card_transaction", "customer_id", "Müşteri No", "dim_customer ile ilişkili", "INTEGER", "key", "count_distinct", "aktif müşteri,kart kullanan müşteri", False),
    ("dwh.fact_card_transaction", "product_id", "Ürün No", "dim_product ile ilişkili", "INTEGER", "key", None, "kart tipi", False),
    ("dwh.fact_card_transaction", "branch_id", "Şube No", "dim_branch ile ilişkili (kartın bağlı şubesi)", "INTEGER", "key", None, "şube", False),
    ("dwh.fact_card_transaction", "mcc_id", "Kategori No", "dim_merchant_category ile ilişkili", "INTEGER", "key", None, "sektör", False),
    ("dwh.fact_card_transaction", "channel_id", "Kanal No", "dim_channel ile ilişkili", "INTEGER", "key", None, "kanal", False),
    ("dwh.fact_card_transaction", "amount_try", "İşlem Tutarı (TL)", "Harcama tutarı, Türk Lirası", "DECIMAL", "measure", "sum", "satış,satış tutarı,ciro,harcama,hacim,tutar", False),
    ("dwh.fact_card_transaction", "installment_count", "Taksit Sayısı", "1 = peşin", "INTEGER", "measure", "avg", "taksit,taksitli", False),
    ("dwh.fact_card_transaction", "is_international", "Yurt Dışı İşlem", "İşlem yurt dışında/yabancı işyerinde mi", "BOOLEAN", "attribute", None, "yurtdışı,yabancı", False),

    ("dwh.fact_card_application", "application_id", "Başvuru No", "Başvuru anahtarı", "BIGINT", "key", "count", "başvuru sayısı", False),
    ("dwh.fact_card_application", "date_key", "Tarih Anahtarı", "Başvuru tarihi", "INTEGER", "key", None, "", False),
    ("dwh.fact_card_application", "branch_id", "Şube No", "", "INTEGER", "key", None, "", False),
    ("dwh.fact_card_application", "product_id", "Ürün No", "Başvurulan kart ürünü", "INTEGER", "key", None, "", False),
    ("dwh.fact_card_application", "channel_id", "Kanal No", "Başvuru kanalı", "INTEGER", "key", None, "", False),
    ("dwh.fact_card_application", "application_status", "Başvuru Durumu", "Onaylandı / Reddedildi / Beklemede", "VARCHAR", "dimension", None, "onay,red,onay oranı", False),
    ("dwh.fact_card_application", "requested_limit_try", "Talep Edilen Limit (TL)", "", "DECIMAL", "measure", "avg", "limit", False),

    ("dwh.fact_loan_disbursement", "loan_id", "Kredi No", "", "BIGINT", "key", "count", "kredi adedi", False),
    ("dwh.fact_loan_disbursement", "date_key", "Tarih Anahtarı", "Kullandırım tarihi", "INTEGER", "key", None, "", False),
    ("dwh.fact_loan_disbursement", "customer_id", "Müşteri No", "", "INTEGER", "key", "count_distinct", "", False),
    ("dwh.fact_loan_disbursement", "branch_id", "Şube No", "", "INTEGER", "key", None, "", False),
    ("dwh.fact_loan_disbursement", "loan_type", "Kredi Türü", "İhtiyaç / Taşıt / Konut / KOBİ", "VARCHAR", "dimension", None, "kredi tipi,ürün", False),
    ("dwh.fact_loan_disbursement", "amount_try", "Kredi Tutarı (TL)", "Kullandırılan anapara", "DECIMAL", "measure", "sum", "kullandırım,hacim,tutar", False),
    ("dwh.fact_loan_disbursement", "term_months", "Vade (Ay)", "", "INTEGER", "measure", "avg", "vade", False),
    ("dwh.fact_loan_disbursement", "monthly_interest_rate", "Aylık Faiz (%)", "", "DECIMAL", "measure", "avg", "faiz,oran", False),

    ("dwh.dim_date", "date_key", "Tarih Anahtarı", "YYYYMMDD", "INTEGER", "key", None, "", False),
    ("dwh.dim_date", "full_date", "Tarih", "", "DATE", "date", None, "gün,tarih", False),
    ("dwh.dim_date", "year", "Yıl", "", "INTEGER", "dimension", None, "yıllık", False),
    ("dwh.dim_date", "quarter", "Çeyrek", "1-4", "INTEGER", "dimension", None, "çeyreklik,dönem", False),
    ("dwh.dim_date", "month", "Ay No", "1-12", "INTEGER", "dimension", None, "", False),
    ("dwh.dim_date", "month_name", "Ay Adı", "Türkçe ay adı", "VARCHAR", "dimension", None, "ay", False),
    ("dwh.dim_date", "year_month", "Yıl-Ay", "YYYY-MM, aylık trendler için", "VARCHAR", "dimension", None, "aylık,ay,trend", False),
    ("dwh.dim_date", "week_of_year", "Hafta", "", "INTEGER", "dimension", None, "haftalık", False),
    ("dwh.dim_date", "day_name", "Gün Adı", "", "VARCHAR", "dimension", None, "", False),
    ("dwh.dim_date", "is_weekend", "Hafta Sonu", "", "BOOLEAN", "attribute", None, "", False),

    ("dwh.dim_branch", "branch_id", "Şube No", "", "INTEGER", "key", None, "", False),
    ("dwh.dim_branch", "branch_name", "Şube Adı", "", "VARCHAR", "dimension", None, "şube", False),
    ("dwh.dim_branch", "city", "İl", "", "VARCHAR", "dimension", None, "şehir,il", False),
    ("dwh.dim_branch", "region", "Bölge", "Coğrafi bölge", "VARCHAR", "dimension", None, "bölge,bölgesel", False),

    ("dwh.dim_product", "product_id", "Ürün No", "", "INTEGER", "key", None, "", False),
    ("dwh.dim_product", "product_name", "Ürün Adı", "", "VARCHAR", "dimension", None, "ürün,kart", False),
    ("dwh.dim_product", "card_type", "Kart Tipi", "Bireysel / Ticari", "VARCHAR", "dimension", None, "ticari,bireysel", False),
    ("dwh.dim_product", "card_tier", "Kart Seviyesi", "Classic / Gold / Platinum / Business", "VARCHAR", "dimension", None, "seviye,segment", False),
    ("dwh.dim_product", "annual_fee_try", "Yıllık Aidat (TL)", "", "DECIMAL", "measure", "avg", "aidat", False),

    ("dwh.dim_merchant_category", "mcc_id", "Kategori No", "", "INTEGER", "key", None, "", False),
    ("dwh.dim_merchant_category", "category_name", "İşyeri Kategorisi", "Sektör adı", "VARCHAR", "dimension", None, "sektör,kategori,mcc", False),

    ("dwh.dim_channel", "channel_id", "Kanal No", "", "INTEGER", "key", None, "", False),
    ("dwh.dim_channel", "channel_name", "Kanal Adı", "Fiziki POS / Sanal POS / Mobil Ödeme / Temassız", "VARCHAR", "dimension", None, "kanal,pos,online,dijital", False),

    ("dwh.dim_customer", "customer_id", "Müşteri No", "", "INTEGER", "key", "count_distinct", "", False),
    ("dwh.dim_customer", "segment", "Müşteri Segmenti", "Bireysel / Affluent / Private / KOBİ", "VARCHAR", "dimension", None, "segment", False),
    ("dwh.dim_customer", "age_band", "Yaş Grubu", "", "VARCHAR", "dimension", None, "yaş", False),
    ("dwh.dim_customer", "gender", "Cinsiyet", "", "VARCHAR", "dimension", None, "", False),
    ("dwh.dim_customer", "home_branch_id", "Ana Şube No", "dim_branch ile ilişkili", "INTEGER", "key", None, "", False),
    ("dwh.dim_customer", "customer_name", "Müşteri Adı", "Kişisel veri", "VARCHAR", "attribute", None, "", True),
    ("dwh.dim_customer", "national_id", "TCKN", "Kişisel veri", "VARCHAR", "attribute", None, "kimlik", True),
    ("dwh.dim_customer", "phone_number", "Telefon", "Kişisel veri", "VARCHAR", "attribute", None, "", True),
    ("dwh.dim_customer", "customer_since", "Müşteri Olma Tarihi", "", "DATE", "date", None, "", False),
]

DD_RELATIONSHIPS = [
    ("dwh.fact_card_transaction", "date_key", "dwh.dim_date", "date_key"),
    ("dwh.fact_card_transaction", "customer_id", "dwh.dim_customer", "customer_id"),
    ("dwh.fact_card_transaction", "product_id", "dwh.dim_product", "product_id"),
    ("dwh.fact_card_transaction", "branch_id", "dwh.dim_branch", "branch_id"),
    ("dwh.fact_card_transaction", "mcc_id", "dwh.dim_merchant_category", "mcc_id"),
    ("dwh.fact_card_transaction", "channel_id", "dwh.dim_channel", "channel_id"),
    ("dwh.fact_card_application", "date_key", "dwh.dim_date", "date_key"),
    ("dwh.fact_card_application", "branch_id", "dwh.dim_branch", "branch_id"),
    ("dwh.fact_card_application", "product_id", "dwh.dim_product", "product_id"),
    ("dwh.fact_card_application", "channel_id", "dwh.dim_channel", "channel_id"),
    ("dwh.fact_loan_disbursement", "date_key", "dwh.dim_date", "date_key"),
    ("dwh.fact_loan_disbursement", "customer_id", "dwh.dim_customer", "customer_id"),
    ("dwh.fact_loan_disbursement", "branch_id", "dwh.dim_branch", "branch_id"),
    ("dwh.dim_customer", "home_branch_id", "dwh.dim_branch", "branch_id"),
]

# (metric_name, business_name, description, expression_sql, base_table, format, synonyms)
DD_METRICS = [
    ("card_sales_amount", "Kart Satış Tutarı", "Toplam kredi kartı harcama tutarı", "SUM(t.amount_try)", "dwh.fact_card_transaction", "currency", "ciro,satış,harcama hacmi"),
    ("card_tx_count", "Kart İşlem Adedi", "Toplam işlem sayısı", "COUNT(*)", "dwh.fact_card_transaction", "number", "işlem sayısı"),
    ("avg_ticket", "Ortalama Sepet", "İşlem başına ortalama tutar", "SUM(t.amount_try) / NULLIF(COUNT(*), 0)", "dwh.fact_card_transaction", "currency", "ortalama işlem tutarı,sepet"),
    ("active_card_customers", "Aktif Kart Müşterisi", "Dönemde en az bir işlem yapan müşteri sayısı", "COUNT(DISTINCT t.customer_id)", "dwh.fact_card_transaction", "number", "aktif müşteri"),
    ("installment_share", "Taksitli İşlem Payı", "Taksitli işlemlerin tutar içindeki payı", "SUM(CASE WHEN t.installment_count > 1 THEN t.amount_try ELSE 0 END) / NULLIF(SUM(t.amount_try), 0)", "dwh.fact_card_transaction", "percent", "taksit oranı"),
    ("application_approval_rate", "Başvuru Onay Oranı", "Onaylanan / toplam sonuçlanan başvuru", "SUM(CASE WHEN a.application_status = 'Onaylandı' THEN 1 ELSE 0 END) * 1.0 / NULLIF(SUM(CASE WHEN a.application_status <> 'Beklemede' THEN 1 ELSE 0 END), 0)", "dwh.fact_card_application", "percent", "onay oranı"),
    ("loan_disbursement_amount", "Kredi Kullandırım Tutarı", "Toplam kullandırılan kredi", "SUM(l.amount_try)", "dwh.fact_loan_disbursement", "currency", "kredi hacmi"),
    ("yoy_growth", "Yıllık Büyüme (YoY)", "Aynı dönemin geçen yılına göre değişim oranı: (bu yıl - geçen yıl) / geçen yıl", "(cur - prev) / NULLIF(prev, 0)", "", "percent", "büyüme,geçen yıla göre,yoy"),
]


def build_dictionary(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE OR REPLACE TABLE meta.dd_tables (
            table_name VARCHAR, business_name VARCHAR, description VARCHAR,
            subject_area VARCHAR, grain VARCHAR, row_count BIGINT)""")
    for t, bn, desc, area, grain in DD_TABLES:
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        con.execute("INSERT INTO meta.dd_tables VALUES (?, ?, ?, ?, ?, ?)", [t, bn, desc, area, grain, n])

    con.execute("""
        CREATE OR REPLACE TABLE meta.dd_columns (
            table_name VARCHAR, column_name VARCHAR, business_name VARCHAR, description VARCHAR,
            data_type VARCHAR, column_role VARCHAR, default_aggregation VARCHAR, synonyms VARCHAR,
            is_pii BOOLEAN, sample_values VARCHAR)""")
    for t, c, bn, desc, dt, role, agg, syn, pii in DD_COLUMNS:
        sample = ""
        if role in ("dimension",) and not pii:
            vals = con.execute(f"SELECT DISTINCT CAST({c} AS VARCHAR) FROM {t} ORDER BY 1 LIMIT 8").fetchall()
            sample = ", ".join(v[0] for v in vals if v[0] is not None)
        con.execute("INSERT INTO meta.dd_columns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [t, c, bn, desc, dt, role, agg, syn, pii, sample])

    con.execute("""
        CREATE OR REPLACE TABLE meta.dd_relationships (
            from_table VARCHAR, from_column VARCHAR, to_table VARCHAR, to_column VARCHAR)""")
    con.executemany("INSERT INTO meta.dd_relationships VALUES (?, ?, ?, ?)", DD_RELATIONSHIPS)

    con.execute("""
        CREATE OR REPLACE TABLE meta.dd_metrics (
            metric_name VARCHAR, business_name VARCHAR, description VARCHAR,
            expression_sql VARCHAR, base_table VARCHAR, value_format VARCHAR, synonyms VARCHAR)""")
    con.executemany("INSERT INTO meta.dd_metrics VALUES (?, ?, ?, ?, ?, ?, ?)", DD_METRICS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=str(ROOT / "data" / "demo.duckdb"))
    ap.add_argument("--transactions", type=int, default=400_000)
    ap.add_argument("--customers", type=int, default=40_000)
    args = ap.parse_args()
    path = Path(args.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    build(con, args.transactions, args.customers)
    for (t,) in con.execute("SELECT table_name FROM meta.dd_tables").fetchall():
        print(f"{t:40s} {con.execute(f'SELECT count(*) FROM {t}').fetchone()[0]:>10,}")
    con.close()
    print(f"OK -> {path}")


if __name__ == "__main__":
    main()
