# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Loading every stored trade date as (ES Day, NQ Day) pairs - the input of every backtest and replay."""
from __future__ import annotations

from .days import load_days, paired


def load_pairs():
    return paired(load_days("ES"), load_days("NQ"))
