// İstemci tarafında görsel küçültme: uzun kenar en fazla 1600px, JPEG/PNG data URL.
const MAX_EDGE = 1600;
const PNG_MAX_CHARS = 2_500_000; // ~1.8 MB; üstündeyse JPEG'e düş

function readAsDataURL(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error ?? new Error("Dosya okunamadı"));
    r.readAsDataURL(file);
  });
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("Görsel çözümlenemedi"));
    img.src = src;
  });
}

export async function downscaleImage(file: File): Promise<string> {
  if (!file.type.startsWith("image/")) throw new Error(`${file.name || "Dosya"} bir görsel değil`);
  const src = await readAsDataURL(file);
  const img = await loadImage(src);
  const w = img.naturalWidth;
  const h = img.naturalHeight;
  const scale = Math.min(1, MAX_EDGE / Math.max(w, h));
  const cw = Math.max(1, Math.round(w * scale));
  const ch = Math.max(1, Math.round(h * scale));
  const canvas = document.createElement("canvas");
  canvas.width = cw;
  canvas.height = ch;
  const ctx = canvas.getContext("2d");
  if (!ctx) return src;
  ctx.imageSmoothingQuality = "high";
  const isPng = file.type === "image/png";
  if (!isPng) {
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, cw, ch);
  }
  ctx.drawImage(img, 0, 0, cw, ch);
  if (isPng) {
    const png = canvas.toDataURL("image/png");
    if (png.length <= PNG_MAX_CHARS) return png;
    // saydamlık kaybolmasın diye beyaz zemin üzerine JPEG
    const c2 = document.createElement("canvas");
    c2.width = cw;
    c2.height = ch;
    const x2 = c2.getContext("2d")!;
    x2.fillStyle = "#ffffff";
    x2.fillRect(0, 0, cw, ch);
    x2.drawImage(canvas, 0, 0);
    return c2.toDataURL("image/jpeg", 0.88);
  }
  return canvas.toDataURL("image/jpeg", 0.88);
}
