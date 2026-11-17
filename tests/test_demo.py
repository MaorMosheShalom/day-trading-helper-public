# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""The free demo (scripts/autotrade.py live paper trading): restart / catch-up, live pictures, stop flag."""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from dth.lab import system  # noqa: E402
from dth.lab.days import RTH_CLOSE, RTH_OPEN, build_live  # noqa: E402
from dth.live import engine, runner  # noqa: E402

def _two_days():
    from dth.lab import evaluate
    ok = [es.d for es, nq in evaluate.load_pairs() if not es.no_trade]
    return ok[20], ok[21]


D1, D2 = _two_days()


def days_of(d):
    days = {s: build_live(s, d) for s in ("ES", "NQ")}
    if any(v is None or len(v.rth) < 380 for v in days.values()):
        pytest.skip(f"{d} not in the stored data")
    return days


def trader(tmp_path, name):
    return runner.PaperTrader(tmp_path / f"{name}.pkl", tmp_path / f"{name}.csv", plan_name=system.LIVE_PLAN)


def summary(tr):
    return [(t["date"], t["setup"], t["side"], t["time"], t["usd"]) for t in tr.state.acc.trades], round(tr.state.acc.balance, 2)


def test_stopped_and_restarted_equals_one_run(tmp_path):
    """Killed at 11:40 (state saved every minute), restarted: same trades and balance as one uninterrupted run."""
    days = days_of(D2)
    a = trader(tmp_path, "a")
    a.run_until(D2, RTH_CLOSE, days)
    b = trader(tmp_path, "b")
    b.run_until(D2, 700, days)
    b = trader(tmp_path, "b")                            # a new process loads the saved case
    b.run_until(D2, RTH_CLOSE, days)
    assert summary(a) == summary(b) and len(a.state.acc.trades) > 0


def test_next_day_catch_up_finishes_the_unfinished_day(tmp_path):
    import autotrade as AT
    d1, d2 = days_of(D1), days_of(D2)
    a = trader(tmp_path, "a")
    a.run_until(D1, RTH_CLOSE, d1)
    a.run_until(D2, RTH_CLOSE, d2)
    b = trader(tmp_path, "b")
    b.run_until(D1, 700, d1)                             # stopped in the middle of D1 (e.g. window closed)
    AT.catch_up(b, "test", D2, RTH_OPEN, [D1, D2])       # next morning, before the open: finishes D1 only
    assert b.state.day == D1 and b.state.last_T == RTH_CLOSE
    b.run_until(D2, RTH_CLOSE, d2)
    assert summary(a) == summary(b)


def test_trade_rows_keep_the_picture_details(tmp_path):
    days = days_of(D2)
    a = trader(tmp_path, "a")
    a.run_until(D2, RTH_CLOSE, days)
    for r in a.state.acc.trades:
        assert {"t_in", "t_out", "entry", "stop", "exit", "dist", "R", "bal_before", "risk_pct"} <= set(r)
        assert abs(r["risk_pct"] - 2.0) < 0.01                       # the example plan: fixed 2%
    assert a.state.entry_info == {}                                   # nothing left open


def test_old_saved_case_without_entry_info_still_works(tmp_path):
    days = days_of(D2)
    a = trader(tmp_path, "a")
    a.state.__dict__.pop("entry_info")                               # a case saved by the old version
    a.save()
    a = trader(tmp_path, "a")
    a.run_until(D2, RTH_CLOSE, days)
    assert len(a.state.acc.trades) > 0


