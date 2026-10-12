// SQL biçimlendirme (T-SQL): yalnız boşluk / satır kırılımı / anahtar kelime büyük harfi değişir, sorgunun anlamı değişmez.
// Tarayıcıda çalışır (sorgu sunucuya gitmez). sql-formatter'ın iki kusuru sonradan düzeltilir:
// tablo ipucu (WITH (NOLOCK)) ayrı satıra iner, TOP n SELECT'ten sonraki satıra kayar.
import { formatDialect, transactsql } from "sql-formatter";

const HINT = /\n[ \t]*WITH\n[ \t]*\(\s*((?:NOLOCK|READUNCOMMITTED|READCOMMITTED|READPAST|UPDLOCK|HOLDLOCK|ROWLOCK|PAGLOCK|TABLOCK|NOWAIT|FORCESEEK|FORCESCAN|INDEX\s*\([^)]*\))(?:\s*,\s*(?:[A-Z]+|INDEX\s*\([^)]*\)))*)\s*\)/gi;
const TOP = /^([ \t]*)SELECT(\s+DISTINCT)?\n([ \t]+)(TOP\s*(?:\(\s*\d+\s*\)|\d+)(?:\s+PERCENT)?(?:\s+WITH\s+TIES)?)\s+/gim;

/** Biçimlendirilmiş SQL; ayrıştırılamazsa hata fırlatır (metin olduğu gibi kalmalı). */
export function formatSql(sql: string): string {
  if ((sql.match(/\/\*/g) ?? []).length > (sql.match(/\*\//g) ?? []).length) throw new Error("Kapanmamış /* yorum */ var.");
  const out = formatDialect(sql, { dialect: transactsql, keywordCase: "upper", functionCase: "upper", dataTypeCase: "upper", tabWidth: 4 })
    .replace(HINT, (_m, h: string) => ` WITH (${h.toUpperCase()})`)
    .replace(TOP, (_m, ind: string, dist: string | undefined, ind2: string, top: string) => `${ind}SELECT${dist ?? ""} ${top}\n${ind2}`);
  // güvenlik: boşluk ve harf büyüklüğü dışında hiçbir karakter değişmemeli (değiştiyse uygulanmaz)
  const bare = (s: string) => s.replace(/\s+/g, "").toUpperCase();
  if (bare(out) !== bare(sql)) throw new Error("Biçimlendirme sorgu metnini değiştirecekti; uygulanmadı.");
  return out.trimEnd() + "\n";
}
