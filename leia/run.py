"""Leia's daily scan. Usage: python -m leia.run [--out docs/data] [--no-push]"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import notify, persona, scorecard, signals, sources

N_CATALYST, N_CORE = 12, 8


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, float):
        return round(o, 4) if math.isfinite(o) else None
    return o


def dedupe(picks):
    seen, out = set(), []
    for p in picks:
        key = (p["name"] or p["symbol"]).split(" Class")[0].strip().lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/data")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    (out / "history").mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(signals.ET)

    print("[leia] scanning the market...")
    uni = sources.universe()
    snaps = {s: signals.snapshot(q) for s, q in uni.items()}
    snaps = {s: v for s, v in snaps.items() if v["price"]}
    states = [v["state"] for v in snaps.values()]
    state = max(set(states), key=states.count) if states else "CLOSED"
    earn = sources.earnings_calendar(days=3)
    print(f"[leia] {len(snaps)} stocks, market state {state}, {len(earn)} upcoming earnings")

    cat_syms = sorted(snaps, key=lambda s: signals.prelim_catalyst(snaps[s]), reverse=True)[:45]
    earn_syms = sorted((s for s in earn if s in snaps and (snaps[s]["market_cap"] or 0) >= 1e9),
                       key=lambda s: snaps[s]["market_cap"], reverse=True)[:15]
    core_ranked = [(signals.prelim_core(v), s) for s, v in snaps.items()]
    core_syms = [s for _, s in sorted((x for x in core_ranked if x[0] is not None), reverse=True)[:45]]
    cat_pool = list(dict.fromkeys(cat_syms + earn_syms))
    pool = set(cat_pool) | set(core_syms)

    hist = sources.history(pool | {"SPY", "QQQ", "^VIX"})
    info = sources.infos(pool)
    feats = {s: signals.price_features(hist.get(s)) for s in pool}
    with ThreadPoolExecutor(max_workers=4) as ex:
        beats = dict(zip(earn_syms, ex.map(sources.beat_rate, earn_syms)))

    catalysts = [signals.score_catalyst(snaps[s], feats[s], info.get(s, {}), earn.get(s), beats.get(s)) for s in cat_pool]
    catalysts = dedupe(sorted(catalysts, key=lambda p: p["score"], reverse=True))[:N_CATALYST]
    core = [signals.score_core(snaps[s], feats[s], info.get(s, {})) for s in core_syms]
    core = dedupe(sorted(core, key=lambda p: p["score"], reverse=True))[:N_CORE]
    with ThreadPoolExecutor(max_workers=4) as ex:
        for p, n in zip(catalysts + core, ex.map(sources.news, [p["symbol"] for p in catalysts + core])):
            p["news"] = n

    def idx(sym):
        c = hist.get(sym)
        if c is None or len(c) < 2:
            return None
        return {"price": round(float(c.iloc[-1]), 2), "change_pct": round((float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100, 2)}

    date = now.strftime("%Y-%m-%d")
    n_earn = sum(1 for p in catalysts if p["earnings"])
    digest = clean({
        "assistant": "Leia", "generated_at": now.isoformat(), "date": date, "market_state": state,
        "universe_size": len(snaps),
        "greeting": persona.greeting(now, state),
        "summary": persona.summary(catalysts, core, n_earn, len(snaps)),
        "market": {"S&P 500": idx("SPY"), "Nasdaq 100": idx("QQQ"), "VIX": idx("^VIX")},
        "catalysts": catalysts, "core": core,
        "earnings": sorted(({"symbol": s, **e} for s, e in earn.items() if s in snaps and (e.get("market_cap") or 0) >= 2e9),
                           key=lambda e: e["date"])[:25],
        "disclaimer": persona.DISCLAIMER,
    })
    (out / "latest.json").write_text(json.dumps(digest, indent=1))
    (out / "history" / f"{date}.json").write_text(json.dumps(digest))

    log = scorecard.log_picks(out / "picks_log.json", date, {"catalysts": catalysts, "core": core})
    (out / "scorecard.json").write_text(json.dumps(clean(scorecard.grade(log)), indent=1))

    print("\n" + digest["greeting"] + " " + digest["summary"])
    for p in catalysts:
        print(f"  {p['symbol']:<6} {p['score']:>3}  {p['move_pct']:+6.1f}%  {p['confidence']:<11} {'; '.join(p['reasons'][:2])}")
    print("  -- steady --")
    for p in core:
        print(f"  {p['symbol']:<6} {p['score']:>3}  {'; '.join(p['reasons'][:2])}")
    if not args.no_push:
        notify.push(f"Leia: {len(catalysts)} setups for {date}", persona.push_text(catalysts, core),
                    click=os.environ.get("LEIA_DASHBOARD_URL"))


if __name__ == "__main__":
    main()
