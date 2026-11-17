# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Automatic trading of the live plan (dth/lab/system.LIVE_PLAN) on a simulated prop-firm evaluation account,
minute by minute, on exactly the code path of the backtest. Every other registered plan runs next to it on paper, in
its own account, for comparison. One trade at a time; risk per trade = account.risk_for(plan).

  python scripts/autotrade.py                          # trade today: waits for 09:30 NY, steps every closed minute
  python scripts/autotrade.py --replay-day 2025-03-20  # self-check: a stored day through the live trader == replay
  python scripts/autotrade.py --status                 # the paper accounts
  python scripts/autotrade.py --catch-up               # finish unfinished / missed days from the stored bars
Options: --fresh (new paper accounts), --risk 2 (a fixed percent), --no-shadow, --plan NAME (with --replay-day),
         --log FILE (the hidden scheduled run has no console).

Resilience: state is saved every minute; a restart resumes and steps the missed minutes; a day that was not finished
(stopped, PC asleep) is finished from the stored bars - identical to a live run, because strategies only ever see
closed bars. The scheduler (scripts/install_autotrade_task.ps1) restarts the process every 10 minutes if it is not
running; a per-day stop flag (scripts/demo_control.ps1 stop) keeps it stopped. After every trade event the live
picture book is redrawn (scripts/trade_book.py live_update): an OPEN picture at the entry, WIN / LOSS at the exit.

BROKER (Topstep, ProjectX Gateway API): --broker topstep sends the live plan's orders to a real (practice) account
(dth/live/projectx.py), with the broker's real-time bars. Setup: scripts/projectx_setup.bat, check:
python scripts/projectx_check.py.
  python scripts/autotrade.py --broker topstep        # trade the account named PX_ACCOUNT in settings_local.txt
  python scripts/autotrade.py --flatten               # close everything on that account now
  a file STOP_TRADING.txt in the project folder       # kill switch: the running trader closes everything and stops
Safety: only an account whose name contains "PRAC" unless --allow-eval; at most PX_MAX_MICROS (default 20) micros;
a protective STOP order at the broker for every trade; flat by 15:57 NY; stop for the day after PX_MAX_DAY_LOSS
(default $900) account loss; positions the trader didn't ask for are closed.
"""
import argparse
import json
import sys
import tempfile
import time
import warnings
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

from dth import data
from dth.lab import daily, evaluate, system
from dth.lab.days import RTH_CLOSE, RTH_OPEN, build_live
from dth.live import account, engine, runner

OUT_DIR = Path(__file__).resolve().parent.parent / "reports"

# one paper account per registered plan (dth/lab/system.PLAN_NAMES): the live plan + the others for comparison
FILES = {n: (OUT_DIR / f"paper_{n}_state.pkl", OUT_DIR / f"paper_{n}_orders.csv") for n in system.PLAN_NAMES}
LIVE = system.LIVE_PLAN                           # the plan the tool trades
DEMO = (OUT_DIR / "demo_topstep_state.pkl", OUT_DIR / "demo_topstep_orders.csv")
STOP_FILE = Path(__file__).resolve().parent.parent / "STOP_TRADING.txt"
SHADOWS = {n: f for n, f in FILES.items() if n != LIVE}


def replay_day_check(d: date, risk, plan: bool, plan_name: str = LIVE) -> bool:
    pairs = {es.d: (es, nq) for es, nq in evaluate.load_pairs()}
    if d not in pairs:
        sys.exit(f"{d} is not a stored trading day")
    es, nq = pairs[d]
    with tempfile.TemporaryDirectory() as tmp:
        tr = runner.PaperTrader(Path(tmp) / "s.pkl", Path(tmp) / "o.csv", risk, plan=plan, plan_name=plan_name)
        got = tr.run_until(d, RTH_CLOSE, {"ES": es, "NQ": nq})
        orders = (Path(tmp) / "o.csv").read_text(encoding="utf-8") if (Path(tmp) / "o.csv").exists() else ""
    sig = daily.signals()
    want, _ = engine.replay_day(es, nq, sig.loc[d] if d in sig.index else None, plan_name)
    if plan:
        want = engine.plan_events(want, plan_name)
    f = lambda evs: [(e.kind, e.sym, e.trade.tag, e.T, round(e.trade.pts, 4)) for e in evs]
    ok = f(got) == f(want)
    print(f"{d} [{plan_name}]: paper trader {len(got)} events, replay {len(want)} events -> {'IDENTICAL' if ok else 'DIFFERENT'}")
    print(orders or "(no orders)")
    return ok


def keep_awake():
    """Windows: don't let the PC go to sleep while this process runs (released automatically when it ends)."""
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)    # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    except Exception:
        pass


