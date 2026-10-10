// Sorgu sonucu: hata / uyarı mesajları, satır sayısı ve sonuç tablosu. Sorgu Çalıştır ekranı ve sorgu modu ortak kullanır.
import type { ReactNode } from "react";
import type { QueryRunResult } from "../types";

export function fmtCell(v: unknown, type: string): string {
  if (v === null || v === undefined) return "NULL";
  if (type === "number" && typeof v === "number") return v.toLocaleString("tr-TR", { maximumFractionDigits: 6 });
  return String(v);
}

export function toCsv(res: QueryRunResult): string {
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[";\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [(res.columns ?? []).map(esc).join(";"), ...(res.rows ?? []).map((r) => r.map(esc).join(";"))];
  return "﻿" + lines.join("\r\n");
}

export function QueryResultView({ result, ranSql, actions, empty = "Sorgu sonucu burada görünecek.", testId = "query-result" }: {
  result: QueryRunResult | null;
  ranSql?: string;
  /** sonuç başlığının sağındaki düğmeler (ör. CSV) */
  actions?: ReactNode;
  empty?: string;
  testId?: string;
}) {
  return (
    <>
      {!result ? <div className="qp-empty muted">{empty}</div> : null}
      {result && !result.ok ? (
        <div className="qp-msg is-bad" role="alert">
          <b>Sorgu çalıştırılmadı</b>
          <ul>{(result.errors ?? []).map((e, i) => <li key={i}>{e}</li>)}</ul>
        </div>
      ) : null}
      {result?.warnings?.length ? (
        <div className="qp-msg is-warn"><b>Uyarı</b><ul>{result.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></div>
      ) : null}
      {result?.ok ? (
        <>
          <div className="qp-res-head">
            <span><b>{(result.rows?.length ?? 0).toLocaleString("tr-TR")}</b> satır · {result.columns?.length} kolon · {result.elapsed_ms} ms</span>
            {result.truncated ? <span className="qp-trunc">İlk {result.row_limit?.toLocaleString("tr-TR")} satır gösteriliyor (sınır). Daha azı için WHERE / TOP kullanın.</span> : null}
            <span className="qp-spacer" />
            {actions}
          </div>
          <div className="qp-grid-wrap">
            <table className="qp-grid" data-testid={testId}>
              <thead>
                <tr><th className="qp-rn">#</th>{result.columns?.map((c, i) => <th key={i} className={result.types?.[i] === "number" ? "is-num" : undefined}>{c}</th>)}</tr>
              </thead>
              <tbody>
                {result.rows?.map((r, ri) => (
                  <tr key={ri}>
                    <td className="qp-rn">{ri + 1}</td>
                    {r.map((v, ci) => {
                      const ty = result.types?.[ci] ?? "string";
                      return <td key={ci} className={`${ty === "number" ? "is-num" : ""}${v === null ? " is-null" : ""}`}>{fmtCell(v, ty)}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            {!result.rows?.length ? <div className="qp-empty muted">Sorgu satır döndürmedi.</div> : null}
          </div>
        </>
      ) : null}
      {result && ranSql ? <details className="qp-ran"><summary className="muted small">Çalıştırılan SQL</summary><pre>{ranSql}</pre></details> : null}
    </>
  );
}
