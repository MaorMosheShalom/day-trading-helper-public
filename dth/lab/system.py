# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Plan registry: which strategies a plan trades and in what priority, shared by the backtest and the live tool.

A *plan* is (trades function, components, priority):
  trades_fn(day, other, sig_row) -> [Trade]   every trade its strategies would take on `day` (pure, no I/O)
  components                                  the strategy tags the one-trade-at-a-time gate accepts
  priority {(tag, symbol): score}             which signal wins when two arrive in the same minute

The public repository ships one EXAMPLE plan built from two published, textbook strategies (dth/lab/examples.py).
Production plans live in an optional private module, `dth/private/plans.py` (not part of this repository), which
may define:
  PLANS      {name: (trades_fn, components, priority, display_name)}
  LIVE_PLAN  the plan the live tool trades
  PLAN_RISK  {name: risk per trade}  (see dth/live/account.risk_for)
"""
from __future__ import annotations

from .days import Day
from .examples import gao_momentum, orb5_concretum

MICRO_PER_POINT = {"ES": 5.0, "NQ": 2.0}          # MES / MNQ: $ per index point of one micro contract

EXAMPLE_COMPONENTS = ("orb5_concretum", "gao_momentum")
EXAMPLE_PRIORITY = {("orb5_concretum", "NQ"): 2.0, ("gao_momentum", "NQ"): 1.0}


def example_trades_for(day: Day, other: Day | None, sig_row=None) -> list:
    """The example plan: 5-minute opening-range breakout + intraday momentum, NQ only."""
    if day.no_trade or not len(day.rth) or day.sym != "NQ":
        return []
    out = []
    for name, fn in (("orb5_concretum", orb5_concretum), ("gao_momentum", gao_momentum)):
        tr = fn(day, other, None, {})
        if tr is not None:
            tr.tag = name
            out.append(tr)
    return out


_PLANS = {"example": (example_trades_for, EXAMPLE_COMPONENTS, EXAMPLE_PRIORITY, "EXAMPLE")}
LIVE_PLAN = "example"
PLAN_RISK: dict = {"example": 0.02}

try:                                              # optional private production plans (not in this repository)
    from ..private import plans as _private      # type: ignore
    _PLANS.update(getattr(_private, "PLANS", {}))
    LIVE_PLAN = getattr(_private, "LIVE_PLAN", LIVE_PLAN)
    PLAN_RISK.update(getattr(_private, "PLAN_RISK", {}))
except ImportError:
    pass

PLAN_NAMES = {k: v[3] for k, v in _PLANS.items()}


def plan_spec(name: str = LIVE_PLAN):
    """(trades function, components, priority) of a registered plan."""
    fn, comps, prio, _ = _PLANS[name]
    return fn, comps, prio


def pick(trades: list, name: str = LIVE_PLAN) -> tuple[list, list]:
    """Full-day version of the one-trade-at-a-time rule: (taken, [(skipped trade, the trade that blocked it)])."""
    _, comps, prio = plan_spec(name)
    cand = [t for t in trades if t.tag in comps]
    cand.sort(key=lambda t: (t.t_in, -prio.get((t.tag, t.sym), -1e9)))
    taken, skipped, busy_until, holder = [], [], -10 ** 9, None
    for t in cand:
        if t.t_in > busy_until:
            taken.append(t)
            busy_until, holder = t.t_out, t
        else:
            skipped.append((t, holder))
    return taken, skipped