def stop_flag(d) -> Path:
    """Button '3 Stop the demo now' writes this for today (NY date): the demo doesn't (re)start today."""
    return OUT_DIR / f"demo_stopped_{d}.flag"


def _hm(T) -> str:
    return f"{int(T) // 60:02d}:{int(T) % 60:02d}"


def _print_events(label, evs):
    for ev in evs:
        tr_ = ev.trade
        print(f"{_stamp(ev.d, ev.T)} {label}{ev.kind.upper():5s} {ev.sym} {tr_.tag} {'LONG' if tr_.side > 0 else 'SHORT'}"
              f" entry {tr_.entry:.2f}" + (f" -> exit {tr_.exit:.2f} ({tr_.reason}, {tr_.pts:+.2f} pts)" if ev.kind == "exit" else ""),
              flush=True)


def _pictures(traders: dict, days=None, backfill: bool = False):
    """The LIVE picture book (scripts/trade_book.py live_update) - never allowed to stop the trading. backfill=True
    (once at every start) also draws trades closed by an older version that didn't keep the picture details."""
    try:
        import trade_book
        trade_book.live_update(traders, days, backfill=backfill)
    except Exception as e:
        print(f"  (pictures failed: {type(e).__name__}: {e})", flush=True)


def catch_up(tr, label: str, d: date, now: int, trade_dates: list) -> list:
    """Finish days the demo didn't finish (stopped / PC off), from the stored 1-minute bars - exactly what the live run
    would have done (the engine only uses closed candles): the saved day if it stopped early, then every missed
    trading day after it (up to yesterday), and today once the market has closed."""
    st = tr.state
    todo = []
    if st.day is not None and st.last_T < RTH_CLOSE and (st.day < d or now > RTH_CLOSE + 1):
        todo.append(st.day)
    if st.day is not None:
        todo += [x for x in trade_dates if st.day < x < d and x.weekday() < 5][-30:]
    if now > RTH_CLOSE + 1 and d.weekday() < 5 and (st.day is None or st.day < d or st.last_T < RTH_CLOSE) and d not in todo:
        todo.append(d)
    evs = []
    for x in todo:
        days = {s: build_live(s, x) for s in ("ES", "NQ")}
        if any(v is None or not len(v.rth) for v in days.values()):
            print(f"{label}: no 1-minute data for {x} - can't finish it", flush=True)
            continue
        was = _hm(st.last_T) if st.day == x else None
        print(f"{label}: {'finishing' if was else 'catching up'} {x}" + (f" (it stopped at {was} NY)" if was else "")
              + " from the stored 1-minute bars", flush=True)
        evs += tr.run_until(x, RTH_CLOSE, days)
    return evs


def catch_up_all(traders: dict, main_plan: str, d: date, now: int):
    """catch_up() for every paper case, then redraw the LIVE picture book (draws anything missing)."""
    try:
        nq = data.load("NQ", "1m")
        tds = sorted(set(data.trade_date(nq.index)))
    except Exception:
        tds = []
    for n, t in traders.items():
        try:
            _print_events(f"[{system.PLAN_NAMES[n]}] ", catch_up(t, system.PLAN_NAMES[n], d, now, tds))
        except Exception as e:
            print(f"[{n}] catch-up error {type(e).__name__}: {e}")
            if n == main_plan:
                raise
    for t in traders.values():
        t.save()
    _pictures(traders, backfill=True)


def catch_up_only():
    """--catch-up: finish unfinished / missed demo days from the stored bars and draw their pictures, then exit."""
    d, now = runner.now_minute_ny()
    traders = {n: runner.PaperTrader(st, od, None, plan_name=n) for n, (st, od) in FILES.items() if st.exists()}
    if not traders:
        print("No demo paper case yet - nothing to catch up.")
        return
    try:
        data.collect(verbose=False)
    except Exception as e:
        print(f"data update failed ({type(e).__name__}), using the stored data")
    catch_up_all(traders, LIVE, d, now)
    print("Caught up. Pictures: reports\\trade_book\\LIVE\\index.html")


