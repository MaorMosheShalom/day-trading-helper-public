# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Topstep (ProjectX Gateway API) connection: login, 1-minute bars, orders - for the DEMO (practice account) first.

API docs: https://gateway.docs.projectx.com (read 2026-11-15). Base https://api.topstepx.com.
  POST /api/Auth/loginKey {userName, apiKey} -> token (24 h);  every call: Authorization: Bearer <token>
  POST /api/Account/search {onlyActiveAccounts}           -> accounts [{id, name, balance, canTrade}]
  POST /api/Contract/search {searchText, live}            -> contracts [{id, symbolId, tickSize, tickValue, activeContract}]
  POST /api/History/retrieveBars {contractId, live, startTime, endTime, unit 2=minute, unitNumber 1, limit,
                                  includePartialBar}      -> bars [{t, o, h, l, c, v}]   (max 20,000 bars, 50 calls / 30 s)
  POST /api/Order/place {accountId, contractId, type 2=market 4=stop, side 0=buy 1=sell, size, stopPrice}
  POST /api/Order/searchOpen {accountId} / Order/search {accountId, startTimestamp} / Order/cancel {accountId, orderId}
  POST /api/Position/searchOpen {accountId} / Position/closeContract {accountId, contractId}
Rules (Topstep help center): the bot must run on YOUR OWN PC (no VPS / VPN / remote server); Live Funded accounts may
not trade through the API; no high-frequency trading. API access $29/month ($14.50 with code "topstep").

Credentials come from settings_local.txt in the project folder (never committed):
  PX_USER=<your TopstepX user name (not the e-mail)>
  PX_KEY=<the API key from TopstepX Settings -> API>
  PX_ACCOUNT=<the exact account name to trade, e.g. the practice account>

