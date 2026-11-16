# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Minute-by-minute live replay of the system.

At minute T (minutes after midnight NY) the strategies see a *snapshot*: the RTH bars that have CLOSED (start < T),
all overnight bars, and the day context without today's high / low / close. They are the same functions the live
tool calls (the plan's trades function, dth/lab/system.plan_spec). The Stepper compares each snapshot's output with what it already knows:
  - a trade seen for the first time is an ENTRY event; it must have started on the last closed bar (t_in == T - 1),
  - a trade whose exit became final is an EXIT event; it must have ended on the last closed bar (t_out == T - 1),
  - a known trade must never change (side / entry / stop) or disappear.
Anything else would mean the backtest used information that wasn't available yet - it is recorded as a violation.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from ..lab import sim, system
from ..lab.days import RTH_CLOSE, RTH_OPEN, Bars, Day

TODAY_KEYS = ("high", "low", "close", "moves")        # known only after the close


def snapshot(day: Day, T: int) -> Day:
    """The day as it looked at minute T: RTH bars that started before T."""
    r = day.rth
    n = r.idx(T)
    snap = copy.copy(day)
    snap.rth = Bars(r.t[:n], r.o[:n], r.h[:n], r.l[:n], r.c[:n], r.v[:n])
    snap.ctx = {k: v for k, v in day.ctx.items() if k not in TODAY_KEYS}
    return snap


def is_final(tr, T: int) -> bool:
    """Has this trade's exit actually happened (vs. 'still open, marked at the last bar')?
    Clock exits are final once the clock has passed them (the last minute may be missing in the data)."""
    if tr.reason == "open":
        return T >= RTH_CLOSE
    if tr.reason == "time":
        return T >= sim.EXIT_BY
    return True


def key(tr) -> tuple:
    return tr.tag, tr.t_in, tr.side


@dataclass
class Event:
    kind: str                 # "entry" | "exit"
    sym: str
    d: object
    T: int                    # minute the event became known (end of the bar it happened on)
    trade: object             # dth.lab.sim.Trade (for exits: the final version)


@dataclass
class Stepper:
    sym: str
    known: dict = field(default_factory=dict)        # key -> Trade as first seen
    closed: set = field(default_factory=set)
    violations: list = field(default_factory=list)

    def step(self, trades: list, T: int, d) -> list[Event]:
        events, seen = [], set()
        last = T - 1
        for tr in trades:
            k = key(tr)
            seen.add(k)
            if k not in self.known:
                if tr.t_in != last:
                    self.violations.append(f"{d} {self.sym} {tr.tag}: entry at {tr.t_in} first seen at T={T}")
                self.known[k] = copy.copy(tr)
                events.append(Event("entry", self.sym, d, T, copy.copy(tr)))
            else:
                k0 = self.known[k]
                if (k0.entry, k0.stop) != (tr.entry, tr.stop) and not (k0.stop != k0.stop and tr.stop != tr.stop):
                    self.violations.append(f"{d} {self.sym} {tr.tag}: entry/stop changed after entry (T={T})")
            if k not in self.closed and is_final(tr, T):
                clock = tr.reason in ("time", "open", "close")
                if (tr.t_out != last) and not (clock and tr.t_out <= last):
                    self.violations.append(f"{d} {self.sym} {tr.tag}: exit at {tr.t_out} first final at T={T}")
                self.closed.add(k)
                events.append(Event("exit", self.sym, d, T, copy.copy(tr)))
        for k in self.known:
            if k not in seen:
                self.violations.append(f"{d} {self.sym} {k[0]}: trade from {k[1]} disappeared at T={T}")
        return events


@dataclass
class OneAtATime:
    """The plan rule on the live event stream (same result as system.pick on the full day).
    Feed it ALL events of one minute T (both symbols); it returns the ones the account acts on."""
    busy_until: int = -10 ** 9          # t_out of the last taken trade (+inf while it is open)
    holder: tuple | None = None         # (sym, tag, t_in, side) of the open taken trade
    taken: set = field(default_factory=set)
    skipped: list = field(default_factory=list)
    plan: str = "plan"                  # which plan's components / priority (dth/lab/system.plan_spec)

    def new_day(self):
        self.busy_until, self.holder, self.taken, self.skipped = -10 ** 9, None, set(), []

    def gate(self, events: list[Event]) -> list[Event]:
        out = []
        _, comps, prio = system.plan_spec(getattr(self, "plan", "plan"))
        k = lambda e: (e.sym, e.trade.tag, e.trade.t_in, e.trade.side)
        for e in events:                                    # exits first: free the account
            if e.kind == "exit" and k(e) in self.taken:
                out.append(e)
                if k(e) == self.holder:
                    self.busy_until, self.holder = e.trade.t_out, None
        entries = [e for e in events if e.kind == "entry" and e.trade.tag in comps]
        entries.sort(key=lambda e: -prio.get((e.trade.tag, e.sym), -1e9))
        for e in entries:
            if self.holder is None and e.trade.t_in > self.busy_until:
                self.taken.add(k(e))
                self.holder, self.busy_until = k(e), 10 ** 9
                out.append(e)
            else:
                self.skipped.append((e, self.holder))
        # an exit in the same minute as its own entry (a 1-bar trade) comes after the entry
        for e in events:
            if e.kind == "exit" and k(e) in self.taken and e not in out:
                out.append(e)
                if k(e) == self.holder:
                    self.busy_until, self.holder = e.trade.t_out, None
        return out


def plan_events(events: list[Event], plan: str = "plan") -> list[Event]:
    """Apply the plan rule to one day's time-ordered events (from replay_day)."""
    g, out, i = OneAtATime(plan=plan), [], 0
    while i < len(events):
        j = i
        while j < len(events) and events[j].T == events[i].T:
            j += 1
        out += g.gate(events[i:j])
        i = j
    return out


def replay_day(es: Day, nq: Day, sig_row, plan: str = "plan") -> tuple[list[Event], list[str]]:
    """Feed one day minute by minute through the system; return its events (time-ordered) and violations."""
    trades_fn = system.plan_spec(plan)[0]
    steppers = {"ES": Stepper("ES"), "NQ": Stepper("NQ")}
    events: list[Event] = []
    if es.no_trade:
        return events, []
    for T in range(RTH_OPEN + 1, RTH_CLOSE + 1):
        snaps = {"ES": snapshot(es, T), "NQ": snapshot(nq, T)}
        for sym, other in (("ES", "NQ"), ("NQ", "ES")):
            trades = trades_fn(snaps[sym], snaps[other], sig_row)
            events += steppers[sym].step(trades, T, es.d)
    viol = steppers["ES"].violations + steppers["NQ"].violations
    # anything still open at the close (shouldn't happen: every component is flat by 16:00)
    for st in steppers.values():
        for k in st.known:
            if k not in st.closed:
                viol.append(f"{es.d} {st.sym} {k[0]}: still open after the close")
    return events, viol
