"""Gerçek modelle uçtan uca agent denemesi: çalışan backend'e mesajlar gönderir, adımları yazdırır.

    python scripts/agent_smoke.py "Bayi satışlarını gösteren bir dashboard istiyorum." "Gerisine sen karar ver." "Koyu tema olsun."

Var olan bir oturuma devam etmek için:  SID=<oturum_id> python scripts/agent_smoke.py "..."
"""
import json
import os
import sys
import time
import urllib.request

B = "http://localhost:8000"


def req(path, method="GET", body=None):
    r = urllib.request.Request(B + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=60))


def send(sid, text):
    print(f"\n>>> KULLANICI: {text}")
    r = urllib.request.Request(f"{B}/api/sessions/{sid}/messages", method="POST",
                               data=json.dumps({"content": text}).encode(), headers={"Content-Type": "application/json"})
    t0 = time.time()
    ev = None
    with urllib.request.urlopen(r, timeout=900) as resp:
        for raw in resp:
            line = raw.decode("utf-8").rstrip("\n")
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: ") and ev == "transcript":
                it = json.loads(line[6:])
                if it["role"] == "tool":
                    t = it["tool"]
                    arg = t["arguments"]
                    short = json.dumps(arg, ensure_ascii=False)[:160]
                    print(f"  [{'OK' if t['ok'] else 'HATA'}] {t['name']} ({t['durationMs']} ms) {t['summary'][:140]} | {short}")
                elif it["role"] in ("assistant", "system"):
                    print(f"  <{it['role']}> {it['content'][:900]}")
            elif line.startswith("data: ") and ev == "error":
                print("  !! HATA:", line[6:])
    print(f"  (süre {time.time() - t0:.0f} sn)")


sid = os.environ.get("SID") or req("/api/sessions", "POST", {})["id"]
print("session", sid)
for msg in sys.argv[1:]:
    send(sid, msg)
st = req(f"/api/sessions/{sid}")
print("\nFAZ:", st["phase"], "| dataset:", [d["id"] for d in st["datasets"]], "| spec v", st["spec_version"])
if st["spec"]:
    print("görseller:", [(v["id"], v["type"], v["title"]) for v in st["spec"]["visuals"]])
    print("filtreler:", st["spec"]["filters"])
print("SID", sid)
