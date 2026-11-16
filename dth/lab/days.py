# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""One Day object per CME trade date, built once from the 1m store and cached.

Times are minutes after midnight New York. A trade date D holds:
  rth  09:30-16:00 on D (390 one-minute bars)
  on   the overnight session: 18:00 on the previous evening -> 09:30 on D
plus daily context that is known BEFORE the RTH open (previous RTH bars, ATR, ...).
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from .. import calendar, data, runcache

RTH_OPEN, RTH_CLOSE = 9 * 60 + 30, 16 * 60
POINT_VALUE = {"ES": 50.0, "NQ": 20.0}
COST_PTS = {"ES": 0.6, "NQ": 0.75}        # round trip: 1 tick slippage each side + ~$5 commission


@dataclass
class Bars:
    t: np.ndarray        # minutes after midnight NY (overnight bars before midnight are negative)
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray

    def __len__(self):
        return len(self.t)

    def window(self, start: int, end: int) -> "Bars":
        m = (self.t >= start) & (self.t < end)
        return Bars(self.t[m], self.o[m], self.h[m], self.l[m], self.c[m], self.v[m])

    def idx(self, minute: int) -> int:
        """Index of the first bar at or after `minute` (len if none)."""
        return int(np.searchsorted(self.t, minute))


@dataclass
class Day:
    sym: str
    d: date
    rth: Bars
    on: Bars
    source: str                     # "real" (CME futures) or "cfd"
    no_trade: str | None            # calendar reason, None = tradable
    ctx: dict = field(default_factory=dict)   # prev-day OHLC, ATR, gap ... (all known before 09:30)


