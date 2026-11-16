# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Shared trade simulator - every strategy is scored by the same rules.

Conservative fills:
  - stop and target touched in the same minute -> the stop wins;
  - the stop is checked on the entry bar itself, the target only from the next bar;
  - a gap through the stop fills at the bar's open (worse than the stop);
  - costs (COST_PTS: slippage + commission) are taken off every trade.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .days import COST_PTS, POINT_VALUE, Bars, Day

EXIT_BY = 15 * 60 + 55            # flat by 15:55 NY


@dataclass
class Trade:
    sym: str
    d: object
    side: int                     # +1 long, -1 short
    t_in: int
    entry: float
    stop: float
    target: float
    t_out: int = 0
    exit: float = 0.0
    reason: str = ""
    pts: float = 0.0              # net of costs
    usd: float = 0.0              # per 1 contract, net
    atr_units: float = 0.0        # pts / ATR - comparable across ES and NQ
    r: float = np.nan             # pts / initial risk
    source: str = ""
    tag: str = ""
    atr: float = np.nan           # the day's ATR(14) in points (for sizing)


def run(day: Day, side: int, i0: int, entry: float, stop: float, target: float = np.nan,
        exit_by: int = EXIT_BY, bars: Bars | None = None, tag: str = "") -> Trade | None:
    """Enter at bar i0 (price `entry`), manage until stop / target / exit_by."""
    b = bars or day.rth
    end = b.idx(exit_by)
    if i0 >= end or np.isnan(entry):
        return None
    lo, hi = b.l[i0:end], b.h[i0:end]
    if np.isnan(stop):
        s = len(lo)
    else:
        hit = lo <= stop if side > 0 else hi >= stop
        s = int(np.argmax(hit)) if hit.any() else len(lo)
    if np.isnan(target):
        g = len(lo)
    else:
        hit = (hi >= target) if side > 0 else (lo <= target)
        hit[0] = False
        g = int(np.argmax(hit)) if hit.any() else len(lo)
    if s < len(lo) and s <= g:
        k = i0 + s
        px = stop if k == i0 else (min(b.o[k], stop) if side > 0 else max(b.o[k], stop))
        reason = "stop"
    elif g < len(lo):
        k, px, reason = i0 + g, target, "target"
    else:
        k, px, reason = end - 1, b.c[end - 1], "time"
    tr = Trade(day.sym, day.d, side, int(b.t[i0]), entry, stop, target, int(b.t[k]), float(px), reason,
               source=day.source, tag=tag)
    tr.pts = side * (px - entry) - COST_PTS[day.sym]
    tr.usd = tr.pts * POINT_VALUE[day.sym]
    tr.atr_units = tr.pts / day.ctx["atr"]
    tr.atr = day.ctx["atr"]
    if not np.isnan(stop) and abs(entry - stop) > 0:
        tr.r = tr.pts / abs(entry - stop)
    return tr


def stop_entry(day: Day, side: int, level: float, start: int, end: int, bars: Bars | None = None) -> tuple[int, float] | None:
    """First bar in [start, end) minutes that trades through `level` (buy-stop above / sell-stop below).
    Fill = level, or the open if the bar opened beyond it."""
    b = bars or day.rth
    i, j = b.idx(start), b.idx(end)
    if i >= j:
        return None
    hit = b.h[i:j] >= level if side > 0 else b.l[i:j] <= level
    if not hit.any():
        return None
    k = i + int(np.argmax(hit))
    px = max(b.o[k], level) if side > 0 else min(b.o[k], level)
    return k, float(px)


def limit_entry(day: Day, side: int, level: float, start: int, end: int, bars: Bars | None = None) -> tuple[int, float] | None:
    """First bar in [start, end) that touches `level` from the favourable side (buy-limit below / sell-limit above)."""
    b = bars or day.rth
    i, j = b.idx(start), b.idx(end)
    if i >= j:
        return None
    hit = b.l[i:j] <= level if side > 0 else b.h[i:j] >= level
    if not hit.any():
        return None
    k = i + int(np.argmax(hit))
    px = min(b.o[k], level) if side > 0 else max(b.o[k], level)
    return k, float(px)
