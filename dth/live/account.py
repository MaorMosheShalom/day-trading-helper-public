# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""The prop-firm "case" account, fed by engine events in time order (same code for the replay and paper trading).

Rules (a typical 50k futures evaluation), all in PropRules:
  - a case costs FEE; a new one is bought the next trading day after a loss
  - start at START; limit = min(peak - TRAILING, LOCK), checked on the balance after each closed trade
  - TEST: reach PASS_AT -> passed; the funded case starts at START on the next trading day (same limit rule)
  - FUNDED: whenever the balance is >= PAYOUT_AT, withdraw PAYOUT (the user keeps SPLIT of it)
  - when a case ends (lost or passed), its other open trades are closed at that minute's price
Sizing: contracts = floor(balance * risk_pct / (risk distance * $ per point of one micro)), capped at MAX_MICROS open.
  risk distance = max(stop distance, MIN_RISK_ATR * ATR); components without a hard stop use RISK_UNIT_ATR * ATR
  (the typical adverse move of a hold that exits on time, not on a stop).
Optional DAILY_LOSS_STOP: after that much realized loss in a day, flatten and take no more trades that day.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from ..lab.days import COST_PTS
from ..lab.system import MICRO_PER_POINT


@dataclass
class PropRules:
    fee: float = 100.0
    start: float = 50_000.0
    trailing: float = 2_000.0
    lock: float = 50_000.0
    pass_at: float = 53_000.0
    payout_at: float = 60_000.0
    payout: float = 5_000.0
    split: float = 0.8
    max_micros: int = 50
    daily_loss_stop: float | None = None


@dataclass
class RiskPolicy:
    """Risk per trade by case phase and balance :
    the test at 3%; the funded case at 1% while the balance is below $52k, 3% from $52k."""
    test: float = 0.03
    funded_low: float = 0.01
    funded_high: float = 0.03
    switch_at: float = 52_000.0

    def __call__(self, acc) -> float:
        if acc.phase == "test":
            return self.test
        return self.funded_low if acc.balance < self.switch_at else self.funded_high

    def __str__(self):
        return (f"test {self.test:.0%}, funded {self.funded_low:.0%} below ${self.switch_at:,.0f} / "
                f"{self.funded_high:.0%} from ${self.switch_at:,.0f}")


PLAN_RULES = dict(payout=3_000.0)          # the plan withdraws $3k at $60k (-> $57k), not $5k

# Risk per trade of each plan (a fixed fraction or a RiskPolicy): registered in dth/lab/system.PLAN_RISK.


def risk_for(plan_name: str):
    """The risk per trade of a plan: a fixed fraction or a RiskPolicy."""
    from ..lab.system import PLAN_RISK
    return PLAN_RISK.get(plan_name, RiskPolicy())


def risk_text(r) -> str:
    return f"fixed {r:.0%} of the balance" if isinstance(r, (int, float)) else str(r)
MIN_RISK_ATR = 0.1
RISK_UNIT_ATR = {"gao_momentum": 0.25}


@dataclass
class Position:
    key: tuple
    sym: str
    side: int
    entry: float
    micros: int
    tag: str
    stop: float = math.nan          # the trade's stop (nan = no hard stop, e.g. a time exit)
    atr: float = math.nan