def _bars(df: pd.DataFrame, base: pd.Timestamp) -> Bars:
    mins = ((df.index - base).total_seconds() // 60).astype(int).to_numpy()
    return Bars(mins, *(df[c].to_numpy(float) for c in ("open", "high", "low", "close", "volume")))


def _despike(b: Bars) -> Bars:
    """Fix isolated bad ticks inside RTH (e.g. Yahoo's stale open after a gap in its feed): a wick more than
    15x the recent typical bar range beyond the previous close, the bar's close and the next open is cut back."""
    if len(b) < 3:
        return b
    rng = pd.Series(b.h - b.l).rolling(30, min_periods=5).median().shift(1).bfill().clip(lower=0.25).to_numpy()
    pc = np.r_[b.c[0], b.c[:-1]]
    nx = np.r_[b.o[1:], b.c[-1]]
    hi_ref, lo_ref = np.maximum.reduce([pc, b.c, nx]), np.minimum.reduce([pc, b.c, nx])
    bad_hi, bad_lo = b.h - hi_ref > 15 * rng, lo_ref - b.l > 15 * rng
    if not (bad_hi.any() or bad_lo.any()):
        return b
    o, h, l = b.o.copy(), b.h.copy(), b.l.copy()
    o = np.where((bad_hi & (o > hi_ref)) | (bad_lo & (o < lo_ref)), pc, o)
    h = np.where(bad_hi, np.maximum(o, b.c), h)
    l = np.where(bad_lo, np.minimum(o, b.c), l)
    return Bars(b.t, o, h, l, b.c, b.v)


def _sources(sym: str) -> set[str]:
    f = data.RAW_DIR / "sources.json"
    prov = json.loads(f.read_text()) if f.exists() else {}
    # every CFD-converted source (Dukascopy, HistData, ...) is "cfd"; real futures sources are not
    return {d for k, v in prov.get(sym, {}).items() if k.endswith("_cfd_adjusted") for d in v}


def build(sym: str) -> list[Day]:
    df = data.load(sym, "1m")
    cfd = _sources(sym)
    df["td"] = data.trade_date(df.index)
    days = build_from(df, sym)
    for day in days:
        day.source = "cfd" if str(day.d) in cfd else "real"
        day.no_trade = calendar.no_trade_reason(day.d)
    return days[1:]


def build_live(sym: str, d: date) -> Day | None:
    """Day `d` from whatever bars exist now (the session may still be running), with context from earlier days."""
    df = data.load(sym, "1m")
    df = df[df.index >= pd.Timestamp(d).tz_localize(data.NY) - pd.Timedelta(days=60)]
    df["td"] = data.trade_date(df.index)
    hist = [x for x in build_from(df[df["td"] < d], sym)]
    g = df[df["td"] == d]
    if not hist or g.empty:
        return None
    base = pd.Timestamp(d).tz_localize(data.NY)
    rth = _despike(_bars(g[(g.index >= base + pd.Timedelta(minutes=RTH_OPEN)) & (g.index < base + pd.Timedelta(minutes=RTH_CLOSE))], base))
    on = _bars(g[g.index < base + pd.Timedelta(minutes=RTH_OPEN)], base)
    day = Day(sym, d, rth, on, "real", calendar.no_trade_reason(d))
    _context(hist + [day], len(hist))
    return day


def build_from(df: pd.DataFrame, sym: str) -> list[Day]:
    days = []
    for d, g in df.groupby("td"):
        if d.weekday() >= 5:
            continue
        base = pd.Timestamp(d).tz_localize(data.NY)
        rth = _despike(_bars(g[(g.index >= base + pd.Timedelta(minutes=RTH_OPEN)) & (g.index < base + pd.Timedelta(minutes=RTH_CLOSE))], base))
        if len(rth) < 380:
            continue
        days.append(Day(sym, d, rth, _bars(g[g.index < base + pd.Timedelta(minutes=RTH_OPEN)], base), "real", None))
    for i in range(len(days)):
        _context(days, i)
    return days


NOISE_LOOKBACK = 14


def _moves(day: Day) -> np.ndarray:
    """|close / open - 1| at each of the 390 RTH minutes (gaps forward-filled) - for the noise bands."""
    r = day.rth
    s = pd.Series(r.c, index=r.t - RTH_OPEN).reindex(range(390)).ffill().bfill()
    return np.abs(s.to_numpy() / r.o[0] - 1)


def _context(days: list[Day], i: int) -> None:
    day = days[i]
    r = day.rth
    if len(r):
        day.ctx.update(open=r.o[0], high=r.h.max(), low=r.l.min(), close=r.c[-1])
        if len(r) >= 380:
            day.ctx["moves"] = _moves(day)
    if i >= NOISE_LOOKBACK and all("moves" in x.ctx for x in days[i - NOISE_LOOKBACK:i]):
        day.ctx["sigma"] = np.mean([x.ctx["moves"] for x in days[i - NOISE_LOOKBACK:i]], axis=0)
    if i == 0:
        return
    p = days[i - 1]
    rng = [x.ctx["high"] - x.ctx["low"] for x in days[max(0, i - 14):i]]
    on = day.on
    day.ctx.update(
        pdo=p.ctx["open"], pdh=p.ctx["high"], pdl=p.ctx["low"], pdc=p.ctx["close"],
        atr=float(np.mean(rng)),
        gap=(r.o[0] - p.ctx["close"]) if len(r) else np.nan,
        onh=on.h.max() if len(on) else np.nan, onl=on.l.min() if len(on) else np.nan,
        nr7=(p.ctx["high"] - p.ctx["low"]) <= min(rng[-7:]) if len(rng) >= 7 else False,
        inside=p.ctx["high"] <= days[i - 2].ctx["high"] and p.ctx["low"] >= days[i - 2].ctx["low"] if i >= 2 else False,
        prev_ret=(p.ctx["close"] - days[i - 2].ctx["close"]) / days[i - 2].ctx["close"] if i >= 2 else 0.0,
        ret5=(p.ctx["close"] - days[i - 6].ctx["close"]) / days[i - 6].ctx["close"] if i >= 6 else 0.0,
        ret20=(p.ctx["close"] - days[i - 21].ctx["close"]) / days[i - 21].ctx["close"] if i >= 21 else 0.0,
    )


def load_days(sym: str) -> list[Day]:
    """Days for one symbol, cached by data version."""
    f = runcache.CACHE / f"lab_days_{sym}_{runcache.data_version()}_{runcache.code_version(('lab/days.py',))}.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    days = build(sym)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(pickle.dumps(days))
    return days


def paired(es: list[Day], nq: list[Day]) -> list[tuple[Day, Day]]:
    by = {d.d: d for d in nq}
    return [(e, by[e.d]) for e in es if e.d in by]
