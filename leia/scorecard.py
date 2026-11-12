"""Keeps Leia honest: logs every pick and grades it against what actually happened."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import sources

HORIZONS = {"same_day": 0, "1d": 1, "5d": 5, "20d": 20}  # trading-day closes after the pick date


def _load(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:  # noqa: BLE001
        return []


def log_picks(path, date, lists):
    log = _load(path)
    seen = {(e["date"], e["list"], e["symbol"]) for e in log}
    for name, picks in lists.items():
        for p in picks:
            if p.get("price") and (date, name, p["symbol"]) not in seen:
                log.append({"date": date, "list": name, "symbol": p["symbol"], "entry": p["price"], "score": p["score"]})
    Path(path).write_text(json.dumps(log, indent=1))
    return log


def grade(log):
    """Fill in realised returns for each logged pick and summarise by list and horizon."""
    if not log:
        return {"picks": 0, "lists": {}, "recent": []}
    hist = sources.history({e["symbol"] for e in log} | {"SPY"}, period="6mo")
    spy = hist.get("SPY")
    rows = []
    for e in log:
        c = hist.get(e["symbol"])
        if c is None or spy is None:
            continue
        day = pd.Timestamp(e["date"])
        idx = c.index.tz_localize(None) if c.index.tz is not None else c.index
        on_or_after = [i for i, d in enumerate(idx) if d.normalize() >= day]
        sidx = spy.index.tz_localize(None) if spy.index.tz is not None else spy.index
        s_after = [i for i, d in enumerate(sidx) if d.normalize() >= day]
        if not on_or_after or not s_after or s_after[0] == 0:
            continue
        row = dict(e)
        for h, n in HORIZONS.items():
            i, j = on_or_after[0] + n, s_after[0] + n
            # Skip today's still-moving bar: only grade against completed sessions.
            if i < len(c) - 1 and j < len(spy) - 1 or (i < len(c) and idx[i].normalize() < pd.Timestamp.now().normalize()):
                r = (float(c.iloc[i]) / e["entry"] - 1) * 100
                m = (float(spy.iloc[j]) / float(spy.iloc[s_after[0] - 1]) - 1) * 100
                row[h] = round(r, 2)
                row[h + "_vs_spy"] = round(r - m, 2)
        rows.append(row)
    lists = {}
    for name in sorted({r["list"] for r in rows}):
        lists[name] = {}
        for h in HORIZONS:
            vals = [r[h] for r in rows if r["list"] == name and h in r]
            ex = [r[h + "_vs_spy"] for r in rows if r["list"] == name and h in r]
            if vals:
                lists[name][h] = {"n": len(vals), "avg": round(sum(vals) / len(vals), 2),
                                  "win_rate": round(100 * sum(v > 0 for v in vals) / len(vals), 1),
                                  "avg_vs_spy": round(sum(ex) / len(ex), 2)}
    return {"picks": len(log), "lists": lists, "recent": sorted(rows, key=lambda r: r["date"], reverse=True)[:60]}
