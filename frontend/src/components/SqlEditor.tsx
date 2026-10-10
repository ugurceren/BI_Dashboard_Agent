// SQL editörü (CodeMirror): yetkili şemadan IntelliSense, Ctrl+Enter / F5 ile çalıştırma (seçili metin varsa yalnız o).
// Sorgu Çalıştır ekranı ve rapor tasarımındaki sorgu modu ortak kullanır.
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { EditorView, basicSetup } from "codemirror";
import { keymap, placeholder } from "@codemirror/view";
import { EditorState, Prec } from "@codemirror/state";
import { sql as sqlLang, MSSQL } from "@codemirror/lang-sql";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags as t } from "@lezer/highlight";
import { buildCompletion } from "../lib/sqlComplete";
import type { QuerySchema } from "../types";

const highlight = HighlightStyle.define([
  { tag: t.keyword, color: "var(--sql-kw)", fontWeight: "600" },
  { tag: [t.string, t.special(t.string)], color: "var(--sql-str)" },
  { tag: [t.number, t.bool, t.null], color: "var(--sql-num)" },
  { tag: [t.lineComment, t.blockComment], color: "var(--sql-comment)", fontStyle: "italic" },
  { tag: [t.typeName, t.standard(t.name)], color: "var(--sql-type)" },
  { tag: [t.operator, t.punctuation], color: "var(--text-2)" },
  { tag: t.special(t.name), color: "var(--sql-type)" },
]);

const editorTheme = EditorView.theme({
  "&": { height: "100%", fontSize: "13.5px", backgroundColor: "var(--surface)", color: "var(--text)" },
  ".cm-scroller": { fontFamily: "var(--mono)", lineHeight: "1.55" },
  ".cm-content": { caretColor: "var(--accent)" },
  ".cm-cursor": { borderLeftColor: "var(--accent)" },
  ".cm-gutters": { backgroundColor: "var(--surface-2)", color: "var(--muted)", border: "none", borderRight: "1px solid var(--border)" },
  ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--accent) 6%, transparent)" },
  ".cm-activeLineGutter": { backgroundColor: "color-mix(in srgb, var(--accent) 10%, transparent)", color: "var(--text)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": { backgroundColor: "color-mix(in srgb, var(--accent) 25%, transparent) !important" },
  ".cm-tooltip": { backgroundColor: "var(--surface)", border: "1px solid var(--border)", borderRadius: "8px", boxShadow: "var(--shadow-md)", color: "var(--text)" },
  ".cm-tooltip-autocomplete > ul > li[aria-selected]": { backgroundColor: "var(--accent-soft)", color: "var(--text)" },
  ".cm-completionDetail": { color: "var(--muted)", fontStyle: "normal", marginLeft: "8px" },
  ".cm-completionInfo": { padding: "6px 10px", maxWidth: "320px", fontSize: "12.5px" },
  ".cm-placeholder": { color: "var(--muted)" },
  ".cm-matchingBracket": { backgroundColor: "color-mix(in srgb, var(--accent) 20%, transparent)", outline: "none" },
});

export interface SqlEditorHandle {
  /** çalıştırılacak metin: seçim varsa seçim, yoksa tüm belge */
  runText(): string;
  text(): string;
  insert(text: string): void;
  replaceAll(text: string): void;
  focus(): void;
}

export const SqlEditor = forwardRef<SqlEditorHandle, {
  schema: QuerySchema | null;
  /** ilk içerik; editör kurulduktan sonra değişirse (başka sorguya geçiş) belge değiştirilir */
  value: string;
  onChange?: (text: string) => void;
  onRun?: () => void;
  placeholderText?: string;
  className?: string;
  testId?: string;
}>(function SqlEditor({ schema, value, onChange, onRun, placeholderText = "SELECT … FROM dbo.Tablo", className, testId }, ref) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const changeRef = useRef(onChange);
  changeRef.current = onChange;
  const runRef = useRef(onRun);
  runRef.current = onRun;
  const valueRef = useRef(value);
  valueRef.current = value;

  // şema gelince (IntelliSense için) kurulur; şema yoksa da düz SQL editörü olarak çalışır
  useEffect(() => {
    if (!host.current) return;
    const { source, ns } = schema ? buildCompletion(schema) : { source: undefined, ns: undefined };
    const lang = sqlLang({ dialect: MSSQL, schema: ns, upperCaseKeywords: true });
    const ev = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: valueRef.current,
        extensions: [
          Prec.highest(keymap.of([
            { key: "Mod-Enter", run: () => { runRef.current?.(); return true; } },
            { key: "F5", run: () => { runRef.current?.(); return true; }, preventDefault: true },
          ])),
          basicSetup,
          lang,
          ...(source ? [MSSQL.language.data.of({ autocomplete: source })] : []),
          syntaxHighlighting(highlight),
          editorTheme,
          EditorView.lineWrapping,
          placeholder(placeholderText),
          EditorView.updateListener.of((u) => { if (u.docChanged) changeRef.current?.(u.state.doc.toString()); }),
        ],
      }),
    });
    view.current = ev;
    return () => { ev.destroy(); view.current = null; };
  }, [schema, placeholderText]);

  // dışarıdan farklı bir değer gelirse (ör. başka sorgu sekmesi) belge değiştirilir
  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
  }, [value]);

  useImperativeHandle(ref, () => ({
    runText: () => {
      const v = view.current;
      if (!v) return valueRef.current.trim();
      const sel = v.state.selection.main;
      return (sel.empty ? v.state.doc.toString() : v.state.sliceDoc(sel.from, sel.to)).trim();
    },
    text: () => view.current?.state.doc.toString() ?? valueRef.current,
    insert: (text) => {
      const v = view.current;
      if (!v) return;
      const sel = v.state.selection.main;
      v.dispatch({ changes: { from: sel.from, to: sel.to, insert: text }, selection: { anchor: sel.from + text.length } });
      v.focus();
    },
    replaceAll: (text) => {
      const v = view.current;
      if (!v) return;
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text }, selection: { anchor: text.length } });
      v.focus();
    },
    focus: () => view.current?.focus(),
  }), []);

  return <div className={className} ref={host} data-testid={testId} />;
});
