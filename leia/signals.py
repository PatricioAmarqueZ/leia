"""Scoring: turns raw data into ranked picks with plain-English reasons and risks.

Every weight is here in the open. Scores rank setups; they are not predictions.
"""
from __future__ import annotations

import datetime as dt
import math
from zoneinfo import ZoneInfo

from .sources import num

ET = ZoneInfo("America/New_York")


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def session_fraction(now=None):
    """How much of the regular session has elapsed (0.1 to 1), to project full-day volume."""
    now = now or dt.datetime.now(ET)
    mins = (now.hour * 60 + now.minute) - (9 * 60 + 30)
    return clamp(mins / 390.0, 0.1, 1.0)


def snapshot(q):
    """Cheap features from the universe quote alone (no extra requests)."""
    price = num(q.get("regularMarketPrice"))
    state = q.get("marketState") or "CLOSED"
    day = num(q.get("regularMarketChangePercent")) or 0.0
    pre, post = num(q.get("preMarketChangePercent")), num(q.get("postMarketChangePercent"))
    if state == "PRE" and pre is not None:
        move, label = pre, "pre-market"
    elif state in ("POST", "POSTPOST", "CLOSED", "PREPRE") and post is not None and abs(post) >= 0.5:
        move, label = day + post, "day + after-hours"
    else:
        move, label = day, "today"
    vol, avg = num(q.get("regularMarketVolume")), num(q.get("averageDailyVolume3Month"))
    relvol = None
    if vol and avg:
        relvol = vol / avg / (session_fraction() if state == "REGULAR" else 1.0)
    hi, d50, d200 = num(q.get("fiftyTwoWeekHigh")), num(q.get("fiftyDayAverage")), num(q.get("twoHundredDayAverage"))
    return {
        "symbol": q["symbol"], "name": q.get("shortName") or q.get("longName") or q["symbol"],
        "price": price, "state": state, "move": move, "move_label": label, "rel_volume": relvol,
        "market_cap": num(q.get("marketCap")), "avg_volume": avg,
        "off_high": (price / hi - 1) * 100 if price and hi else None,
        "vs_50d": (price / d50 - 1) * 100 if price and d50 else None,
        "vs_200d": (price / d200 - 1) * 100 if price and d200 else None,
    }


def prelim_catalyst(s):
    pts = clamp(s["move"], 0, 15) / 15 * 25
    if s["rel_volume"]:
        pts += clamp(math.log2(max(s["rel_volume"], 1e-6)), 0, 3) / 3 * 20
    if s["off_high"] is not None:
        pts += 15 if s["off_high"] > -2 else 10 if s["off_high"] > -5 else 5 if s["off_high"] > -10 else 0
    pts += 5 * ((s["vs_50d"] or 0) > 0) + 5 * ((s["vs_200d"] or 0) > 0)
    return pts


def prelim_core(s):
    if (s["market_cap"] or 0) < 10e9 or (s["vs_200d"] or -1) <= 0:
        return None
    return clamp(s["vs_200d"], 0, 40) / 2 + clamp(s["vs_50d"] or 0, 0, 20) / 2 + (10 if (s["off_high"] or -99) > -5 else 0)


def price_features(closes):
    if closes is None or len(closes) < 6:
        return {}
    c = closes
    rets = c.pct_change().dropna()

    def ret(n):
        return (float(c.iloc[-1]) / float(c.iloc[-1 - n]) - 1) * 100 if len(c) > n else None

    return {"ret_5d": ret(5), "ret_20d": ret(20), "ret_60d": ret(60),
            "daily_swing": float(rets.tail(20).std() * 100) if len(rets) >= 10 else None,
            "spark": [round(float(x), 2) for x in c.tail(30)]}