def live(risk, plan: bool, shadow: bool = True):
    keep_awake()
    main_plan = LIVE
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "live_plan.txt").write_text(main_plan, encoding="utf-8")   # read by scripts/demo_control.ps1
    d, now = runner.now_minute_ny()
    from datetime import datetime
    if stop_flag(d).exists():
        print(f"{datetime.now():%Y-%m-%d %H:%M} demo start skipped: you stopped it for today ('3 Stop the demo now').")
        return
    tr = runner.PaperTrader(*FILES[main_plan], risk, plan=plan, plan_name=main_plan)
    shs = {n: runner.PaperTrader(st, od, None, plan_name=n) for n, (st, od) in FILES.items() if n != main_plan} if shadow else {}
    traders = {main_plan: tr, **shs}
    closed_now = d.weekday() >= 5 or now > RTH_CLOSE + 1
    finished = all(t.state.day is not None and t.state.last_T >= RTH_CLOSE and (t.state.day == d or d.weekday() >= 5)
                   for t in traders.values())
    if closed_now and finished:
        return                                       # the 10-minute restart after the session: nothing to do, stay quiet
    print(f"===== {datetime.now():%Y-%m-%d %H:%M} demo started (New York {d} {_hm(now)}) =====", flush=True)
    try:
        data.collect(verbose=True)                   # once: everything Yahoo still has (fills missed days)
    except Exception as e:
        print(f"first data update failed ({type(e).__name__}), continuing")
    catch_up_all(traders, main_plan, d, now)
    if closed_now:
        for t in traders.values():
            t.save()
        return
    acc = tr.state.acc
    print(f"Paper trading {d}: {system.PLAN_NAMES[main_plan] + ' (one trade at a time)' if tr.state.gate is not None else 'old stacked system'}, "
          f"risk {account.risk_text(acc.risk_pct)}. Case {acc.case_no} ({acc.phase}, balance ${acc.balance:,.0f}).", flush=True)
    last_beat, stopped = None, False
    try:
        while True:
            d2, now = runner.now_minute_ny()
            if d2 != d or now > RTH_CLOSE + 1:
                break
            if stop_flag(d).exists():
                print(f"{_stamp(d, now)} stopped by you ('3 Stop the demo now').", flush=True)
                stopped = True
                break
            if now <= RTH_OPEN:
                time.sleep(30)
                continue
            try:
                data.collect_recent()                # each minute: only the last hours (one small request)
            except Exception as e:                   # keep running on network problems
                print(f"data update failed ({type(e).__name__}), retrying next minute")
            days = {s: build_live(s, d) for s in ("ES", "NQ")}
            if any(v is None or not len(v.rth) for v in days.values()):
                time.sleep(60)
                continue
            if days["ES"].no_trade:
                print(f"No-trade day ({days['ES'].no_trade}).", flush=True)
                for t in traders.values():
                    t.run_until(d, RTH_CLOSE, days)  # marks the day done
                break
            avail = int(min(v.rth.t[-1] for v in days.values()))   # only step minutes whose bars have arrived
            any_ev = False
            for n, t in traders.items():
                try:
                    evs = t.run_until(d, min(now, avail + 1), days)
                except Exception as e:
                    if n == main_plan:
                        raise
                    print(f"  [{n.upper()} shadow] error {type(e).__name__}: {e}")   # a shadow never stops the plan
                    continue
                _print_events("" if n == main_plan else f"  [{system.PLAN_NAMES[n]} shadow] ", evs)
                any_ev = any_ev or bool(evs)
            if any_ev:
                _pictures(traders, days)
            if last_beat is None or now - last_beat >= 30:
                last_beat = now
                op = ", ".join(f"{p.tag} {'LONG' if p.side > 0 else 'SHORT'} {p.micros}" for p in tr.state.acc.open.values()) or "none"
                print(f"{_stamp(d, now)} alive - bars up to {_hm(avail)} NY, case balance ${tr.state.acc.balance:,.0f}, "
                      f"open: {op}", flush=True)
            time.sleep(max(1.0, 62 - time.localtime().tm_sec))
    except KeyboardInterrupt:
        print(f"{_stamp(d, runner.now_minute_ny()[1])} stopped from outside (window closed / Ctrl+C) - it restarts "
              f"by itself within 10 minutes and catches up.", flush=True)
        stopped = True
    finally:
        for t in traders.values():
            t.save()
    if stopped:
        return
    # after the close: wait for the last bars (the free data is a few minutes late), then finish the day exactly
    for _ in range(25):
        try:
            data.collect_recent()
        except Exception:
            pass
        nqd = build_live("NQ", d)
        if nqd is not None and len(nqd.rth) and nqd.rth.t[-1] >= RTH_CLOSE - 1:
            break
        time.sleep(60)
    days = {s: build_live(s, d) for s in ("ES", "NQ")}
    if not any(v is None or not len(v.rth) for v in days.values()):
        for n, t in traders.items():
            try:
                _print_events("" if n == main_plan else f"  [{system.PLAN_NAMES[n]} shadow] ", t.run_until(d, RTH_CLOSE, days))
            except Exception as e:
                print(f"[{n}] end of day error {type(e).__name__}: {e}")
        _pictures(traders, days)
    print(f"===== {datetime.now():%Y-%m-%d %H:%M} demo day done =====", flush=True)
    status()


