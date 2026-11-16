# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""PaperTrader: runs the system on live data exactly like the replay (same snapshot -> Stepper -> account code),
one minute at a time, and sends the resulting NET position per symbol to a broker.

State (account, today's steppers, broker positions) is saved after every minute, so the process can be stopped
and restarted at any time. If the data feed arrives late, the missed minutes are stepped through in order.
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .. import data
from ..lab import daily, system
from ..lab.days import RTH_CLOSE, RTH_OPEN, build_live
from . import account, engine
from .broker import Broker, PaperBroker


@dataclass
class State:
    acc: account.PropAccount
    day: date | None = None
    last_T: int = RTH_OPEN
    steppers: dict = field(default_factory=dict)
    positions: dict = field(default_factory=dict)
    gate: engine.OneAtATime | None = None          # the plan rule (None = old stacked system)
    plan_name: str = "example"                      # the plan this account trades (dth/lab/system.plan_spec)
    entry_info: dict = field(default_factory=dict)  # open trade -> its entry details (for the trade pictures)


class PaperTrader:
    def __init__(self, state_path: Path, orders_path: Path, risk_pct=None, rules: account.PropRules | None = None,
                 broker: Broker | None = None, plan: bool = True, plan_name: str = system.LIVE_PLAN):
        """plan=True: one trade at a time of `plan_name` (dth/lab/system.plan_spec); plan=False: every signal trades.
        risk_pct: a number or a policy (default: account.risk_for(plan_name))."""
        self.state_path = Path(state_path)
        if self.state_path.exists():
            self.state: State = pickle.loads(self.state_path.read_bytes())
            if not hasattr(self.state, "gate"):
                self.state.gate = None
        else:
            if risk_pct is None:
                risk_pct = account.risk_for(plan_name) if plan else 0.02
            rules = rules or (account.PropRules(**account.PLAN_RULES) if plan else account.PropRules())
            self.state = State(account.PropAccount(rules, risk_pct),
                               gate=engine.OneAtATime(plan=plan_name) if plan else None, plan_name=plan_name)
        self.trades_fn = system.plan_spec(getattr(self.state, "plan_name", "plan"))[0]
        self.broker = broker or PaperBroker(orders_path, self.state.positions)

    # ------------------------------------------------------------------ one session
    def _new_day(self, d: date):
        st = self.state
        if st.acc.open and st.day is not None:
            # a day that was never finished (no data to finish it): close what was open flat, never carry it over
            st.acc._close_all(st.day, RTH_CLOSE, None, "day not finished (no data) - closed flat")
            st.acc._event(st.day, RTH_CLOSE, "open trade closed flat: the day could not be finished")
        st.entry_info = {}
        st.day, st.last_T = d, RTH_OPEN
        st.steppers = {"ES": engine.Stepper("ES"), "NQ": engine.Stepper("NQ")}
        if st.gate is not None:
            st.gate.new_day()
        st.acc.start_day(d)

    def run_until(self, d: date, upto_T: int, days: dict | None = None) -> list:
        """Process every minute T (last_T, upto_T] of date d. `days` = {"ES": Day, "NQ": Day} (live build if None)."""
        st = self.state
        if st.day != d:
            self._new_day(d)
        days = days or {s: build_live(s, d) for s in ("ES", "NQ")}
        if any(v is None for v in days.values()):
            return []
        if days["ES"].no_trade:                      # holiday / news day: nothing to trade, the day is done
            st.last_T = RTH_CLOSE
            self.save()
            return []
        try:
            sig = daily.signals(d)
            row = sig.loc[d] if d in sig.index else None
        except Exception as e:                   # daily signals missing
            print(f"daily signals not available ({type(e).__name__})")
            row = None
        events = []
        for T in range(st.last_T + 1, min(upto_T, RTH_CLOSE) + 1):
            snaps = {s: engine.snapshot(days[s], T) for s in ("ES", "NQ")}
            minute = []
            for sym, other in (("ES", "NQ"), ("NQ", "ES")):
                minute += st.steppers[sym].step(self.trades_fn(snaps[sym], snaps[other], row), T, d)
            if st.gate is not None:
                minute = st.gate.gate(minute)
            for ev in minute:
                self._apply(ev, snaps)
                events.append(ev)
            st.last_T = T
            self._sync_broker(d, T)
        self.save()
        return events

    def _apply(self, ev, snaps):
        acc = self.state.acc

        def price_at(sym, T):
            b = snaps[sym].rth
            return float(b.c[-1]) if len(b) else float("nan")
        tr = ev.trade
        k = (tr.sym, tr.tag, tr.t_in, tr.side)
        info = getattr(self.state, "entry_info", None)
        if info is None:
            info = self.state.entry_info = {}
        if ev.kind == "entry":
            bal, ph, case = acc.balance, acc.phase, acc.case_no
            rp = acc.current_risk() if acc.phase in ("test", "funded") else 0.0
            n = acc.on_entry(ev.d, ev.T, tr)
            if n:      # what the trade picture needs (the case at the entry + the trade's plan)
                info[k] = dict(d=ev.d, sym=tr.sym, setup=tr.tag, side=tr.side, t_in=tr.t_in, entry=tr.entry,
                               stop=tr.stop, target=tr.target, atr=tr.atr, dist=account.risk_distance(tr),
                               bal_before=bal, risk_pct=100 * rp, case=case, phase=ph, micros=n)
        else:
            nt, nl = len(acc.trades), len(acc.log)
            acc.on_exit(ev.d, ev.T, tr, price_at)
            if len(acc.trades) > nt and k in info:
                e = info.pop(k)
                evs = [x["event"] for x in acc.log[nl:] if x["event"] in ("case LOST", "TEST PASSED", "PAYOUT")]
                acc.trades[nt].update(t_in=tr.t_in, t_out=tr.t_out, entry=tr.entry, stop=tr.stop, target=tr.target,
                                      exit=tr.exit, R=tr.pts / e["dist"] if e["dist"] else 0.0,
                                      **{x: e[x] for x in ("dist", "bal_before", "risk_pct", "atr")},
                                      event={"case LOST": "CASE LOST on this trade",
                                             "TEST PASSED": "TEST PASSED on this trade",
                                             "PAYOUT": "PAYOUT after this trade"}.get(evs[0], "") if evs else "")

    def _sync_broker(self, d: date, T: int):
        net = {"ES": 0, "NQ": 0}
        for p in self.state.acc.open.values():
            net[p.sym] += p.side * p.micros
        # the change happened during bar T-1 (at its open, or when a resting stop order traded)
        when = (datetime(d.year, d.month, d.day) + timedelta(minutes=T - 1)).strftime("%Y-%m-%d %H:%M NY")
        for sym, target in net.items():
            if target != self.broker.position(sym):
                ps = [p for p in self.state.acc.open.values() if p.sym == sym]
                tags = sorted({p.tag for p in ps}) or ["flat"]
                self.broker.set_position(sym, target, when, ", ".join(tags), protective_stop(ps))
        self.state.positions = dict(getattr(self.broker, "positions", {}))

    # ------------------------------------------------------------------ persistence / reporting
    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_bytes(pickle.dumps(self.state))
        acc = self.state.acc
        summary = {"case": acc.case_no, "phase": acc.phase, "balance": round(acc.balance, 2),
                   "limit": round(acc.limit, 2) if acc.phase in ("test", "funded") else None,
                   "risk_pct": str(acc.risk_pct), "risk_now": acc.current_risk() if acc.phase in ("test", "funded") else None,
                   "plan": (system.PLAN_NAMES.get(getattr(self.state, "plan_name", "example"), "?")
                            + ", one trade at a time") if self.state.gate is not None else "stacked (old)", "fees_paid": acc.fees_paid, "paid_to_you": acc.paid_to_user,
                   "net": acc.paid_to_user - acc.fees_paid, "open": [vars(p) for p in acc.open.values()],
                   "last_day": str(self.state.day), "last_minute": self.state.last_T,
                   "recent_events": acc.log[-10:]}
        self.state_path.with_suffix(".json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")

    def trades(self) -> pd.DataFrame:
        return pd.DataFrame(self.state.acc.trades)


CATASTROPHE_STOP_ATR = 1.0      # broker stop for trades without a hard stop (their losses normally stay well inside 1 ATR)


def protective_stop(ps: list):
    """The stop order to keep at the broker for these open positions (one trade at a time -> one position)."""
    if len(ps) != 1:
        return None
    p = ps[0]
    stop = getattr(p, "stop", float("nan"))
    if stop == stop:
        return float(stop)
    atr = getattr(p, "atr", float("nan"))
    return float(p.entry - p.side * CATASTROPHE_STOP_ATR * atr) if atr == atr else None


def now_minute_ny() -> tuple[date, int]:
    n = datetime.now(ZoneInfo(data.NY))
    return n.date(), n.hour * 60 + n.minute