def projection(price, info):
    lo, mean, hi = (num(info.get(k)) for k in ("targetLowPrice", "targetMeanPrice", "targetHighPrice"))
    n = int(num(info.get("numberOfAnalystOpinions")) or 0)
    if not (price and mean and n >= 3):
        return None
    pct = lambda t: round((t / price - 1) * 100, 1) if t else None  # noqa: E731
    return {"target_low": lo, "target_mean": mean, "target_high": hi, "bear": pct(lo), "base": pct(mean),
            "bull": pct(hi), "analysts": n, "rating": (info.get("recommendationKey") or "").replace("_", " ") or None,
            "rating_mean": num(info.get("recommendationMean"))}


def _cap(v):
    return f"${v / 1e9:.1f}B" if v >= 1e9 else f"${v / 1e6:.0f}M"


def _confidence(score):
    return "High" if score >= 70 else "Medium" if score >= 50 else "Speculative"


def _risk(s, f):
    swing, cap = f.get("daily_swing") or 0, s["market_cap"] or 0
    if swing >= 5 or cap < 1e9 or (s["price"] or 0) < 5:
        return "Very high"
    if swing >= 3 or cap < 5e9:
        return "High"
    return "Moderate" if swing >= 1.8 or cap < 30e9 else "Lower"


def score_catalyst(s, f, info, earn, beats):
    reasons, risks = [], []
    proj = projection(s["price"], info)
    short = (num(info.get("shortPercentOfFloat")) or 0) * 100
    pts = 0.0

    m = s["move"]
    pts += clamp(m, 0, 15) / 15 * 25
    if m >= 2:
        reasons.append(f"Up {m:.1f}% {s['move_label']}")
    if m > 25:
        pts -= 10
        risks.append(f"Already up {m:.0f}%: buying now means chasing, and sharp reversals are common")
    rv = s["rel_volume"]
    if rv:
        pts += clamp(math.log2(max(rv, 1e-6)), 0, 3) / 3 * 20
        if rv >= 1.5:
            reasons.append(f"Trading {rv:.1f}x its normal volume, so real money is behind the move")
    oh = s["off_high"]
    if oh is not None:
        pts += 15 if oh > -2 else 10 if oh > -5 else 5 if oh > -10 else 0
        if oh > -2:
            reasons.append("At or breaking through its 52-week high")
        elif oh > -5:
            reasons.append(f"Within {abs(oh):.1f}% of its 52-week high")
    up50, up200 = (s["vs_50d"] or 0) > 0, (s["vs_200d"] or 0) > 0
    pts += 5 * up50 + 5 * up200
    if up50 and up200:
        reasons.append("Uptrend intact: above both its 50-day and 200-day averages")
    elif not up200:
        risks.append("Still below its 200-day average, so the longer trend is down")
    r5, r20 = f.get("ret_5d"), f.get("ret_20d")
    if r5 is not None and r20 is not None and r5 > 0 and r20 > 0:
        pts += clamp(r20, 0, 30) / 30 * 10
        if r20 >= 8:
            reasons.append(f"Momentum: up {r20:.0f}% over the past month")
    if earn:
        pts += 12
        line = f"Reports earnings {earn['timing']} ({earn['date'][:10]})"
        if beats and beats[1] >= 3:
            line += f"; beat estimates in {beats[0]} of the last {beats[1]} quarters"
        reasons.append(line)
        risks.append("Earnings are a coin-flip event: the stock can gap hard in either direction")
    if proj:
        pts += clamp(proj["base"], 0, 40) / 40 * 8
        if proj["rating"] in ("buy", "strong buy"):
            pts += 4
        if proj["base"] >= 10:
            reasons.append(f"{proj['analysts']} analysts see {proj['base']:.0f}% upside on average (target ${proj['target_mean']:.2f})")
        elif proj["base"] < 0:
            risks.append(f"Already {abs(proj['base']):.0f}% above the average analyst target")
    else:
        risks.append("Little or no analyst coverage, so there is no consensus to lean on")
    if short >= 15:
        pts += 4
        reasons.append(f"{short:.0f}% of the float is sold short: fuel for a squeeze if it keeps rising")
        risks.append("Heavily shorted stocks are violent in both directions")
    cap = s["market_cap"] or 0
    if cap < 2e9:
        risks.append(f"Small cap ({_cap(cap)}): moves fast both ways and can be thinly traded")
    if (s["price"] or 0) < 5:
        risks.append("Low-priced stock: higher pump-and-dump risk")
    if (f.get("daily_swing") or 0) >= 4:
        risks.append(f"Typically swings about {f['daily_swing']:.1f}% a day")
    score = int(round(clamp(pts, 0, 100)))
    return _pick(s, f, info, proj, earn, beats, score, reasons, risks)