def _demo_broker(allow_eval: bool, positions=None):
    from dth.live import projectx
    cfg = projectx.settings()
    name = cfg.get("PX_ACCOUNT", "")
    if not name:
        sys.exit("PX_ACCOUNT is not set in settings_local.txt - run scripts\\projectx_setup.bat")
    if "prac" not in name.lower() and not allow_eval:
        sys.exit(f"Account {name!r} is not a practice account. The demo trades only a PRACTICE account; "
                 f"to use it on an evaluation (Combine) account on purpose, add --allow-eval.")
    px = projectx.ProjectX.from_settings()
    return projectx.ProjectXBroker(px, name, DEMO[1], max_micros=int(cfg.get("PX_MAX_MICROS", 20)),
                                   positions=positions), float(cfg.get("PX_MAX_DAY_LOSS", 900))


def _stamp(d, T):
    from datetime import datetime, timedelta
    return (datetime(d.year, d.month, d.day) + timedelta(minutes=T)).strftime("%Y-%m-%d %H:%M NY")


def flatten_now(allow_eval: bool):
    br, _ = _demo_broker(allow_eval)
    d, now = runner.now_minute_ny()
    for sym in ("NQ", "ES"):
        br.flatten(sym, _stamp(d, now), "--flatten (by hand)")
        print(f"{sym}: account position now {br.real_position(sym)}")


def live_demo(risk, allow_eval: bool):
    """The LIVE PLAN on a Topstep account: Topstep's bars, real orders, protective stops at the broker."""
    keep_awake()
    tr = runner.PaperTrader(*DEMO, risk, plan_name=LIVE)
    br, max_day_loss = _demo_broker(allow_eval, tr.state.positions)
    tr.broker = br
    px = br.px
    d, now = runner.now_minute_ny()
    if d.weekday() >= 5:
        print("Weekend - nothing to do.")
        return
    start_bal = float(br.acct.get("balance", 0))
    print(f"DEMO on Topstep account {br.acct.get('name')} (balance ${start_bal:,.0f}), plan {system.PLAN_NAMES[LIVE]}, "
          f"sized on the simulated 50k case: case {tr.state.acc.case_no} ({tr.state.acc.phase}, "
          f"${tr.state.acc.balance:,.0f}). Stop it any time: create {STOP_FILE.name}.")
    print("Loading the newest bars from Topstep ...")
    px.collect(verbose=True)
    for sym in ("NQ", "ES"):                         # start clean: nothing open the trader doesn't know about
        note = br.reconcile(sym, _stamp(d, now))
        if note:
            print("  " + note)
    stopped_for_day, last_bal_check = False, 0
    while True:
        d2, now = runner.now_minute_ny()
        if d2 != d or now > RTH_CLOSE + 1:
            break
        if STOP_FILE.exists():
            for sym in ("NQ", "ES"):
                br.flatten(sym, _stamp(d, now), "STOP_TRADING.txt")
            print(f"{STOP_FILE.name} found - closed everything and stopped. Delete the file to trade again.")
            break
        if now <= RTH_OPEN:
            time.sleep(20)
            continue
        if not stopped_for_day:
            try:
                px.collect()
            except Exception as e:                   # keep running on network problems (the broker stop protects)
                print(f"Topstep bars failed ({type(e).__name__}: {e}), retrying next minute")
            days = {s: build_live(s, d) for s in ("ES", "NQ")}
            if any(v is None or not len(v.rth) for v in days.values()):
                time.sleep(15)
                continue
            if days["ES"].no_trade:
                print(f"No-trade day ({days['ES'].no_trade}).")
                break
            avail = int(min(v.rth.t[-1] for v in days.values()))
            try:
                for ev in tr.run_until(d, min(now, avail + 1), days):
                    print(f"{_stamp(d, ev.T)} {ev.kind.upper():5s} {ev.sym} {ev.trade.tag} side {ev.trade.side:+d} "
                          f"entry {ev.trade.entry:.2f} stop {ev.trade.stop:.2f}")
                for sym in ("NQ", "ES"):
                    note = br.reconcile(sym, _stamp(d, now))
                    if note:
                        print("  " + note)
            except Exception as e:                   # an order problem: say it, keep going (stops are at the broker)
                print(f"ERROR {type(e).__name__}: {e}")
            if now - last_bal_check >= 5:
                last_bal_check = now
                try:
                    bal = float(px.account(br.acct["name"]).get("balance", start_bal))
                    if start_bal - bal >= max_day_loss:
                        for sym in ("NQ", "ES"):
                            br.flatten(sym, _stamp(d, now), f"daily loss ${start_bal - bal:,.0f} >= ${max_day_loss:,.0f}")
                        stopped_for_day = True
                        print(f"Account lost ${start_bal - bal:,.0f} today - everything closed, no more trades today.")
                except Exception as e:
                    print(f"balance check failed ({type(e).__name__})")
            if now >= RTH_CLOSE - 3:                 # every strategy is flat by now - make sure the account is too
                for sym in ("NQ", "ES"):
                    if br.real_position(sym) != 0:
                        br.flatten(sym, _stamp(d, now), "end of day safety close")
        time.sleep(max(1.0, 63 - time.localtime().tm_sec))       # wake 3 s after each new minute
    tr.save()
    for sym in ("NQ", "ES"):
        if br.real_position(sym) != 0:
            br.flatten(sym, _stamp(d, now), "end of session")
    print(f"Account balance now ${float(px.account(br.acct['name']).get('balance', 0)):,.0f} "
          f"(start of day ${start_bal:,.0f}). Orders: {DEMO[1]}")
    status()


