# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Topstep (ProjectX) broker logic against a FAKE account - no internet, no credentials."""
from datetime import date

import pytest

from dth.live import projectx as P
from dth.live import runner


class FakePX:
    """Minimal stand-in for P.ProjectX: market orders fill at `price`, stop orders stay open."""

    def __init__(self, price=20000.0):
        self.price, self.next_id = price, 100
        self.pos = {}                      # contractId -> signed size
        self.orders = []                   # open orders
        self.filled = []
        self.placed = []

    def account(self, name):
        return {"id": 7, "name": name, "balance": 150000.0, "canTrade": True}

    def contract(self, sid):
        return {"id": f"CON.{sid}.Z26", "tickSize": 0.25, "tickValue": 0.5}

    def place(self, account_id, cid, side, size, otype=P.MARKET, stop_price=None, tag=None):
        self.next_id += 1
        o = {"id": self.next_id, "contractId": cid, "side": side, "size": size, "type": otype, "stopPrice": stop_price}
        self.placed.append(o)
        if otype == P.MARKET:
            self.pos[cid] = self.pos.get(cid, 0) + (size if side == P.BUY else -size)
            self.filled.append({**o, "filledPrice": self.price})
        else:
            self.orders.append(o)
        return self.next_id

    def orders_since(self, account_id, since):
        return self.filled

    def open_orders(self, account_id):
        return list(self.orders)

    def cancel(self, account_id, oid):
        self.orders = [o for o in self.orders if o["id"] != oid]

    def positions(self, account_id):
        return [{"contractId": c, "size": abs(n), "type": 1 if n > 0 else 2} for c, n in self.pos.items() if n]

    def close(self, account_id, cid):
        self.pos[cid] = 0

    def hit_stop(self):
        """The broker's stop order fills."""
        for o in self.orders:
            if o["type"] == P.STOP:
                self.pos[o["contractId"]] = 0
        self.orders = []


def broker(tmp_path, **kw):
    px = FakePX()
    return px, P.ProjectXBroker(px, "PRAC-TEST", tmp_path / "orders.csv", **kw)


def test_entry_sends_market_order_and_protective_stop_then_exit_cleans_up(tmp_path):
    px, br = broker(tmp_path)
    br.set_position("NQ", 3, "t1", "orb5_concretum", stop=19950.3)
    assert br.real_position("NQ") == 3
    stops = [o for o in px.orders if o["type"] == P.STOP]
    assert len(stops) == 1 and stops[0]["side"] == P.SELL and stops[0]["size"] == 3
    assert stops[0]["stopPrice"] == 19950.25                   # long: rounded DOWN to the tick (never tighter)
    br.set_position("NQ", 0, "t2", "flat")
    assert br.real_position("NQ") == 0 and px.orders == []
    assert "MNQ" in (tmp_path / "orders.csv").read_text(encoding="utf-8")


def test_short_stop_rounds_up_and_size_is_capped(tmp_path):
    px, br = broker(tmp_path, max_micros=2)
    br.set_position("NQ", -5, "t1", "orb5_concretum", stop=20050.1)
    assert br.real_position("NQ") == -2
    assert px.orders[0]["side"] == P.BUY and px.orders[0]["stopPrice"] == 20050.25


def test_broker_stop_fill_does_not_re_enter(tmp_path):
    px, br = broker(tmp_path)
    br.set_position("NQ", 2, "t1", "x", stop=19900)
    px.hit_stop()
    assert br.reconcile("NQ", "t2") is not None           # noted once
    assert br.position("NQ") == 2                        # the trader still holds it -> no new order is sent
    n = len(px.placed)
    assert br.reconcile("NQ", "t3") is None and len(px.placed) == n
    br.set_position("NQ", 0, "t4", "exit")                # the trader's own exit: nothing left to close
    assert br.real_position("NQ") == 0 and br.position("NQ") == 0


def test_unexpected_position_is_closed(tmp_path):
    px, br = broker(tmp_path)
    px.pos[px.contract("F.US.MNQ")["id"]] = 4             # e.g. a manual click in the platform
    assert br.reconcile("NQ", "t1") is not None
    assert br.real_position("NQ") == 0


def test_round_stop():
    assert P.round_stop(100.1, 0.25, +1) == 100.0
    assert P.round_stop(100.1, 0.25, -1) == 100.25
    assert P.round_stop(100.25, 0.25, +1) == 100.25


def test_protective_stop_for_trades_without_a_hard_stop():
    from dth.live.account import Position
    p = Position(("NQ", "gao_momentum", 600, 1), "NQ", 1, 20000.0, 3, "gao_momentum", float("nan"), 200.0)
    assert runner.protective_stop([p]) == 20000.0 - runner.CATASTROPHE_STOP_ATR * 200.0
    p2 = Position(("NQ", "orb5_concretum", 600, -1), "NQ", -1, 20000.0, 3, "orb5_concretum", 20040.0, 200.0)
    assert runner.protective_stop([p2]) == 20040.0


def test_a_stored_day_through_the_broker_matches_the_paper_trader(tmp_path):
    """The live plan on a stored day: every entry becomes one market order + one stop, the account ends flat,
    and the trader's events equal the paper run."""
    from dth.lab import evaluate, system
    from dth.lab.days import RTH_CLOSE
    pairs = {es.d: (es, nq) for es, nq in evaluate.load_pairs()}
    d = sorted(x for x, (e, n) in pairs.items() if not e.no_trade)[5]
    es, nq = pairs[d]
    paper = runner.PaperTrader(tmp_path / "p.pkl", tmp_path / "p.csv", plan_name=system.LIVE_PLAN)
    want = paper.run_until(d, RTH_CLOSE, {"ES": es, "NQ": nq})
    px, br = broker(tmp_path)
    tr = runner.PaperTrader(tmp_path / "b.pkl", tmp_path / "b.csv", plan_name=system.LIVE_PLAN)
    tr.broker = br
    got = tr.run_until(d, RTH_CLOSE, {"ES": es, "NQ": nq})
    f = lambda evs: [(e.kind, e.sym, e.trade.tag, e.T) for e in evs]
    assert f(got) == f(want)
    entries = [e for e in got if e.kind == "entry" and e.trade.tag in system.plan_spec(system.LIVE_PLAN)[1]]
    markets = [o for o in px.placed if o["type"] == P.MARKET]
    stops = [o for o in px.placed if o["type"] == P.STOP]
    taken = len(tr.state.acc.trades)
    # entry = 1 market order + 1 stop order; exit = cancel the stop + close the position
    assert len(stops) == taken == len(markets) and 0 < taken <= len(entries)
    assert br.real_position("NQ") == 0 and px.orders == []
