// Rapor yaşam döngüsü statüleri ve renkleri (envanter kartı şeridi, filtre, rozet).
export type ReportStatus = "idea" | "design" | "test" | "live";

export const STATUSES: { id: ReportStatus; label: string; color: string; hint: string }[] = [
  { id: "idea", label: "Fikir", color: "#8b5cf6", hint: "İhtiyaç konuşuluyor, henüz dashboard yok" },
  { id: "design", label: "Tasarımda", color: "#0ea5e9", hint: "Dashboard tasarlanıyor" },
  { id: "test", label: "Test", color: "#f59e0b", hint: "Kullanıcı testinde / doğrulamada" },
  { id: "live", label: "Canlıda", color: "#10b981", hint: "Yayında, kullanılıyor" },
];

export const statusInfo = (s?: string | null) => STATUSES.find((x) => x.id === s) ?? STATUSES[0];