@dataclass
class PropAccount:
    rules: PropRules
    risk_pct: float
    case_no: int = 0
    phase: str = "none"                 # "test" | "funded" | "none" (waiting for the next day)
    balance: float = 0.0
    peak: float = 0.0
    open: dict = field(default_factory=dict)
    fees_paid: float = 0.0
    paid_to_user: float = 0.0
    log: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    pending: str | None = "test"        # what starts at the next session ("test" or "funded")
    day_pnl: float = 0.0
    stopped_today: bool = False

    # ------------------------------------------------------------------ state changes
    def _event(self, d, T, what, **kw):
        self.log.append({"date": d, "time": T, "case": self.case_no, "phase": self.phase, "event": what,
                         "balance": round(self.balance, 2), **kw})

    def start_day(self, d):
        """Called before each session: begin whatever was pending (a new test after a loss, or the funded case)."""
        self.day_pnl, self.stopped_today = 0.0, False
        if self.pending == "test":
            self.case_no += 1
            self.fees_paid += self.rules.fee
            self.phase, self.balance, self.peak = "test", self.rules.start, self.rules.start
            self._event(d, None, "case bought", fee=self.rules.fee)
        elif self.pending == "funded":
            self.phase, self.balance, self.peak = "funded", self.rules.start, self.rules.start
            self._event(d, None, "funded case starts")
        self.pending = None

    def current_risk(self) -> float:
        """Risk per trade right now (risk_pct is a number or a policy f(account), e.g. RiskPolicy)."""
        return self.risk_pct(self) if callable(self.risk_pct) else self.risk_pct

    @property
    def limit(self) -> float:
        return min(self.peak - self.rules.trailing, self.rules.lock)

    def _micros_open(self) -> int:
        return sum(p.micros for p in self.open.values())

    def on_entry(self, d, T, tr) -> int:
        """Size and open a trade. Returns the micros taken (0 = skipped)."""
        if self.phase not in ("test", "funded") or self.stopped_today:
            return 0
        dist = risk_distance(tr)
        if not dist or dist != dist:
            return 0
        rp = self.current_risk()
        n = int(self.balance * rp // (dist * MICRO_PER_POINT[tr.sym]))
        n = min(n, self.rules.max_micros - self._micros_open())
        if n <= 0:
            return 0
        k = (tr.sym, tr.tag, tr.t_in, tr.side)
        self.open[k] = Position(k, tr.sym, tr.side, tr.entry, n, tr.tag, tr.stop, tr.atr)
        return n

    def on_exit(self, d, T, tr, price_at=None):
        k = (tr.sym, tr.tag, tr.t_in, tr.side)
        pos = self.open.pop(k, None)
        if pos is None:
            return
        self._book(d, T, pos, tr.pts, tr.reason)
        self._check(d, T, price_at)
        lim = self.rules.daily_loss_stop
        if lim and not self.stopped_today and self.phase in ("test", "funded") and self.day_pnl <= -lim:
            self._close_all(d, T, price_at, "daily loss stop - flattened")
            self.stopped_today = True
            self._event(d, T, "daily loss stop")
            self._check(d, T, price_at)

    def _book(self, d, T, pos, pts, reason):
        usd = pts * MICRO_PER_POINT[pos.sym] * pos.micros
        self.balance += usd
        self.day_pnl += usd
        self.trades.append({"date": d, "time": T, "case": self.case_no, "phase": self.phase, "sym": pos.sym,
                            "setup": pos.tag, "side": pos.side, "micros": pos.micros, "pts": round(pts, 2),
                            "usd": round(usd, 2), "reason": reason, "balance": round(self.balance, 2)})

    def _close_all(self, d, T, price_at, reason):
        for k, pos in list(self.open.items()):
            px = price_at(pos.sym, T) if price_at else pos.entry
            self._book(d, T, pos, pos.side * (px - pos.entry) - COST_PTS[pos.sym], reason)
        self.open.clear()

    def _check(self, d, T, price_at):
        """Apply the case rules on the balance after a closed trade."""
        r = self.rules
        if self.balance <= self.limit:
            self._close_all(d, T, price_at, "case lost - flattened")
            self._event(d, T, "case LOST", limit=round(self.limit, 2))
            self.phase, self.pending = "none", "test"
            return
        self.peak = max(self.peak, self.balance)
        if self.phase == "test" and self.balance >= r.pass_at:
            self._close_all(d, T, price_at, "test passed - flattened")
            self._event(d, T, "TEST PASSED")
            self.phase, self.pending = "none", "funded"
            return
        while self.phase == "funded" and self.balance >= r.payout_at:
            self.balance -= r.payout
            self.paid_to_user += r.payout * r.split
            self._event(d, T, "PAYOUT", withdrawn=r.payout, to_user=r.payout * r.split)

    def to_dict(self) -> dict:
        out = asdict(self)
        out["open"] = [asdict(p) for p in self.open.values()]
        return out


def risk_distance(tr) -> float:
    """Points of adverse move one trade is sized for."""
    atr = tr.atr if tr.atr == tr.atr else 0.0
    if tr.tag in RISK_UNIT_ATR:
        return RISK_UNIT_ATR[tr.tag] * atr
    stop = abs(tr.entry - tr.stop) if tr.stop == tr.stop else math.nan
    return max(stop, MIN_RISK_ATR * atr)


def simulate(day_events, rules: PropRules, risk_pct: float, prices) -> PropAccount:
    """day_events: [(date, [Event...]), ...] in date order; prices(date, sym, T) -> last close before minute T."""
    acc = PropAccount(rules, risk_pct)
    for d, events in day_events:
        acc.start_day(d)
        for ev in events:
            px = (lambda sym, T, _d=d: prices(_d, sym, T))
            if ev.kind == "entry":
                acc.on_entry(d, ev.T, ev.trade)
            else:
                acc.on_exit(d, ev.T, ev.trade, px)
        acc.open.clear()                    # every component is flat by 16:00 (safety)
    return acc
