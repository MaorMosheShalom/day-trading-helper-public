# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""No-trade days (STRATEGY §1): US holidays and CPI/PPI/PCE/NFP/FOMC release days.

The list lives in data/calendar/events.csv (compiled from bls.gov, bea.gov,
federalreserve.gov and nyse.com) and must be extended as new schedules come out.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd

EVENTS_CSV = Path(__file__).resolve().parent.parent / "data" / "calendar" / "events.csv"
NO_TRADE_EVENTS = {"HOLIDAY", "CPI", "PPI", "PCE", "NFP", "FOMC"}


@lru_cache
def _events() -> pd.DataFrame:
    df = pd.read_csv(EVENTS_CSV, parse_dates=["date"])
    df["date"] = df["date"].dt.date
    return df


def coverage() -> tuple[date, date]:
    ev = _events()
    return ev["date"].min(), ev["date"].max()


def events_on(d: date) -> list[str]:
    ev = _events()
    return ev.loc[ev["date"] == d, "event"].tolist()


def no_trade_reason(d: date) -> str | None:
    """Why `d` is a no-trade day, or None if it's tradable."""
    if d.weekday() >= 5:
        return "weekend"
    lo, hi = coverage()
    if not lo <= d <= hi:
        return "outside calendar coverage"
    hits = [e for e in events_on(d) if e in NO_TRADE_EVENTS]
    return ", ".join(hits) if hits else None