ProjectXBroker sends the trader's NET position to the account (market orders) and keeps a protective STOP order at
the broker for every open trade, so a crash or a lost connection never leaves a position without a stop.
"""
from __future__ import annotations

import csv
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .. import data
from .broker import Broker

ROOT = Path(__file__).resolve().parent.parent.parent
BASE = "https://api.topstepx.com"
SETTINGS = ROOT / "settings_local.txt"
# contracts: the bars come from the FULL-SIZE contracts (same prices and volume as the research data), the orders go
# to the MICROS (MNQ / MES: same price, 1/10 of the money)
DATA_SYMBOL = {"ES": "F.US.EP", "NQ": "F.US.ENQ"}
TRADE_SYMBOL = {"ES": "F.US.MES", "NQ": "F.US.MNQ"}
SEARCH_TEXT = {"F.US.EP": "ES", "F.US.ENQ": "NQ", "F.US.MES": "MES", "F.US.MNQ": "MNQ"}
MARKET, STOP = 2, 4
BUY, SELL = 0, 1


def settings(path: Path = SETTINGS) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


class ApiError(RuntimeError):
    pass


class ProjectX:
    """Thin client for the ProjectX Gateway API (REST only; bars are polled once a minute)."""

    def __init__(self, user: str, key: str, base: str = BASE, session=None):
        import requests
        self.user, self.key, self.base = user, key, base.rstrip("/")
        self.s = session or requests.Session()
        self.token, self.token_time = None, 0.0
        self._contracts = {}

    @classmethod
    def from_settings(cls, path: Path = SETTINGS) -> "ProjectX":
        cfg = settings(path)
        if not cfg.get("PX_USER") or not cfg.get("PX_KEY"):
            raise SystemExit(f"Missing PX_USER / PX_KEY in {path}. Run scripts\\projectx_setup.bat first.")
        return cls(cfg["PX_USER"], cfg["PX_KEY"])

    # ------------------------------------------------------------------ plumbing
    def login(self):
        r = self.s.post(f"{self.base}/api/Auth/loginKey", json={"userName": self.user, "apiKey": self.key}, timeout=20)
        j = r.json()
        if not j.get("success") or not j.get("token"):
            raise ApiError(f"login failed (errorCode {j.get('errorCode')}, {j.get('errorMessage')}): check PX_USER is "
                           f"your TopstepX user name (not the e-mail) and PX_KEY is the API key")
        self.token, self.token_time = j["token"], time.time()

    def call(self, path: str, body: dict, tries: int = 3) -> dict:
        for i in range(tries):
            if self.token is None or time.time() - self.token_time > 20 * 3600:
                self.login()
            try:
                r = self.s.post(f"{self.base}/api/{path}", json=body, timeout=20,
                                headers={"Authorization": f"Bearer {self.token}"})
            except Exception as e:                       # network hiccup: retry
                if i == tries - 1:
                    raise ApiError(f"{path}: {type(e).__name__} {e}")
                time.sleep(2)
                continue
            if r.status_code == 401:
                self.token = None
                continue
            if r.status_code == 429:                     # rate limit: back off
                time.sleep(5 * (i + 1))
                continue
            j = r.json()
            if not j.get("success", False):
                raise ApiError(f"{path} failed: errorCode {j.get('errorCode')} {j.get('errorMessage')}")
            return j
        raise ApiError(f"{path}: no answer after {tries} tries")

    # ------------------------------------------------------------------ account / contracts
    def accounts(self) -> list[dict]:
        return self.call("Account/search", {"onlyActiveAccounts": True}).get("accounts", [])

    def account(self, name: str) -> dict:
        for a in self.accounts():
            if a.get("name", "").strip().lower() == name.strip().lower():
                return a
        raise ApiError(f"account {name!r} not found. Your accounts: {[a.get('name') for a in self.accounts()]}")

    def contract(self, symbol_id: str) -> dict:
        """The active (front) contract of e.g. F.US.MNQ: {id, tickSize, tickValue, ...}."""
        if symbol_id not in self._contracts:
            cs = self.call("Contract/search", {"searchText": SEARCH_TEXT[symbol_id], "live": False}).get("contracts", [])
            cs = [c for c in cs if c.get("symbolId") == symbol_id]
            act = [c for c in cs if c.get("activeContract")] or cs
            if not act:
                raise ApiError(f"no contract found for {symbol_id}")
            self._contracts[symbol_id] = act[0]
        return self._contracts[symbol_id]

    # ------------------------------------------------------------------ data
    def bars(self, contract_id: str, start: datetime, end: datetime) -> pd.DataFrame:
        """Closed 1-minute bars [start, end), indexed by New York time (bar START time, like the research data)."""
        out, s = [], start
        while s < end:
            e = min(s + timedelta(days=10), end)
            j = self.call("History/retrieveBars", {"contractId": contract_id, "live": False,
                                                   "startTime": s.astimezone(timezone.utc).isoformat(),
                                                   "endTime": e.astimezone(timezone.utc).isoformat(), "unit": 2,
                                                   "unitNumber": 1, "limit": 20000, "includePartialBar": False})
            out += j.get("bars", [])
            s = e
        if not out:
            return pd.DataFrame(columns=data.COLUMNS)
        df = pd.DataFrame(out).rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df.index = pd.to_datetime(df.pop("t"), utc=True).dt.tz_convert(data.NY)
        df = df[~df.index.duplicated(keep="last")].sort_index()
        return df[data.COLUMNS].astype(float)

    def collect(self, days_back: float | None = None, verbose: bool = False) -> dict:
        """Add the newest ES / NQ 1-minute bars to the local store (the same store the research and paper tool use).
        days_back=None: since the last stored bar (at most 30 days)."""
        now = datetime.now(timezone.utc)
        added = {}
        for sym, sid in DATA_SYMBOL.items():
            if days_back is None:
                old = data.load(sym, "1m")
                last = old.index[-1].tz_convert("UTC").to_pydatetime() if len(old) else now - timedelta(days=30)
                start = max(last - timedelta(minutes=30), now - timedelta(days=30))
            else:
                start = now - timedelta(days=days_back)
            df = self.bars(self.contract(sid)["id"], start, now)
            added[sym] = data._merge_save(sym, "1m", df) if len(df) else 0
            if verbose:
                print(f"{sym}: {len(df)} bars from Topstep ({self.contract(sid)['id']}), +{added[sym]} new")
        return added

    # ------------------------------------------------------------------ orders / positions
    def place(self, account_id: int, contract_id: str, side: int, size: int, otype: int = MARKET,
              stop_price: float | None = None, tag: str | None = None) -> int:
        body = {"accountId": account_id, "contractId": contract_id, "type": otype, "side": side, "size": int(size),
                "limitPrice": None, "stopPrice": stop_price, "trailPrice": None, "customTag": tag,
                "stopLossBracket": None, "takeProfitBracket": None}
        return int(self.call("Order/place", body)["orderId"])

    def open_orders(self, account_id: int) -> list[dict]:
        return self.call("Order/searchOpen", {"accountId": account_id}).get("orders", [])

    def orders_since(self, account_id: int, since: datetime) -> list[dict]:
        return self.call("Order/search", {"accountId": account_id,
                                          "startTimestamp": since.astimezone(timezone.utc).isoformat()}).get("orders", [])

    def cancel(self, account_id: int, order_id: int):
        self.call("Order/cancel", {"accountId": account_id, "orderId": int(order_id)})

    def positions(self, account_id: int) -> list[dict]:
        return self.call("Position/searchOpen", {"accountId": account_id}).get("positions", [])

    def close(self, account_id: int, contract_id: str):
        self.call("Position/closeContract", {"accountId": account_id, "contractId": contract_id})


def round_stop(price: float, tick: float, side_of_position: int) -> float:
    """A protective stop on the tick grid, never closer to the market than the planned stop:
    long position -> sell stop rounded DOWN, short -> buy stop rounded UP."""
    n = price / tick
    n = math.floor(n + 1e-9) if side_of_position > 0 else math.ceil(n - 1e-9)
    return round(n * tick, 6)


class ProjectXBroker(Broker):
    """Real orders on one ProjectX account. The trader asks for a NET position per symbol; this:
      - flat -> position: MARKET order, then a protective STOP order at the trade's stop (rounded away from price);
      - position -> flat: cancel this contract's open orders, close the position;
      - safety: never more than max_micros; positions it didn't ask for are closed (reconcile);
    Every order is written to the orders CSV with the broker's fill price, to compare with the backtest."""

    def __init__(self, px: ProjectX, account_name: str, log_path: Path, max_micros: int = 20,
                 positions: dict | None = None):
        self.px, self.log_path, self.max_micros = px, Path(log_path), int(max_micros)
        self.acct = px.account(account_name)
        self.account_id = int(self.acct["id"])
        if not self.acct.get("canTrade", True):
            raise ApiError(f"account {account_name} can't trade right now (canTrade=false)")
        self.positions = dict(positions or {})
        self.stop_orders: dict[str, int] = {}
        self.stopped: dict[str, int] = {}          # sym -> position the broker stop closed (trader still holds it)
        self.notes: list[str] = []

    def _cid(self, sym: str) -> str:
        return self.px.contract(TRADE_SYMBOL[sym])["id"]

    def real_position(self, sym: str) -> int:
        cid = self._cid(sym)
        net = 0
        for p in self.px.positions(self.account_id):
            if p.get("contractId") == cid:
                # ProjectX position type: 1 = long, 2 = short
                net += int(p.get("size", 0)) * (1 if int(p.get("type", 1)) == 1 else -1)
        return net

    def position(self, sym: str) -> int:
        return self.positions.get(sym, 0)

    def _log(self, when, sym, action, qty, target, why, fill=None, order_id=None, note=""):
        new = not self.log_path.exists()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "contract", "action", "qty", "net after", "why", "fill price", "order id",
                            "sent at (your PC)", "note"])
            w.writerow([when, TRADE_SYMBOL[sym].split(".")[-1], action, qty, target, why,
                        "" if fill is None else fill, order_id or "", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), note])

    def _fill_price(self, order_id: int, since: datetime, wait: float = 10.0):
        t0 = time.time()
        while time.time() - t0 < wait:
            for o in self.px.orders_since(self.account_id, since):
                if int(o.get("id", -1)) == order_id and o.get("filledPrice") is not None:
                    return float(o["filledPrice"])
            time.sleep(1)
        return None

    def _cancel_all(self, sym: str):
        cid = self._cid(sym)
        for o in self.px.open_orders(self.account_id):
            if o.get("contractId") == cid:
                try:
                    self.px.cancel(self.account_id, int(o["id"]))
                except ApiError as e:
                    self.notes.append(f"cancel {o.get('id')}: {e}")
        self.stop_orders.pop(sym, None)

    def flatten(self, sym: str, when: str, why: str):
        self._cancel_all(sym)
        real = self.real_position(sym)
        if real != 0:
            self.px.close(self.account_id, self._cid(sym))
            self._log(when, sym, "CLOSE", abs(real), 0, why)
        self.positions[sym] = 0

    def set_position(self, sym: str, target: int, when: str, why: str, stop: float | None = None) -> None:
        self.stopped.pop(sym, None)
        if abs(target) > self.max_micros:
            self.notes.append(f"{when} {sym}: asked for {target}, capped at {self.max_micros}")
            target = int(math.copysign(self.max_micros, target))
        if target == 0:
            self.flatten(sym, when, why)
            return
        real = self.real_position(sym)
        if real != 0 and (real > 0) != (target > 0):           # reversing: go flat first
            self.flatten(sym, when, why + " (reverse)")
            real = 0
        qty = target - real
        if qty != 0:
            since = datetime.now(timezone.utc) - timedelta(seconds=5)
            oid = self.px.place(self.account_id, self._cid(sym), BUY if qty > 0 else SELL, abs(qty), MARKET)
            fill = self._fill_price(oid, since)
            self._log(when, sym, "BUY" if qty > 0 else "SELL", abs(qty), target, why, fill, oid)
        self.positions[sym] = target
        # protective stop for the whole position (replaces the old one)
        if stop is not None and stop == stop:
            old = self.stop_orders.pop(sym, None)
            if old is not None:
                try:
                    self.px.cancel(self.account_id, old)
                except ApiError:
                    pass
            side_pos = 1 if target > 0 else -1
            px = round_stop(stop, float(self.px.contract(TRADE_SYMBOL[sym])["tickSize"]), side_pos)
            try:
                sid = self.px.place(self.account_id, self._cid(sym), SELL if side_pos > 0 else BUY, abs(target), STOP,
                                    stop_price=px)
                self.stop_orders[sym] = sid
                self._log(when, sym, "STOP ORDER", abs(target), target, why, px, sid, "protective stop at the broker")
            except ApiError as e:                    # the market is already past the stop: the trade is stopped out
                self._log(when, sym, "STOP REJECTED", abs(target), target, why, px, None, str(e)[:120])
                self.flatten(sym, when, "stop already passed - closed")

    def reconcile(self, sym: str, when: str) -> str | None:
        """Compare the account with what the trader thinks. Returns a note when they differ.
        - account has a position the trader doesn't want -> close it (safety);
        - the trader holds a position but the account is flat -> the broker stop was hit (normal, noted)."""
        real, want = self.real_position(sym), self.positions.get(sym, 0)
        if real == want or (real == 0 and self.stopped.get(sym) == want):
            return None
        if want == 0:
            self.flatten(sym, when, "safety: unexpected position closed")
            return f"{when} {sym}: account had {real}, trader wanted 0 -> closed"
        if real == 0:
            # keep positions[sym] = want, so the trader does NOT re-enter; its own exit (target 0) clears this
            self._cancel_all(sym)
            self.stopped[sym] = want
            self._log(when, sym, "STOPPED OUT", abs(want), 0, "the protective stop at the broker filled")
            return f"{when} {sym}: broker stop filled ({want} -> 0)"
        return f"{when} {sym}: account {real} vs trader {want} (left as is)"
