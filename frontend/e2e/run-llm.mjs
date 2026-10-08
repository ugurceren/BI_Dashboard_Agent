// Gerçek LLM duman testi: E2E_LLM=1 ile yalnız "gercek-llm" projesini çalıştırır (cross-env gerektirmez).
import { spawnSync } from "node:child_process";
const r = spawnSync("npx", ["playwright", "test", "--project=gercek-llm", ...process.argv.slice(2)],
  { stdio: "inherit", shell: true, env: { ...process.env, E2E_LLM: "1", E2E_SERVERS: "none" } });
process.exit(r.status ?? 1);
