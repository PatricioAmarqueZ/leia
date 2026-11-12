"""Phone push via ntfy.sh (free). Set NTFY_TOPIC to a private, hard-to-guess topic name."""
from __future__ import annotations

import os

import requests


def push(title, body, click=None):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("[notify] NTFY_TOPIC not set; skipping push. Would have sent:\n" + title + "\n" + body)
        return False
    headers = {"Title": title.encode("ascii", "ignore").decode(), "Tags": "chart_with_upwards_trend"}
    if click:
        headers["Click"] = click
    r = requests.post(f"{os.environ.get('NTFY_SERVER', 'https://ntfy.sh')}/{topic}",
                      data=body.encode("utf-8"), headers=headers, timeout=20)
    r.raise_for_status()
    return True
