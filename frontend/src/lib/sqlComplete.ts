// SQL editörü IntelliSense: yetkili tablo/view adları + alias duyarlı kolon önerileri (CodeMirror tamamlama kaynağı).
import type { Completion, CompletionContext, CompletionResult } from "@codemirror/autocomplete";
import type { QueryObject, QuerySchema } from "../types";

// nesne tipi: tablo (olgu / boyut / köprü ayrımı gösterilmez) ya da view
export const KIND_LABEL: Record<string, string> = { fact: "Tablo", dimension: "Tablo", bridge: "Tablo", view: "View", table: "Tablo" };

/** "dbo.FactInternetSales" → ["dbo", "FactInternetSales"]; ek veritabanı "EDW.dbo.X" → ["EDW.dbo", "X"] */
export const split = (name: string) => {
  const i = name.lastIndexOf(".");
  return i < 0 ? ["dbo", name] : [name.slice(0, i), name.slice(i + 1)];
};

function colInfo(c: QueryObject["columns"][number]) {
  return [c.business_name, c.description, c.blocked ? "🔒 Kişisel veri (PII): bu rolle sorgulanamaz." : ""].filter(Boolean).join("\n") || undefined;
}

/** Tam adlı tablo + FROM'daki tabloların kolonları (alias'sız yazarken de öneri çıksın). */
export function buildCompletion(schema: QuerySchema) {
  const byId = new Map(schema.objects.map((o) => [o.id, o]));
  const tables: Completion[] = schema.objects.map((o) => {
    const [sch, tbl] = split(o.name);
    return {
      label: tbl, apply: `${sch}.${tbl}`, type: o.kind === "view" ? "interface" : "class", boost: 2,
      detail: `${sch} · ${KIND_LABEL[o.kind] ?? o.kind}`, info: [o.business_name, o.description].filter(Boolean).join("\n") || undefined,
    };
  });
  const colsOf = (o: QueryObject, boost: number): Completion[] => o.columns.map((c) => ({
    label: c.name, type: "property", boost: c.blocked ? -50 : boost,
    detail: `${c.type || ""} · ${split(o.name)[1]}${c.blocked ? " · 🔒" : ""}`, info: colInfo(c),
  }));
  const schemas = new Set(schema.allowed_schemas.map((x) => x.toLowerCase()));
  for (const o of schema.objects) {   // ek veritabanı adı da niteleyici: "edw." → lang-sql şema / tablo listesi
    const p = o.name.split(".");
    if (p.length >= 3) { schemas.add(p[0].toLowerCase()); schemas.add(p[1].toLowerCase()); }
  }
  const NOT_ALIAS = new Set(["on", "join", "inner", "left", "right", "full", "cross", "outer", "where", "group", "order", "having", "union", "with", "as"]);
  const WORD = /^[\wçğıöşüÇĞİÖŞÜ]*$/;
  const source = (ctx: CompletionContext): CompletionResult | null => {
    const word = ctx.matchBefore(/[\wçğıöşüÇĞİÖŞÜ]+/);
    const from = word ? word.from : ctx.pos;
    const doc = ctx.state.doc.toString().toLowerCase().replace(/[[\]]/g, "");
    // sorgudaki tablolar ve alias'ları: "dbo.Tablo [AS] alias"
    const used = new Set<string>();
    const alias = new Map<string, string>();
    // "şema.Tablo [AS] alias" ya da ek veritabanı "db.şema.Tablo [AS] alias"
    for (const m of doc.matchAll(/\b(?:([a-z_]\w*)\.)?([a-z_]\w*)\.([a-z_]\w*)\b(?:\s+(?:as\s+)?([a-z_]\w*))?/g)) {
      const id = [m[1] ? `${m[1]}.${m[2]}.${m[3]}` : "", `${m[2]}.${m[3]}`].find((x) => x && byId.has(x));
      if (!id) continue;
      used.add(id);
      alias.set(m[3], id);
      if (m[4] && !NOT_ALIAS.has(m[4])) alias.set(m[4], id);
    }
    // alias. / Tablo. → o tablonun kolonları
    const qual = /([\wçğıöşü]+)\.$/i.exec(ctx.state.sliceDoc(Math.max(0, from - 80), from));
    if (qual) {
      const q = qual[1].toLowerCase();
      if (schemas.has(q)) return null; // dbo. → lang-sql tablo listesi
      const id = alias.get(q);
      return id ? { from, options: colsOf(byId.get(id)!, 0), validFor: WORD } : null;
    }
    if (!word && !ctx.explicit) return null;
    return { from, options: [...[...used].flatMap((id) => colsOf(byId.get(id)!, 5)), ...tables], validFor: WORD };
  };
  // lang-sql ad alanı: şema → tablo; ek veritabanında veritabanı → şema → tablo
  // (kolonlar alias-duyarlı kendi kaynağımızdan gelir, çift öneri olmasın)
  type NS = { [k: string]: NS | Completion[] };
  const ns: NS = {};
  for (const o of schema.objects) {
    const p = o.name.split(".");
    let node = ns;
    for (const part of p.slice(0, -1)) node = (node[part] ??= {}) as NS;
    node[p[p.length - 1]] = [];
  }
  return { source, ns };
}

