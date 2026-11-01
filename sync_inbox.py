"""Pull records from a remotely hosted bridge into the box inbox (idempotent).

Usage (on the box):  MUSE_BRIDGE_URL=https://muse-bridge.example.com \\
                     .venv/bin/python sync_inbox.py
Token comes from MUSE_BRIDGE_TOKEN or ./.env. Appends new ids to inbox/*.jsonl.
"""
import json, os, urllib.request
from pathlib import Path
here = Path(__file__).resolve().parent
env = {}
if (here / ".env").exists():
    env = dict(l.split("=", 1) for l in (here / ".env").read_text().split() if "=" in l)
tok = os.environ.get("MUSE_BRIDGE_TOKEN") or env.get("MUSE_BRIDGE_TOKEN")
base = (os.environ.get("MUSE_BRIDGE_URL") or env.get("MUSE_BRIDGE_URL", "")).rstrip("/")
assert tok and base, "set MUSE_BRIDGE_URL and MUSE_BRIDGE_TOKEN"
inbox = here / "inbox"; inbox.mkdir(exist_ok=True)
files = {"job_find": inbox / "job_finds.jsonl", "message": inbox / "messages.jsonl"}
have = set()
for f in files.values():
    if f.exists():
        have |= {json.loads(l)["id"] for l in f.read_text().splitlines() if l.strip()}
req = urllib.request.Request(f"{base}/api/inbox", headers={"Authorization": f"Bearer {tok}"})
data = json.load(urllib.request.urlopen(req, timeout=30))
new = [r for r in data["records"] if r["id"] not in have]
for r in new:
    with files.get(r.get("type"), inbox / "other.jsonl").open("a") as fh:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"pulled {len(new)} new record(s) from {base}")
