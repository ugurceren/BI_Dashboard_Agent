// SQL editörü IntelliSense: yetkili tablo/view adları + alias duyarlı kolon önerileri (CodeMirror tamamlama kaynağı).
import type { Completion, CompletionContext, CompletionResult } from "@codemirror/autocomplete";
import type { QueryObject, QuerySchema } from "../types";

// nesne tipi: tablo (olgu / boyut / köprü ayrımı gösterilmez) ya da view
export const KIND_LABEL: Record<string, string> = { fact: "Tablo", dimension: "Tablo", bridge: "Tablo", view: "View", table: "Tablo" };

/** "dbo.FactInternetSales" → ["dbo", "FactInternetSales"] */
export const split = (name: string) => {
  const i = name.indexOf(".");
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
  const NOT_ALIAS = new Set(["on", "join", "inner", "left", "right", "full", "cross", "outer", "where", "group", "order", "having", "union", "with", "as"]);
  const WORD = /^[\wçğıöşüÇĞİÖŞÜ]*$/;
  const source = (ctx: CompletionContext): CompletionResult | null => {
    const word = ctx.matchBefore(/[\wçğıöşüÇĞİÖŞÜ]+/);
    const from = word ? word.from : ctx.pos;
    const doc = ctx.state.doc.toString().toLowerCase().replace(/[[\]]/g, "");
    // sorgudaki tablolar ve alias'ları: "dbo.Tablo [AS] alias"
    const used = new Set<string>();
    const alias = new Map<string, string>();
    for (const m of doc.matchAll(/\b([a-z_]\w*)\.([a-z_]\w*)\b(?:\s+(?:as\s+)?([a-z_]\w*))?/g)) {
      const id = `${m[1]}.${m[2]}`;
      if (!byId.has(id)) continue;
      used.add(id);
      alias.set(m[2], id);
      if (m[3] && !NOT_ALIAS.has(m[3])) alias.set(m[3], id);
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
  const ns: Record<string, Record<string, Completion[]>> = {};
  for (const o of schema.objects) {
    const [sch, tbl] = split(o.name);
    (ns[sch] ??= {})[tbl] = []; // kolonlar alias-duyarlı kendi kaynağımızdan gelir (çift öneri olmasın)
  }
  return { source, ns };
}

