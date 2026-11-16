# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Check the Topstep (ProjectX) connection before the demo - nothing is traded unless you add --test-order.

  python scripts/projectx_check.py               # login, your accounts, the contracts, live bars (how fresh they are)
  python scripts/projectx_check.py --test-order  # on the PRACTICE account only: buy 1 MNQ, put a stop, close it again
Needs PX_USER / PX_KEY / PX_ACCOUNT in settings_local.txt (scripts\\projectx_setup.bat writes them).
"""
import argparse
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

from dth import data
from dth.live import projectx as P


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-order", action="store_true")
    a = ap.parse_args()
    cfg = P.settings()
    px = P.ProjectX.from_settings()
    print("1. Login ...", end=" ", flush=True)
    px.login()
    print("OK")

    print("2. Your accounts:")
    accs = px.accounts()
    for x in accs:
        mark = "  <- PX_ACCOUNT (the demo trades this one)" if x.get("name", "").lower() == cfg.get("PX_ACCOUNT", "").lower() else ""
        print(f"   {x.get('name'):30s} id {x.get('id')}  balance ${float(x.get('balance', 0)):>11,.2f}  "
              f"can trade: {x.get('canTrade')}{mark}")
    if not cfg.get("PX_ACCOUNT"):
        print("   PX_ACCOUNT is not set: copy the PRACTICE account's name into settings_local.txt (PX_ACCOUNT=...)")
    elif not any(x.get("name", "").lower() == cfg["PX_ACCOUNT"].lower() for x in accs):
        print(f"   !! PX_ACCOUNT={cfg['PX_ACCOUNT']} is not one of these - fix the name in settings_local.txt")

    print("3. Contracts:")
    for sid in ("F.US.ENQ", "F.US.EP", "F.US.MNQ", "F.US.MES"):
        c = px.contract(sid)
        print(f"   {sid:9s} -> {c['id']:20s} {c.get('description', '')}  tick {c.get('tickSize')} = ${c.get('tickValue')}")

    print("4. Live 1-minute bars (NQ, last 2 hours):")
    now = datetime.now(timezone.utc)
    df = px.bars(px.contract("F.US.ENQ")["id"], now - timedelta(hours=2), now)
    if df.empty:
        print("   no bars in the last 2 hours (market closed? CME is closed Fri 17:00 - Sun 18:00 NY and daily 17:00-18:00)")
    else:
        last = df.index[-1]
        age = (datetime.now(timezone.utc) - last.tz_convert("UTC").to_pydatetime()).total_seconds() - 60
        print(f"   {len(df)} bars, last bar {last:%Y-%m-%d %H:%M} NY close {df.close.iloc[-1]:.2f}; it closed "
              f"{max(age, 0):.0f} s ago {'(REAL-TIME)' if age < 90 else '(market closed or delayed)'}")
        old = data.load("NQ", "1m")
        both = df.join(old[["close"]].rename(columns={"close": "stored"}), how="inner")
        if len(both):
            diff = (both.close - both.stored).abs()
            print(f"   same minutes in your stored data: {len(both)}, close differs by {diff.mean():.2f} pts on average "
                  f"(stored data is Yahoo; small differences are normal, e.g. after a contract roll)")

    if not a.test_order:
        print("\nAll good. Next: the test order (practice account only):  python scripts\\projectx_check.py --test-order")
        return
    name = cfg.get("PX_ACCOUNT", "")
    if "prac" not in name.lower():
        sys.exit(f"--test-order only runs on a PRACTICE account (PX_ACCOUNT={name!r} doesn't contain 'PRAC').")
    log = Path(__file__).resolve().parent.parent / "reports" / "demo_topstep_test_orders.csv"
    br = P.ProjectXBroker(px, name, log, max_micros=1)
    print(f"5. TEST ORDER on {name}: buy 1 MNQ at market, stop 100 points below, then close.")
    if br.real_position("NQ") != 0:
        sys.exit("   the account already has an MNQ position - close it first (or: python scripts\\autotrade.py --flatten)")
    when = datetime.now().strftime("%Y-%m-%d %H:%M local")
    last = px.bars(px.contract("F.US.MNQ")["id"], datetime.now(timezone.utc) - timedelta(minutes=30),
                   datetime.now(timezone.utc))
    if last.empty:
        sys.exit("   no MNQ prices right now (market closed) - run the test when the market is open")
    br.set_position("NQ", 1, when, "connection test", stop=float(last.close.iloc[-1]) - 100)
    time.sleep(3)
    pos, orders = br.real_position("NQ"), [o for o in px.open_orders(br.account_id)]
    print(f"   position now {pos} MNQ, open orders: {[(o.get('type'), o.get('side'), o.get('stopPrice')) for o in orders]}")
    br.set_position("NQ", 0, when, "connection test - close")
    time.sleep(2)
    pos, orders = br.real_position("NQ"), px.open_orders(br.account_id)
    print(f"   after closing: position {pos}, open orders {len(orders)}")
    print(f"   orders and fill prices: {log}")
    print("   TEST " + ("PASSED" if pos == 0 and not orders else "NOT CLEAN - check the account in TopstepX"))


if __name__ == "__main__":
    main()
