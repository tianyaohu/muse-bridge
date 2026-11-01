"""Minimal Nori -> Muse driver via useapi.net REST (third-party; uses Frank's Muse account).

  export USEAPI_TOKEN=...   (or put USEAPI_TOKEN=... in nori-to-muse/.env, chmod 600)
  python muse_chat.py "Find 10 new SWE co-op postings in Vancouver posted this week and \\
      report them with the nori-muse-bridge report_job_finds tool"
  python muse_chat.py --chat <chatId> "follow-up message"

Docs: https://useapi.net/docs/api-muse-v1  (POST /chats, POST /chats/{id}/messages, GET /chats/{id})
"""
import json, os, sys, time, urllib.parse, urllib.request
from pathlib import Path
BASE = "https://api.useapi.net/v1/muse"
envf = Path(__file__).with_name(".env")
tok = os.environ.get("USEAPI_TOKEN") or (dict(l.split("=", 1) for l in envf.read_text().split() if "=" in l).get("USEAPI_TOKEN") if envf.exists() else None)
if not tok:
    sys.exit("USEAPI_TOKEN not set (Frank must create a useapi.net token and add a Muse account)")
def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)
args = sys.argv[1:]
chat = None
if args[:1] == ["--chat"]:
    chat, args = args[1], args[2:]
msg = " ".join(args)
if chat:
    call("POST", f"/chats/{urllib.parse.quote(chat, safe='')}/messages", {"message": msg})
else:
    chat = call("POST", "/chats", {"message": msg})["chatId"]
print("chatId:", chat)
for _ in range(60):  # poll up to ~10 min
    time.sleep(10)
    c = call("GET", f"/chats/{urllib.parse.quote(chat, safe='')}")
    st = c.get("status") or c.get("state")
    if st not in ("running", "in_progress", "queued"):
        print(json.dumps(c, indent=2)[:8000]); break
else:
    print("still running; check later with GET /chats/" + chat)
