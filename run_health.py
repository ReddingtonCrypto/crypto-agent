"""Records the outcome of each scan cycle so the dashboard can show whether
the bot is actually running successfully (not just silently stale).

Writes data/run_health.json: the last run plus a short rolling history.
"""
import json
import os
from datetime import datetime, timezone

HEALTH_FILE = "data/run_health.json"
KEEP = 20


def record(status, **info):
    """status = 'ok' or 'error'; info = extra fields (open/closed counts, error)."""
    os.makedirs("data", exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = {"time": now, "status": status}
    entry.update(info)

    data = {"last": entry, "history": []}
    if os.path.exists(HEALTH_FILE):
        try:
            with open(HEALTH_FILE) as f:
                data = json.load(f)
        except Exception:
            data = {"last": entry, "history": []}

    data["last"] = entry
    hist = data.get("history", [])
    hist.append(entry)
    data["history"] = hist[-KEEP:]

    with open(HEALTH_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load():
    if os.path.exists(HEALTH_FILE):
        try:
            with open(HEALTH_FILE) as f:
                return json.load(f)
        except Exception:
            return None
    return None
