"""Leia's voice: crisp, British, a touch dry. Plain text so it can be spoken aloud."""
from __future__ import annotations

DISCLAIMER = ("Leia ranks setups from public data; she does not predict prices and this is not financial advice. "
              "Short-term trading is risky and most picks with big upside carry matching downside.")


def greeting(now_et, state):
    part = "Good morning" if now_et.hour < 12 else "Good afternoon" if now_et.hour < 18 else "Good evening"
    when = {"PRE": "The opening bell is still ahead of us.", "REGULAR": "Markets are open.",
            }.get(state, "Markets are closed, so this is your look ahead.")
    return f"{part}. Leia here. {when}"


def summary(catalysts, core, earnings_count, universe_size):
    if not catalysts:
        return f"I've been through {universe_size:,} stocks and nothing is worth your attention just now. Rather a quiet one."
    top = catalysts[0]
    bits = [f"I've been through {universe_size:,} stocks.",
            f"{len(catalysts)} short-term setups made the cut, led by {top['symbol']} with a score of {top['score']}."]
    if earnings_count:
        bits.append(f"{earnings_count} of them report earnings shortly, so do mind the gap.")
    if core:
        bits.append(f"For steadier holds, {core[0]['symbol']} tops the list.")
    return " ".join(bits)


def push_text(catalysts, core):
    lines = []
    for p in catalysts[:5]:
        why = p["reasons"][0] if p["reasons"] else "on the radar"
        lines.append(f"{p['symbol']} ({p['score']}) {p['move_pct']:+.1f}% - {why}")
    if core:
        lines.append("Steady: " + ", ".join(p["symbol"] for p in core[:5]))
    return "\n".join(lines)
