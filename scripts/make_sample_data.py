# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Synthetic ES / NQ 1-minute futures bars, so the tests and a quick demo run without any vendor data.

A correlated random walk (ES/NQ correlation ~0.9) with a realistic intraday volatility shape: quiet overnight
session, a volatile open, a U-shaped regular session and a random daily drift (some days trend, some chop).
Bars are written in exactly the store format the tool uses: data/raw/<SYM>_1m/<YYYY-MM>.csv (UTC timestamps).

  python scripts/make_sample_data.py                  # 120 trading days into data/raw/
  python scripts/make_sample_data.py --days 40 --out C:/tmp/raw
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dth import data  # noqa: E402

START = {"ES": 5600.0, "NQ": 19800.0}


def _vol_shape(t: np.ndarray) -> np.ndarray:
    """Relative 1-minute volatility by minute of the day (t = minutes after midnight New York, negative = evening)."""
    rth = (t >= 570) & (t < 960)
    v = np.where(rth, 1.0 + 1.6 * np.exp(-(t - 570) / 25.0) + 0.7 * np.exp((t - 960) / 35.0), 0.35)
    return v


def make(out: Path, start: str = "2025-03-03", days: int = 120, seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    px = dict(START)
    frames = {"ES": [], "NQ": []}
    for d in pd.bdate_range(start, periods=days):
        base = pd.Timestamp(d)
        idx = pd.date_range(base - pd.Timedelta(hours=6), base + pd.Timedelta(hours=16, minutes=59), freq="1min")
        t = ((idx - base).total_seconds() // 60).astype(int).to_numpy()
        n = len(idx)
        vol = _vol_shape(t)
        common, own = rng.standard_normal(n), rng.standard_normal(n)
        drift = rng.normal(0, 0.006) / n                       # this day's trend (or chop when ~0)
        shocks = {"NQ": common, "ES": 0.9 * common + 0.436 * own}
        scale = {"NQ": 0.00022, "ES": 0.00017}
        for sym in ("ES", "NQ"):
            ret = vol * scale[sym] * shocks[sym] + drift * (1.25 if sym == "NQ" else 1.0)
            c = px[sym] * np.exp(np.cumsum(ret))
            o = np.r_[px[sym], c[:-1]]
            wick = np.abs(rng.standard_normal(n)) * vol * scale[sym] * c * 0.6
            h, lo = np.maximum(o, c) + wick, np.minimum(o, c) - wick
            tick = 0.25
            df = pd.DataFrame({"open": np.round(o / tick) * tick, "high": np.round(h / tick) * tick,
                               "low": np.round(lo / tick) * tick, "close": np.round(c / tick) * tick,
                               "volume": (vol * (900 if sym == "ES" else 600) * (1 + rng.random(n))).astype(int)},
                              index=idx.tz_localize(data.NY))
            df["high"] = df[["open", "high", "close"]].max(axis=1)
            df["low"] = df[["open", "low", "close"]].min(axis=1)
            frames[sym].append(df)
            px[sym] = float(c[-1])
    old = data.RAW_DIR
    data.RAW_DIR = Path(out)
    try:
        for sym, parts in frames.items():
            data._write_months(sym, "1m", pd.concat(parts))
    finally:
        data.RAW_DIR = old
    return {sym: sum(len(p) for p in parts) for sym, parts in frames.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--start", default="2025-03-03")
    ap.add_argument("--out", default=str(data.RAW_DIR))
    a = ap.parse_args()
    n = make(Path(a.out), a.start, a.days)
    print(f"wrote {n} synthetic 1-minute bars to {a.out}")


if __name__ == "__main__":
    main()
