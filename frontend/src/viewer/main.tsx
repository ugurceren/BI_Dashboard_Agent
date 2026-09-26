// Bağımsız HTML görüntüleyici: <script id="report-data"> içindeki {spec, data} ile yalnızca dashboard'u çizer.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { DashboardRenderer } from "../dashboard/DashboardRenderer";
import type { DashboardData, ReportSpec } from "../types";
import "./viewer.css";

interface Payload {
  spec: ReportSpec;
  data: DashboardData;
}

async function loadPayload(): Promise<Payload> {
  const raw = document.getElementById("report-data")?.textContent?.trim() ?? "";
  // Yer tutucu backend tarafından değiştirildiyse JSON ile başlar.
  if (raw.startsWith("{")) return JSON.parse(raw) as Payload;
  if (import.meta.env.DEV) {
    const [spec, data] = await Promise.all([import("../mocks/demo_spec.json"), import("../mocks/demo_data.json")]);
    return { spec: spec.default as unknown as ReportSpec, data: data.default as unknown as DashboardData };
  }
  throw new Error("Rapor verisi bulunamadı.");
}

const root = createRoot(document.getElementById("root")!);

function render(p: Payload) {
  if (p.spec?.title) document.title = p.spec.title;
  if (p.spec?.theme?.background) document.body.style.background = p.spec.theme.background;
  root.render(
    <StrictMode>
      <DashboardRenderer spec={p.spec} data={p.data ?? { datasets: {} }} />
    </StrictMode>,
  );
}

if (import.meta.env.DEV) {
  // geliştirme kolaylığı: konsoldan farklı spec/veri denemek için
  (window as unknown as { __renderReport: (p: Payload) => void }).__renderReport = render;
}

loadPayload()
  .then((p) => {
    render(p);
  })
  .catch((e: unknown) => {
    root.render(<div className="viewer-error">Rapor yüklenemedi: {e instanceof Error ? e.message : String(e)}</div>);
  });