def status():
    if DEMO[0].with_suffix(".json").exists():
        print("===== DEMO on Topstep (real orders on your practice account) =====")
        print(DEMO[0].with_suffix(".json").read_text(encoding="utf-8"))
    for n, (st, _) in sorted(FILES.items(), key=lambda x: x[0] != LIVE):
        name = f"{system.PLAN_NAMES[n]} - " + ("YOUR LIVE PLAN" if n == LIVE else "paper comparison only")
        j = st.with_suffix(".json")
        print(f"===== {name} =====")
        print(j.read_text(encoding="utf-8") if j.exists() else "No paper case yet.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay-day")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--risk", type=float, help="fixed percent per trade (default: the plan's risk policy)")
    ap.add_argument("--stacked", action="store_true", help="no one-trade-at-a-time gate (every signal trades)")
    ap.add_argument("--no-shadow", action="store_true", help="don't run the other plans on paper next to the live plan")
    ap.add_argument("--broker", choices=["paper", "topstep"], default="paper",
                    help="topstep = DEMO: real orders on your Topstep practice account (settings_local.txt)")
    ap.add_argument("--allow-eval", action="store_true", help="with --broker topstep: allow a non-practice account")
    ap.add_argument("--flatten", action="store_true", help="close everything on the Topstep account now")
    ap.add_argument("--plan", default=system.LIVE_PLAN, choices=list(system.PLAN_NAMES), help="with --replay-day: which plan to check")
    ap.add_argument("--log", help="write everything to this file (the hidden scheduled demo has no window)")
    ap.add_argument("--catch-up", action="store_true", help="finish unfinished/missed demo days + draw their pictures")
    a = ap.parse_args()
    if a.log:
        Path(a.log).parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(a.log, "a", encoding="utf-8", buffering=1)
    risk = a.risk / 100 if a.risk else None
    plan = not a.stacked
    if a.replay_day:
        sys.exit(0 if replay_day_check(date.fromisoformat(a.replay_day), risk, plan, a.plan) else 1)
    if a.status:
        status()
        return
    if a.catch_up:
        catch_up_only()
        return
    if a.flatten:
        flatten_now(a.allow_eval)
        return
    if a.broker == "topstep":
        if a.fresh:
            for f in (DEMO[0], DEMO[0].with_suffix(".json")):
                f.unlink(missing_ok=True)
        live_demo(risk, a.allow_eval)
        return
    if a.fresh:
        for f in [x for st, od in FILES.values() for x in (st, st.with_suffix(".json"), od)]:
            f.unlink(missing_ok=True)
    live(risk, plan, shadow=plan and not a.no_shadow)


if __name__ == "__main__":
    main()
