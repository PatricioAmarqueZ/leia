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


def push_slot(now):
    """Which phone alert this run is allowed to send. Late or overnight runs update the app silently."""
    if now.weekday() >= 5:
        return None
    m = now.hour * 60 + now.minute
    if 4 * 60 <= m < 9 * 60 + 30:
        return "morning"
    if 16 * 60 <= m < 20 * 60:
        return "evening"
    return None


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:  # noqa: BLE001
        return default


def previous_digests(hist_dir, today, n=10):
    """Most recent earlier scans, newest first (one per day)."""
    files = sorted((p for p in Path(hist_dir).glob("*.json") if p.stem < today), reverse=True)[:n]
    return [d for d in (load_json(p, None) for p in files) if d]


def since_last(prev, snaps):
    """How the last list's picks have done since it was published."""
    if not prev:
        return None
    out = {"date": prev.get("date"), "lists": {}}
    for name in ("catalysts", "core"):
        rows = []
        for p in prev.get(name) or []:
            now_px = (snaps.get(p["symbol"]) or {}).get("price")
            if p.get("price") and now_px:
                rows.append({"symbol": p["symbol"], "change_pct": round((now_px / p["price"] - 1) * 100, 2)})
        if rows:
            rows.sort(key=lambda r: r["change_pct"], reverse=True)
            out["lists"][name] = {"avg": round(sum(r["change_pct"] for r in rows) / len(rows), 2),
                                  "winners": sum(r["change_pct"] > 0 for r in rows), "n": len(rows),
                                  "best": rows[0], "worst": rows[-1], "rows": rows}
    return out if out["lists"] else None


def streaks(prevs, symbols):
    """Consecutive previous scan days each symbol appeared on either list."""
    out = {}
    for s in symbols:
        n = 0
        for d in prevs:
            if any(p["symbol"] == s for p in (d.get("catalysts") or []) + (d.get("core") or [])):
                n += 1
            else:
                break
        out[s] = n
    return out


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
    prevs = previous_digests(out / "history", date)
    # Grade the last list made before the most recent trading session, so the recap reflects real trading
    # (a Friday-evening list compared on Saturday or Monday pre-market would always read 0%).
    spy = hist.get("SPY")
    session = str(spy.index[-1].date()) if spy is not None and len(spy) else date
    graded = next((d for d in prevs if d.get("date", "") < session), None)
    recap = since_last(graded, snaps)
    st = streaks(prevs, [p["symbol"] for p in catalysts + core])
    for p in catalysts + core:
        p["streak"] = st.get(p["symbol"], 0)
        p["fidelity_url"] = f"https://digital.fidelity.com/prgw/digital/research/quote/dashboard/summary?symbol={p['symbol']}"
    n_earn = sum(1 for p in catalysts if p["earnings"])
    digest = clean({
        "assistant": "Leia", "generated_at": now.isoformat(), "date": date, "market_state": state,
        "universe_size": len(snaps),
        "greeting": persona.greeting(now, state),
        "summary": persona.summary(catalysts, core, n_earn, len(snaps)),
        "market": {"S&P 500": idx("SPY"), "Nasdaq 100": idx("QQQ"), "VIX": idx("^VIX")},
        "catalysts": catalysts, "core": core, "since_last": recap,
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
    if args.no_push:
        return
    force = os.environ.get("LEIA_FORCE_PUSH") == "1"
    slot = push_slot(now) or ("test" if force else None)
    state_path = out / "push_state.json"
    state = load_json(state_path, {})
    if not slot:
        print(f"[leia] {now:%a %H:%M} ET is outside the alert windows; app updated, no push.")
        return
    if slot != "test" and state.get(slot) == date:
        print(f"[leia] {slot} alert already sent today; app updated, no push.")
        return
    title = {"morning": "Leia · Morning brief", "evening": "Leia · Closing recap"}.get(slot, "Leia · Test alert")
    if notify.push(title, persona.push_text(catalysts, core, digest.get("since_last"), slot),
                   click=os.environ.get("LEIA_DASHBOARD_URL")) and slot != "test":
        state[slot] = date
        state_path.write_text(json.dumps(state))


if __name__ == "__main__":
    main()