def test_live_pictures_open_then_closed(tmp_path, monkeypatch):
    import trade_book as TB
    monkeypatch.setattr(TB, "LIVE", tmp_path / "LIVE")
    monkeypatch.setattr(TB, "share", lambda root: None)
    days = days_of(D2)
    probe = trader(tmp_path, "probe")
    evs = probe.run_until(D2, RTH_CLOSE, days)
    first_in = next(e for e in evs if e.kind == "entry")
    first_out = next(e for e in evs if e.kind == "exit" and e.trade.t_in == first_in.trade.t_in)
    tr = trader(tmp_path, "live")
    T = first_in.T
    tr.run_until(D2, T, days)                                         # the trade has just started
    TB.live_update({system.LIVE_PLAN: tr}, {s: engine.snapshot(days[s], T) for s in days})
    pics = [p for p in (tmp_path / "LIVE").rglob("*.png") if not p.name.startswith("_")]
    assert len(pics) == 1 and pics[0].name.endswith("OPEN.png")
    assert "OPEN NOW" in (tmp_path / "LIVE" / "index.html").read_text(encoding="utf-8")
    tr.run_until(D2, first_out.T, days)                               # ... and now it has closed
    TB.live_update({system.LIVE_PLAN: tr}, {s: engine.snapshot(days[s], first_out.T) for s in days})
    pics = [p for p in (tmp_path / "LIVE").rglob("*.png") if not p.name.startswith("_")]
    assert len(pics) == 1 and ("WIN" in pics[0].name or "LOSS" in pics[0].name or "FLAT" in pics[0].name)
    tr.run_until(D2, RTH_CLOSE, days)                                 # the rest of the day
    TB.live_update({system.LIVE_PLAN: tr}, days)
    pics = [p for p in (tmp_path / "LIVE").rglob("*.png") if not p.name.startswith("_")]
    assert len(pics) == len(tr.state.acc.trades)
    import pandas as pd
    month = pd.read_csv(next((tmp_path / "LIVE").rglob("_month.csv")))
    assert round(month["$"].sum()) == round(sum(round(t["usd"]) for t in tr.state.acc.trades))


def test_stop_flag_keeps_it_stopped_today(tmp_path, monkeypatch):
    import autotrade as AT
    monkeypatch.setattr(AT, "OUT_DIR", tmp_path)
    d, _ = runner.now_minute_ny()
    AT.stop_flag(d).write_text("stopped", encoding="utf-8")
    called = []
    monkeypatch.setattr(AT.runner, "PaperTrader", lambda *a, **k: called.append(1))
    AT.live(None, True)
    assert called == []                                               # it didn't even open the paper case


def test_month_page_lists_every_trade_as_text(tmp_path):
    """A busy month: the month page has every trade as searchable text (not only the first rows in a picture)."""
    import pandas as pd
    import trade_book as TB
    n = 25
    t = pd.DataFrame([{"#": i, "date": date(2025, 3, 1 + i % 28), "month": "2025-03", "year": 2025, "symbol": "NQ",
                       "strategy": f"setup {i}", "side": "LONG" if i % 2 else "SHORT", "entry time": "10:05",
                       "entry": 100.0 + i, "stop": None, "target": float("nan"), "exit time": "11:00", "exit": 101.0,
                       "exit reason": "time", "micros": 2, "risk $": 400, "risk %": 2.0,
                       "result": "WIN" if i % 3 else "LOSS", "$": 50 if i % 3 else -40, "R": 0.5,
                       "balance after": 50000 + i, "event": "", "picture": f"x/{i:04d} trade.png" if i < 20 else ""}
                      for i in range(1, n + 1)])
    TB.summaries(t, pd.DataFrame(), {}, tmp_path, "TEST", tmp_path)
    page = next(tmp_path.rglob("_month.html")).read_text(encoding="utf-8")
    assert (tmp_path / "2025" / "2025-03 March" / "_month.png").exists()
    assert page.count("<tr class='trade") == n                       # all 25 rows, as text
    assert "setup 25" in page and "id='q'" in page                    # the last trade + the search box
    assert page.count("class='trade sec'") == 19                      # a picture section for each trade with a picture


def test_start_draws_trades_closed_by_the_old_version(tmp_path, monkeypatch):
    """Trades closed by a version that didn't keep the picture details (the first demo day) get their picture at the
    next start (the catch-up replays that day from the stored bars)."""
    import autotrade as AT
    import trade_book as TB
    monkeypatch.setattr(TB, "LIVE", tmp_path / "LIVE")
    monkeypatch.setattr(TB, "share", lambda root: None)
    days = days_of(D2)
    tr = trader(tmp_path, "old")
    tr.run_until(D2, RTH_CLOSE, days)
    assert tr.state.acc.trades
    for t in tr.state.acc.trades:                                     # what the old version saved
        for k in ("t_in", "t_out", "entry", "stop", "target", "exit", "R", "dist", "bal_before", "risk_pct", "atr"):
            t.pop(k, None)
    AT.catch_up_all({system.LIVE_PLAN: tr}, system.LIVE_PLAN, D2, RTH_CLOSE)
    pics = [p for p in (tmp_path / "LIVE").rglob("*.png") if not p.name.startswith("_")]
    assert len(pics) == len(tr.state.acc.trades)
