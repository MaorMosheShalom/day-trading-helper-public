# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Engine invariants: prop-firm case rules, position sizing, no look-ahead, one trade at a time, replay == live."""
from datetime import date

import numpy as np

from dth.lab.days import RTH_CLOSE, Bars, Day
from dth.lab.sim import Trade
from dth.live import account, engine, runner

D = date(2026, 1, 5)


def trade(pts, sym="ES", tag="x", t_in=600, entry=100.0, stop=99.0, atr=10.0):
    t = Trade(sym, D, 1, t_in, entry, stop, np.nan, t_in + 5, entry + pts, "time", pts=pts, tag=tag)
    t.atr = atr
    return t


def close_with(acc, pts, micros=10, k=[0]):
    """Open a 10-micro ES position directly and close it for `pts` points ($50 per point)."""
    k[0] += 1
    tr = trade(pts, t_in=600 + k[0])
    key = (tr.sym, tr.tag, tr.t_in, tr.side)
    acc.open[key] = account.Position(key, "ES", 1, tr.entry, micros, tr.tag)
    acc.on_exit(D, tr.t_out, tr)


def new_acc():
    acc = account.PropAccount(account.PropRules(), 0.01)
    acc.start_day(D)
    return acc


# ------------------------------------------------------------------ prop-firm case rules
def test_case_lost_at_peak_minus_2k_and_new_case_costs_100():
    acc = new_acc()
    close_with(acc, 20)                     # 51,000 -> peak 51,000, limit 49,000
    assert acc.limit == 49_000
    close_with(acc, -41)                    # 48,950 <= 49,000
    assert acc.log[-1]["event"] == "case LOST" and acc.phase == "none"
    acc.start_day(D)
    assert acc.case_no == 2 and acc.fees_paid == 200 and acc.balance == 50_000


def test_pass_at_53k_then_funded_limit_locks_at_50k():
    acc = new_acc()
    close_with(acc, 60)                     # 53,000 -> test passed
    assert acc.log[-1]["event"] == "TEST PASSED"
    acc.start_day(D)
    assert acc.phase == "funded" and acc.balance == 50_000 and acc.limit == 48_000
    close_with(acc, 50)                     # 52,500: limit = min(50,500, 50,000) = 50,000
    assert acc.limit == 50_000
    close_with(acc, -51)                    # 49,950 -> lost
    assert acc.log[-1]["event"] == "case LOST"


def test_payout_at_60k_leaves_55k_and_pays_80_percent():
    acc = new_acc()
    close_with(acc, 60)
    acc.start_day(D)
    for _ in range(4):
        close_with(acc, 50)                 # +2,500 each -> 60,000
    assert acc.log[-1]["event"] == "PAYOUT"
    assert acc.balance == 55_000 and acc.paid_to_user == 4_000


def test_sizing_uses_risk_distance():
    acc = new_acc()
    assert acc.on_entry(D, 601, trade(0, entry=100, stop=98)) == 50      # $500 / (2 pts * $5) = 50 (cap 50)
    acc = account.PropAccount(account.PropRules(), 0.002)
    acc.start_day(D)
    assert acc.on_entry(D, 601, trade(0, entry=100, stop=99.9, atr=20)) == 10   # min 0.1 ATR = 2 pts -> $100 / $10
    assert account.risk_distance(trade(0, entry=100, stop=99.9, atr=20)) == 2.0
    assert account.risk_distance(trade(0, tag="gao_momentum", stop=np.nan, atr=20)) == 5.0   # no stop: 0.25 ATR unit


def test_risk_policy_by_phase_and_balance():
    p = account.RiskPolicy()
    acc = account.PropAccount(account.PropRules(**account.PLAN_RULES), p)
    acc.start_day(D)
    assert acc.phase == "test" and acc.current_risk() == 0.03
    acc.phase, acc.balance = "funded", 51_999
    assert acc.current_risk() == 0.01
    acc.balance = 52_000
    assert acc.current_risk() == 0.03


# ------------------------------------------------------------------ no look-ahead, one trade at a time
def test_snapshot_hides_the_future():
    n = 30
    t = np.arange(570, 570 + n)
    b = Bars(t, *(np.arange(n, dtype=float) for _ in range(4)), np.ones(n))
    day = Day("ES", D, b, Bars(*(np.array([]),) * 6), "real", None, {"atr": 1.0, "high": 9, "low": 0, "close": 5, "pdc": 1})
    s = engine.snapshot(day, 580)
    assert len(s.rth) == 10 and s.rth.t[-1] == 579
    assert "high" not in s.ctx and "close" not in s.ctx and s.ctx["pdc"] == 1
    assert len(day.rth) == n                                          # the original is untouched


def _ev(kind, sym, tag, t_in, t_out, T):
    tr = trade(1.0, sym=sym, tag=tag, t_in=t_in)
    tr.t_out = t_out
    return engine.Event(kind, sym, D, T, tr)


def test_gate_one_trade_at_a_time_and_priority():
    from dth.lab import system
    a_in = _ev("entry", "NQ", "gao_momentum", 600, 650, 601)         # low priority, first
    b_in = _ev("entry", "NQ", "orb5_concretum", 620, 700, 621)       # comes while a is open -> skipped
    a_out = _ev("exit", "NQ", "gao_momentum", 600, 650, 651)
    c_in = _ev("entry", "NQ", "orb5_concretum", 650, 690, 651)       # in a's exit minute -> skipped
    d_in = _ev("entry", "NQ", "gao_momentum", 660, 690, 661)         # same minute as e, lower priority
    e_in = _ev("entry", "NQ", "orb5_concretum", 660, 690, 661)
    x_in = _ev("entry", "NQ", "not_in_the_plan", 670, 690, 671)
    out = engine.plan_events([a_in, b_in, a_out, c_in, d_in, e_in, x_in], "example")
    got = [(e.kind, e.trade.tag, e.trade.t_in) for e in out]
    assert got == [("entry", "gao_momentum", 600), ("exit", "gao_momentum", 600), ("entry", "orb5_concretum", 660)]
    taken, skipped = system.pick([e.trade for e in (a_in, b_in, c_in, d_in, e_in, x_in)], "example")
    assert [(t.tag, t.t_in) for t in taken] == [("gao_momentum", 600), ("orb5_concretum", 660)]
    assert len(skipped) == 3


def test_replay_has_no_look_ahead_and_equals_the_live_trader(tmp_path):
    """Every (synthetic) day minute by minute: zero look-ahead violations, and the live paper trader takes exactly
    the trades of the replay - the property that makes backtest results apply to live trading."""
    from dth.lab import evaluate
    pairs = evaluate.load_pairs()
    assert len(pairs) >= 20
    n_events = 0
    for es, nq in pairs[:15]:
        ev, viol = engine.replay_day(es, nq, None, "example")
        assert viol == []
        want = engine.plan_events(ev, "example")
        tr = runner.PaperTrader(tmp_path / f"{es.d}.pkl", tmp_path / f"{es.d}.csv", plan_name="example")
        got = tr.run_until(es.d, RTH_CLOSE, {"ES": es, "NQ": nq})
        f = lambda evs: [(e.kind, e.sym, e.trade.tag, e.T, round(e.trade.pts, 4)) for e in evs]
        assert f(got) == f(want)
        n_events += len(got)
    assert n_events > 0
