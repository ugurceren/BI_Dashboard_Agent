// Tanıtım ortak parçaları: **kalın** / `kod` destekli metin, büyütülebilir ekran görüntüsü.
import { Fragment, useEffect, useState, type ReactNode } from "react";
import { shotUrl } from "./content";

/** "**kalın**" ve "`kod`" işaretlerini çizer (başka biçim yok; HTML yorumlanmaz). */
export function Rich({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return (
    <>
      {parts.map((p, i) => p.startsWith("**") && p.endsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong>
        : p.startsWith("`") && p.endsWith("`") ? <code key={i}>{p.slice(1, -1)}</code>
        : <Fragment key={i}>{p}</Fragment>)}
    </>
  );
}

/** Ekran görüntüsü: tıklanınca tam ekran büyür (Esc / tıklama ile kapanır). */
export function Shot({ src, caption, className }: { src: string; caption?: string; className?: string }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } };
    window.addEventListener("keydown", key, true);
    return () => window.removeEventListener("keydown", key, true);
  }, [open]);
  return (
    <figure className={`tr-shot ${className ?? ""}`}>
      <button type="button" className="tr-shot-btn" onClick={() => setOpen(true)} aria-label={`${caption ?? "Ekran görüntüsü"} — büyüt`}>
        <img src={shotUrl(src)} alt={caption ?? ""} loading="lazy" width={1440} height={900} />
      </button>
      {caption ? <figcaption>{caption}</figcaption> : null}
      {open ? (
        <div className="tr-lightbox" role="dialog" aria-label={caption ?? "Ekran görüntüsü"} onClick={() => setOpen(false)}>
          <img src={shotUrl(src)} alt={caption ?? ""} />
        </div>
      ) : null}
    </figure>
  );
}

export const Kicker = ({ children }: { children: ReactNode }) => <div className="tr-kicker">{children}</div>;
