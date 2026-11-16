# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Market data: download ES/NQ futures bars from Yahoo and keep a growing local store.

Yahoo only serves 1m bars for the last ~30 days (max 8 days per request), so
`collect()` must run regularly; every run merges new bars into data/raw/<SYM>_<interval>/<YYYY-MM>.csv
and never deletes old ones, so history accumulates over time.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = Path(os.environ.get("DTH_DATA_DIR", ROOT / "data" / "raw"))
NY = "America/New_York"

# Our symbol -> Yahoo ticker (CME front-month continuous, same market as TradingView ES1!/NQ1!)
TICKERS = {"ES": "ES=F", "NQ": "NQ=F"}
COLUMNS = ["open", "high", "low", "close", "volume"]


def _store_path(sym: str, interval: str) -> Path:
    """Legacy single-file store (read only for migration)."""
    return RAW_DIR / f"{sym}_{interval}.csv"


def _store_dir(sym: str, interval: str) -> Path:
    """Monthly store: data/raw/<SYM>_<interval>/<YYYY-MM>.csv (months by UTC timestamp)."""
    return RAW_DIR / f"{sym}_{interval}"


def _read(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(NY)
    return df[COLUMNS].astype(float)


def load(sym: str, interval: str = "1m") -> pd.DataFrame:
    """Load stored bars for a symbol (all monthly files), indexed by New York time."""
    d = _store_dir(sym, interval)
    files = sorted(d.glob("*.csv")) if d.exists() else []
    if not files:
        legacy = _store_path(sym, interval)
        return _read(legacy) if legacy.exists() else pd.DataFrame(columns=COLUMNS)
    df = pd.concat([_read(f) for f in files])
    return df[~df.index.duplicated(keep="last")].sort_index()


def _write_months(sym: str, interval: str, df: pd.DataFrame) -> None:
    d = _store_dir(sym, interval)
    d.mkdir(parents=True, exist_ok=True)
    utc = df.copy()
    utc.index = utc.index.tz_convert("UTC")
    for month, part in utc.groupby(utc.index.strftime("%Y-%m")):
        part.to_csv(d / f"{month}.csv")


def migrate(sym: str, interval: str) -> bool:
    """One-time: split the legacy single CSV into monthly files, verify, then delete the legacy file."""
    legacy = _store_path(sym, interval)
    if not legacy.exists():
        return False
    old = _read(legacy)
    _write_months(sym, interval, old)
    new = load(sym, interval)
    if len(new) != len(old) or not new.index.equals(old.index) or not (new.values == old.values).all():
        raise RuntimeError(f"migration check failed for {sym} {interval}: {len(old)} vs {len(new)} rows")
    legacy.unlink()
    return True


def _download(ticker: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
    df = yf.download(ticker, interval=interval, start=start, end=end,
                     progress=False, auto_adjust=False, prepost=True, threads=False)
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[COLUMNS]
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(NY)
    return df


def _merge_save(sym: str, interval: str, new: pd.DataFrame) -> int:
    """Merge new bars (they win on duplicates) and rewrite only the months they touch."""
    migrate(sym, interval)
    new = new.dropna(subset=["open", "high", "low", "close"])
    if new.empty:
        return 0
    months = set(new.index.tz_convert("UTC").strftime("%Y-%m"))
    d = _store_dir(sym, interval)
    old_parts = [_read(d / f"{m}.csv") for m in months if (d / f"{m}.csv").exists()]
    old = pd.concat(old_parts) if old_parts else pd.DataFrame(columns=COLUMNS)
    merged = pd.concat([old, new])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    _write_months(sym, interval, merged)
    return len(merged) - len(old)


def trade_date(index: pd.DatetimeIndex):
    """CME trade date of each bar: the session that opens at 18:00 NY belongs to the next day."""
    return (index + pd.Timedelta(hours=6)).date


def replace_sessions(sym: str, interval: str, new: pd.DataFrame) -> int:
    """Replace whole sessions: every stored bar whose trade date `new` covers is dropped first,
    so a session never mixes two sources. Returns the number of sessions replaced."""
    migrate(sym, interval)
    new = new.dropna(subset=["open", "high", "low", "close"])
    if new.empty:
        return 0
    dates = set(trade_date(new.index))
    months = set(new.index.tz_convert("UTC").strftime("%Y-%m"))
    d = _store_dir(sym, interval)
    old_parts = [_read(d / f"{m}.csv") for m in months if (d / f"{m}.csv").exists()]
    old = pd.concat(old_parts) if old_parts else pd.DataFrame(columns=COLUMNS)
    if len(old):
        old = old[[t not in dates for t in trade_date(old.index)]]
    merged = pd.concat([old, new[COLUMNS]])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    _write_months(sym, interval, merged)
    return len(dates)


def collect(verbose: bool = True) -> dict[str, int]:
    """Fetch everything Yahoo still has and merge it into the local store.

    1m: last 29 days in 7-day chunks. 60m: last ~720 days (used for 1H/4H context).
    Returns the number of new bars added per store.
    """
    now = datetime.now(timezone.utc)
    added = {}
    for sym, ticker in TICKERS.items():
        chunks = []
        start = now - timedelta(days=29)
        while start < now:
            end = min(start + timedelta(days=7), now)
            chunks.append(_download(ticker, "1m", start, end))
            start = end
        n = _merge_save(sym, "1m", pd.concat(chunks))
        added[f"{sym}_1m"] = n
        n = _merge_save(sym, "60m", _download(ticker, "60m", now - timedelta(days=720), now))
        added[f"{sym}_60m"] = n
        if verbose:
            print(f"{sym}: +{added[f'{sym}_1m']} 1m bars, +{added[f'{sym}_60m']} 60m bars")
    return added


def collect_recent(hours: float = 3.0) -> dict[str, int]:
    """Light update for the live loop (one small request per symbol): the last few hours of 1-minute bars."""
    now = datetime.now(timezone.utc)
    added = {}
    for sym, ticker in TICKERS.items():
        added[f"{sym}_1m"] = _merge_save(sym, "1m", _download(ticker, "1m", now - timedelta(hours=hours), now))
    return added
