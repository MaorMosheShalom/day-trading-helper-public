# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Daily (end-of-day) signals for strategies that filter on the previous days, e.g. a "dip day" flag known before the
open. Plans that need them provide their own implementation; the public example plan needs none."""
from __future__ import annotations

import pandas as pd


def signals(today=None) -> pd.DataFrame:
    """One row per date (index = datetime.date), one column per signal. Empty: no daily signals."""
    return pd.DataFrame()


def download() -> list:
    """Refresh the daily data the signals need; returns the names that failed. Nothing to download here."""
    return []
