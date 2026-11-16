# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Two published intraday strategies in their textbook form - the public example plan.

  orb5_concretum   Zarattini & Aziz (2023), "Can Day Trading Really Be Profitable?": the first 5-minute candle closes
                   up -> long at 09:35, down -> short; stop = the other end of that candle; target 10R; else out at
                   the time exit.
  gao_momentum     Gao, Han, Li & Zhou (JFE 2018), "Market intraday momentum": the sign of the return from the
                   previous close to 10:00 -> the same direction from 15:30 to 16:00 (no stop; sized on a risk unit).

Every strategy is a pure function f(day, other, prev, extra) -> Trade | None that only reads `day`'s bars - the
live engine (dth/live/engine.py) calls it on snapshots that contain closed bars only, and flags any look-ahead.
"""
from __future__ import annotations

import numpy as np

from . import sim
from .days import Bars

NAN = float("nan")


def m(h: int, mm: int = 0) -> int:
    """Minutes after midnight New York."""
    return h * 60 + mm


def vwap(b: Bars) -> np.ndarray:
    tp = (b.h + b.l + b.c) / 3
    v = np.where(b.v > 0, b.v, 1.0)
    return np.cumsum(tp * v) / np.cumsum(v)


def orb5_concretum(day, other, prev, extra, p=None):
    p = p or {"n": 5, "rr": 10}
    r = day.rth
    j = r.idx(m(9, 30) + p["n"])
    if j >= len(r) or j < 1:
        return None
    o, c, h, lo = r.o[0], r.c[j - 1], r.h[:j].max(), r.l[:j].min()
    if c == o:
        return None
    side = 1 if c > o else -1
    px = r.o[j]
    stop = lo if side > 0 else h
    if side * (px - stop) <= 0:
        return None
    return sim.run(day, side, j, px, stop, px + side * p["rr"] * abs(px - stop))


def gao_momentum(day, other, prev, extra, p=None):
    p = p or {"first": m(10), "enter": m(15, 30)}
    r = day.rth
    i10, i = r.idx(p["first"]), r.idx(p["enter"])
    if i10 < 1 or i >= len(r) or "pdc" not in day.ctx:
        return None
    first = r.c[i10 - 1] - day.ctx["pdc"]
    if first == 0:
        return None
    side = 1 if first > 0 else -1
    return sim.run(day, side, i, r.o[i], NAN, NAN, exit_by=m(16))
