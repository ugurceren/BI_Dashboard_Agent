// npm run e2e: masaüstü ve sunucu modu testlerini SIRAYLA çalıştırır; her aşamada yalnız gereken backend açılır.
// Ek argümanlar Playwright'a geçer (ör. npm run e2e -- --grep "Sorgu").
import { spawnSync } from "node:child_process";

let failed = false;
for (const [servers, project] of [["desktop", "masaustu"], ["server", "sunucu"]]) {
  console.log(`\n=== ${project} (${servers}) ===`);
  const r = spawnSync("npx", ["playwright", "test", `--project=${project}`, ...process.argv.slice(2)],
    { stdio: "inherit", shell: true, env: { ...process.env, E2E_SERVERS: servers } });
  failed ||= r.status !== 0;
}
process.exit(failed ? 1 : 0);
