// Rapor paylaşımı: kullanıcı (DOMAIN\kullanici) ya da AD grubu; isteğe bağlı dışa aktarma (HTML) izni.
import { useState } from "react";
import type { Grant } from "../types";

export function ShareEditor({ grants, onChange, disabled }: { grants: Grant[]; onChange: (g: Grant[]) => void; disabled?: boolean }) {
  const [type, setType] = useState<Grant["principal_type"]>("group");
  const [name, setName] = useState("");
  const add = () => {
    const p = name.trim().replace(/\s+/g, " ");
    if (!p) return;
    const key = `${type}:${p.toLocaleLowerCase("tr")}`;
    if (!grants.some((g) => `${g.principal_type}:${g.principal.toLocaleLowerCase("tr")}` === key)) {
      onChange([...grants, { principal_type: type, principal: p, can_export: false }]);
    }
    setName("");
  };
  return (
    <div className="share">
      {grants.length ? (
        <ul className="share-list">
          {grants.map((g, i) => (
            <li key={`${g.principal_type}:${g.principal}`} className="share-row">
              <span className={`share-kind is-${g.principal_type}`}>{g.principal_type === "group" ? "AD grubu" : "Kullanıcı"}</span>
              <span className="share-name" title={g.principal}>{g.principal}</span>
              <label className="share-export" title="HTML olarak indirebilir (veri dosyaya gömülür)">
                <input type="checkbox" checked={g.can_export} disabled={disabled}
                  onChange={(e) => onChange(grants.map((x, j) => (j === i ? { ...x, can_export: e.target.checked } : x)))} />
                Dışa aktarabilir
              </label>
              <button type="button" className="btn btn-ghost btn-sm" disabled={disabled} aria-label={`${g.principal} iznini kaldır`}
                onClick={() => onChange(grants.filter((_, j) => j !== i))}>Kaldır</button>
            </li>
          ))}
        </ul>
      ) : <p className="muted small share-empty">Henüz kimseyle paylaşılmadı: rapor yalnız size ve yöneticilere görünür.</p>}
      <form className="share-add" onSubmit={(e) => { e.preventDefault(); add(); }}>
        <select className="vt-input" value={type} disabled={disabled} onChange={(e) => setType(e.target.value as Grant["principal_type"])} aria-label="Paylaşım türü">
          <option value="group">AD grubu</option>
          <option value="user">Kullanıcı</option>
        </select>
        <input className="vt-input" value={name} disabled={disabled} onChange={(e) => setName(e.target.value)} maxLength={256}
          placeholder={type === "group" ? "Grup adı, ör. BI_Satis_Ekibi" : "DOMAIN\\kullanici"} aria-label="Kullanıcı ya da grup adı" />
        <button type="submit" className="btn btn-secondary btn-sm" disabled={disabled || !name.trim()}>Ekle</button>
      </form>
    </div>
  );
}