def score_core(s, f, info):
    reasons, risks = [], []
    proj = projection(s["price"], info)
    pts = 0.0
    if proj and proj["analysts"] >= 5:
        pts += clamp(proj["base"], 0, 30) / 30 * 25
        if proj["rating_mean"]:
            pts += clamp((3 - proj["rating_mean"]) / 2, 0, 1) * 15
        if proj["base"] >= 5:
            reasons.append(f"{proj['analysts']} analysts rate it '{proj['rating']}' with {proj['base']:.0f}% average upside (target ${proj['target_mean']:.2f})")
        else:
            risks.append("Trading at or above the average analyst target, so the easy upside may be priced in")
    else:
        risks.append("Thin analyst coverage")
    eg, rg = (num(info.get(k)) for k in ("earningsGrowth", "revenueGrowth"))
    if eg is not None:
        pts += clamp(eg * 100, 0, 50) / 50 * 15
        if eg >= 0.1:
            reasons.append(f"Earnings growing {eg * 100:.0f}% year over year")
        elif eg < 0:
            risks.append(f"Earnings shrinking ({eg * 100:.0f}% year over year)")
    if rg is not None:
        pts += clamp(rg * 100, 0, 30) / 30 * 10
        if rg >= 0.08:
            reasons.append(f"Revenue growing {rg * 100:.0f}% year over year")
    if (s["vs_200d"] or 0) > 0:
        pts += 10
    if (s["vs_50d"] or 0) > 0 and (s["vs_200d"] or 0) > (s["vs_50d"] or 0):
        pts += 5
    if (s["vs_50d"] or 0) > 0 and (s["vs_200d"] or 0) > 0:
        reasons.append("In a steady uptrend, above its 50-day and 200-day averages")
    r60 = f.get("ret_60d")
    if r60 is not None:
        pts += clamp(r60, 0, 30) / 30 * 10
        if r60 >= 10:
            reasons.append(f"Up {r60:.0f}% over the past three months")
    pe = num(info.get("forwardPE"))
    if pe and pe > 0:
        pts += 10 if pe < 25 else 5 if pe < 40 else 0
        if pe < 25:
            reasons.append(f"Reasonably priced at {pe:.0f}x next year's earnings")
        elif pe >= 40:
            risks.append(f"Expensive at {pe:.0f}x next year's earnings: needs strong growth to justify it")
    score = int(round(clamp(pts, 0, 100)))
    return _pick(s, f, info, proj, None, None, score, reasons, risks)


def _pick(s, f, info, proj, earn, beats, score, reasons, risks):
    return {
        "symbol": s["symbol"], "name": s["name"], "sector": info.get("sector"), "industry": info.get("industry"),
        "price": s["price"], "move_pct": round(s["move"], 2), "move_label": s["move_label"],
        "rel_volume": round(s["rel_volume"], 2) if s["rel_volume"] else None,
        "market_cap": s["market_cap"], "score": score, "confidence": _confidence(score), "risk": _risk(s, f),
        "reasons": reasons[:5], "risks": risks[:4], "projection": proj,
        "daily_swing": round(f["daily_swing"], 2) if f.get("daily_swing") else None,
        "ret_5d": f.get("ret_5d"), "ret_20d": f.get("ret_20d"), "ret_60d": f.get("ret_60d"),
        "earnings": ({**earn, "beats": beats} if earn else None), "spark": f.get("spark") or [], "news": [],
    }
