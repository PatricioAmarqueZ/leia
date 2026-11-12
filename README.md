# Leia

A daily market scanner with a British accent. Leia screens every liquid US stock, ranks short-term setups and
steadier holds, explains each pick, and grades her own past calls. Not financial advice.

## Run locally
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m leia.run --no-push
    python3 -m http.server 8765 --directory docs   # then open http://localhost:8765

## Run in the cloud (free)
1. Push this folder to a GitHub repository.
2. Settings > Pages: deploy from branch `main`, folder `/docs`.
3. Install the ntfy app on your phone and subscribe to a private topic name.
4. Settings > Secrets and variables > Actions: secret `NTFY_TOPIC` (your topic), variable `LEIA_DASHBOARD_URL` (your Pages URL).
The workflow in `.github/workflows/leia.yml` then scans before the open and after the close every weekday.

## API (for a future Jarvis-style assistant)
The dashboard's data is plain JSON at stable URLs under your Pages site:
- `data/latest.json`: today's greeting, summary, picks, reasons, risks, analyst targets
- `data/scorecard.json`: how past picks performed
- `data/history/YYYY-MM-DD.json`: every past scan

## Layout
- `leia/sources.py` data (Yahoo Finance, free and unofficial)
- `leia/signals.py` scoring weights, reasons and risk flags
- `leia/persona.py` Leia's voice
- `leia/scorecard.py` pick log and grading
- `leia/notify.py` phone push
- `docs/` dashboard and JSON output
