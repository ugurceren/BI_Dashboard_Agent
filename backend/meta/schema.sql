-- BI Lens platform (Vitrin) meta veritabanı — SQL Server
-- Kurulum: boş bir veritabanı oluşturun (ör. BI_Lens_Meta), BI Lens hizmet hesabına db_datareader + db_datawriter
-- (+ ilk açılışta tabloları kendisi oluştursun isterseniz db_ddladmin) verin. Ya da bu scripti DBA çalıştırır.
-- Uygulama tablolar yoksa bu dosyayı kendisi çalıştırır (GO ile ayrılmış adımlar).

CREATE TABLE dbo.meta_info (
  k nvarchar(64) NOT NULL PRIMARY KEY,
  v nvarchar(400) NOT NULL
);
GO
CREATE TABLE dbo.role_assignments (
  principal_type varchar(10) NOT NULL,          -- user | group
  principal      nvarchar(256) NOT NULL,        -- küçük harf: domain\kullanici ya da AD grup adı (CN)
  platform_role  varchar(20) NULL,              -- admin | builder | viewer (NULL: değiştirme)
  data_role      nvarchar(64) NULL,             -- policy.toml [roles.*] (NULL: değiştirme)
  granted_by     nvarchar(256) NOT NULL,
  granted_at     varchar(32) NOT NULL,
  CONSTRAINT pk_role_assignments PRIMARY KEY (principal_type, principal)
);
GO
CREATE TABLE dbo.published_reports (
  report_id       varchar(32) NOT NULL PRIMARY KEY,
  session_id      varchar(32) NOT NULL,
  owner           nvarchar(256) NOT NULL,
  owner_name      nvarchar(256) NULL,
  title           nvarchar(200) NOT NULL,
  description     nvarchar(2000) NULL,
  domains         nvarchar(1000) NULL,          -- JSON dizi
  current_version int NOT NULL,
  status          varchar(10) NOT NULL,         -- active | retired
  created_at      varchar(32) NOT NULL,
  updated_at      varchar(32) NOT NULL
);
GO
CREATE UNIQUE INDEX ux_published_session ON dbo.published_reports(session_id);
GO
CREATE TABLE dbo.report_versions (
  report_id     varchar(32) NOT NULL,
  version       int NOT NULL,
  spec_json     nvarchar(max) NOT NULL,
  datasets_json nvarchar(max) NOT NULL,
  notes         nvarchar(2000) NULL,
  published_by  nvarchar(256) NOT NULL,
  published_at  varchar(32) NOT NULL,
  CONSTRAINT pk_report_versions PRIMARY KEY (report_id, version)
);
GO
CREATE TABLE dbo.report_grants (
  report_id      varchar(32) NOT NULL,
  principal_type varchar(10) NOT NULL,
  principal      nvarchar(256) NOT NULL,
  can_export     bit NOT NULL DEFAULT 0,
  granted_by     nvarchar(256) NOT NULL,
  granted_at     varchar(32) NOT NULL,
  CONSTRAINT pk_report_grants PRIMARY KEY (report_id, principal_type, principal)
);
GO
CREATE TABLE dbo.audit_events (
  id           bigint IDENTITY(1,1) NOT NULL PRIMARY KEY,
  ts           varchar(32) NOT NULL,
  username     nvarchar(256) NOT NULL,
  event        varchar(64) NOT NULL,
  report_id    varchar(32) NULL,
  details_json nvarchar(max) NULL
);
GO
CREATE INDEX ix_audit_ts ON dbo.audit_events(ts);
GO
