"""Free market data for Leia, via Yahoo Finance (unofficial feed, so every call is defensive)."""
from __future__ import annotations

import datetime as dt
import math
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yfinance as yf
from yfinance import EquityQuery


def num(x):
    try:
        f = float(x)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _retry(fn, tries=3, wait=2.0):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - unofficial API, anything can happen
            last = e
            time.sleep(wait * (i + 1))
    raise last


def universe(min_cap=300e6, min_vol=300_000, min_price=2.0, max_pages=20):
    """Every liquid US-listed stock, as a {symbol: quote} snapshot."""
    q = EquityQuery("and", [
        EquityQuery("eq", ["region", "us"]),
        EquityQuery("gt", ["intradaymarketcap", min_cap]),
        EquityQuery("gt", ["avgdailyvol3m", min_vol]),
        EquityQuery("gt", ["intradayprice", min_price]),
    ])
    out, offset = {}, 0
    for _ in range(max_pages):
        r = _retry(lambda: yf.screen(q, sortField="intradaymarketcap", sortAsc=False, size=250, offset=offset))
        quotes = r.get("quotes") or []
        for x in quotes:
            if x.get("quoteType") == "EQUITY" and x.get("symbol"):
                out[x["symbol"]] = x
        offset += 250
        if not quotes or offset >= (r.get("total") or 0):
            break
    return out


def history(symbols, period="6mo"):
    """{symbol: Series of daily closes}."""
    symbols = sorted(set(symbols))
    if not symbols:
        return {}
    try:
        df = _retry(lambda: yf.download(symbols, period=period, interval="1d", group_by="ticker",
                                        auto_adjust=True, threads=True, progress=False))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for s in symbols:
        try:
            closes = (df[s]["Close"] if isinstance(df.columns, pd.MultiIndex) else df["Close"]).dropna()
            if len(closes) >= 5:
                out[s] = closes
        except Exception:  # noqa: BLE001
            continue
    # The batch download occasionally drops a symbol (cache lock); fetch stragglers one at a time.
    for s in [x for x in symbols if x not in out][:15]:
        try:
            closes = _retry(lambda: yf.Ticker(s).history(period=period, interval="1d", auto_adjust=True))["Close"].dropna()
            if len(closes) >= 5:
                out[s] = closes
        except Exception:  # noqa: BLE001
            continue
    return out


def _info(sym):
    try:
        return sym, yf.Ticker(sym).info or {}
    except Exception:  # noqa: BLE001
        return sym, {}


def infos(symbols, workers=4):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(ex.map(_info, sorted(set(symbols))))


def earnings_calendar(days=3):
    """Upcoming earnings in the next few days: {symbol: {date, timing, eps_est}}."""
    today = dt.date.today()
    try:
        df = yf.Calendars(start=today, end=today + dt.timedelta(days=days)).get_earnings_calendar(
            limit=100, filter_most_active=False)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    now = pd.Timestamp.now(tz="UTC")
    for sym, row in df.iterrows():
        when = row.get("Event Start Date")
        if pd.isna(when) or when < now - pd.Timedelta(hours=1) or not pd.isna(row.get("Reported EPS")):
            continue
        out[str(sym)] = {
            "date": when.isoformat(),
            "timing": {"AMC": "after the close", "BMO": "before the open"}.get(row.get("Timing"), "during the day"),
            "eps_est": num(row.get("EPS Estimate")),
            "company": row.get("Company"),
            "market_cap": num(row.get("Marketcap")),
        }
    return out


def beat_rate(sym):
    """(beats, quarters) over recent reported quarters, or None."""
    try:
        df = yf.Ticker(sym).get_earnings_dates(limit=10)
        s = df["Surprise(%)"].dropna()
        return (int((s > 0).sum()), int(len(s))) if len(s) else None
    except Exception:  # noqa: BLE001
        return None


def news(sym, limit=2):
    try:
        items = yf.Ticker(sym).news or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for it in items[:limit]:
        c = it.get("content") or it
        url = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or c.get("link"))
        title = c.get("title")
        if title and url:
            out.append({"title": title, "url": url,
                        "publisher": (c.get("provider") or {}).get("displayName") or c.get("publisher")})
    return out
