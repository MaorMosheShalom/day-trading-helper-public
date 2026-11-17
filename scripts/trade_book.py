# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""TRADE PICTURE BOOK: a picture of every trade - the chart at the second it took the trade (LEFT, nothing after the
entry) and when it cashed out (RIGHT) - with why, the risk, and whether it won or lost money.
Folders: <plan>/<year>/<YYYY-MM Month>/<nnn date sym setup side WIN|LOSS $>.png + _month.png/.csv/.html per month,
_year_<y>.png per year, index.html at the top. Same trades as the live tool (scripts/plan_replay.py: minute-by-minute
replay, one trade at a time) on a simulated 50k prop-firm account (PLAN_RULES, risk = account.risk_for(plan)).

  python scripts/trade_book.py                       # every registered plan, all stored data -> reports/trade_book/
  python scripts/trade_book.py --plan example        # one plan
  python scripts/trade_book.py --live                # the live paper accounts: redraw reports/trade_book/LIVE/
                                                     #   (autotrade.py also draws it live after every trade)
  options: --from YYYY-MM-DD --to YYYY-MM-DD --no-zip --force (redraw existing pictures) --workers N
Pictures are drawn in parallel (ProcessPoolExecutor) and only once: existing ones are skipped (incremental builds).
"""
import argparse
import html
import os
import shutil
import sys
import textwrap
import warnings
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

from dth.lab import system
from dth.lab.system import MICRO_PER_POINT
from dth.live import account, engine

BOOK = ROOT / "reports" / "trade_book"
FORWARD_START = date(2026, 11, 11)          # first day after the data everything was tested on (to 2026-11-10)
PLANS = list(system.PLAN_NAMES)
FOLDER = {p: n + (" (live plan)" if p == system.LIVE_PLAN else "") for p, n in system.PLAN_NAMES.items()}
MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
NAMES = {"orb5_concretum": "5-minute opening-range breakout", "gao_momentum": "Intraday momentum (Gao et al.)"}
MICRO = {"ES": "MES", "NQ": "MNQ"}
REASON = {"stop": "STOP hit", "target": "TARGET hit", "time": "time exit 15:55", "trail": "trailing stop hit",
          "close": "closed at 16:00", "open": "closed (last bar)", "flip": "opposite signal"}


def why(tag: str, side: int) -> str:
    up = side > 0
    w = {"orb5_concretum": f"The first 5-minute candle closed {'UP' if up else 'DOWN'}. Rule (Zarattini & Aziz 2023): "
                           f"follow it at 09:35. Stop at the other end of that candle. Target 10x the risk.",
         "gao_momentum": f"From yesterday's close to 10:00 the market moved {'UP' if up else 'DOWN'}. Rule (Gao, Han, "
                         f"Li & Zhou 2018): trade the same direction from 15:30 to the close."}
    return w.get(tag, tag)


def levels(tag: str, day) -> list:
    """Reference levels the setup used (only bars before the entry are ever needed for them)."""
    r = day.rth
    if not len(r):
        return []
    if tag == "orb5_concretum":
        j = r.idx(575)
        return [(float(r.h[:j].max()), "first 5 min high"), (float(r.l[:j].min()), "first 5 min low")] if j > 0 else []
    if tag == "gao_momentum" and "pdc" in day.ctx:
        return [(float(day.ctx["pdc"]), "yesterday's close")]
    return []


def verdict(usd: float, R: float, reason: str) -> tuple[str, str]:
    if usd > 0:
        return "GOOD TRADE - made money, the rules worked", "#1a7f37"
    if usd == 0:
        return "FLAT - no money made or lost", "#555555"
    if R < -1.2:
        return "BAD FILL - lost more than the planned risk (price jumped past the stop)", "#b42318"
    if reason in ("time", "close", "open", "trail", "flip"):
        return "SMALL LOSS - closed by the rules before the stop (normal)", "#b54708"
    return "NORMAL LOSS - the stop did its job (losses are part of the plan)", "#b54708"


def hhmm(x, _=None):
    x = int(round(x))
    return f"{x // 60:02d}:{x % 60:02d}"


# ---------------------------------------------------------------------------------------------------- drawing
def _candles(ax, t, o, h, l, c):
    if not len(t):
        return
    up = c >= o
    col = np.where(up, "#26a269", "#c01c28")
    ax.vlines(t, l, h, colors=col, linewidth=0.7, zorder=2)
    body = np.maximum(np.abs(c - o), (h.max() - l.min()) * 0.002 if len(h) else 0.01)
    ax.bar(t, body, bottom=np.minimum(o, c), width=0.75, color=col, edgecolor=col, linewidth=0.3, zorder=3)


def draw_trade(job: dict) -> str:
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["text.parse_math"] = False      # "$" is money here, not math
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    out = Path(job["path"])
    if out.exists() and not job.get("force"):
        return "skip"
    bt, bo, bh, bl, bc = (np.asarray(job["bars"][k]) for k in ("t", "o", "h", "l", "c"))
    side, t_in, t_out = job["side"], job["t_in"], job["t_out"]
    entry, stop, target, exit_px = job["entry"], job["stop"], job["target"], job["exit"]
    x0 = max(570, t_in - 75)
    left = bt < t_in
    right = (bt <= t_out)
    vis = (bt >= x0) & right
    ys = [entry, exit_px] + ([stop] if stop == stop else []) + ([target] if target == target else [])
    ys += [v for v, _ in job["levels"] if v == v]
    lo = min(np.min(bl[vis]) if vis.any() else entry, min(ys))
    hi = max(np.max(bh[vis]) if vis.any() else entry, max(ys))
    pad = (hi - lo) * 0.08 or 1.0
    ylim = (lo - pad, hi + pad)
    win = job["usd"] > 0
    rc = "#1a7f37" if win else ("#555555" if job["usd"] == 0 else "#b42318")
    is_open = bool(job.get("open"))                  # live: the trade has started, the exit hasn't happened yet
    if is_open:
        rc = "#1f6feb"

    fig = plt.figure(figsize=(14, 8.2), dpi=80)
    gs = fig.add_gridspec(2, 2, height_ratios=[2.15, 1], left=0.05, right=0.98, top=0.86, bottom=0.03,
                          hspace=0.18, wspace=0.08)
    axL, axR = fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1])
    sidetxt = "LONG (buy)" if side > 0 else "SHORT (sell)"
    fig.text(0.05, 0.955, f"{job['plan_name']}  |  Trade #{job['n']}  |  {job['d']:%a %d %b %Y}  |  {job['sym']} "
             f"({MICRO[job['sym']]})  |  {NAMES.get(job['tag'], job['tag'])}  |  {sidetxt}", fontsize=13, weight="bold")
    fig.text(0.05, 0.915, f"Case #{job['case']} ({job['phase']})  |  balance before ${job['bal_before']:,.0f}  ->  "
             f"after ${job['bal_after']:,.0f}" + (f"  |  {job['event']}" if job["event"] else ""), fontsize=11, color="#333")
    badge = "OPEN" if is_open else ("WIN" if win else ("FLAT" if job["usd"] == 0 else "LOSS"))
    fig.text(0.98, 0.898, f"{badge}  {'+' if job['usd'] >= 0 else '-'}${abs(job['usd']):,.0f}"
             + (" so far" if is_open else ""), fontsize=21 if is_open else 24, weight="bold", color=rc, ha="right")

    for ax, mask, title, xmax in ((axL, left & (bt >= x0), f"THE SECOND IT TOOK THE TRADE  -  ENTRY {hhmm(t_in)} NY",
                                   t_in + max(20, (t_in - x0) * 0.35)),
                                  (axR, vis, (f"STILL OPEN at {hhmm(t_out)} NY  -  the exit picture comes when it closes"
                                              if is_open else
                                              f"WHEN IT CASHED OUT  -  EXIT {hhmm(t_out)} NY  ({REASON.get(job['reason'], job['reason'])})"),
                                   t_out + max(8, (t_out - x0) * 0.05))):
        _candles(ax, bt[mask], bo[mask], bh[mask], bl[mask], bc[mask])
        ax.set_xlim(x0 - 1, xmax)
        ax.set_ylim(*ylim)
        ax.set_title(title, fontsize=11.5, weight="bold", color="#1f3b73" if ax is axL else rc)
        ax.xaxis.set_major_formatter(FuncFormatter(hhmm))
        ax.xaxis.set_major_locator(MaxNLocator(8, integer=True))
        ax.grid(alpha=0.25)
        ax.tick_params(labelsize=8.5)
        for v, name in job["levels"]:
            ax.axhline(v, color="#7c3aed", lw=0.9, ls=":", alpha=0.8)
            ax.text(x0, v, f" {name} {v:,.2f}", fontsize=7.5, color="#7c3aed", va="bottom")
        ax.axhline(entry, color="#1f6feb", lw=1.0, ls="--")
        if stop == stop:
            ax.axhline(stop, color="#c01c28", lw=1.2, ls="--")
            ax.text(xmax, stop, f"STOP {stop:,.2f} ", fontsize=8, color="#c01c28", ha="right", va="bottom", weight="bold")
        if target == target:
            ax.axhline(target, color="#1a7f37", lw=1.2, ls="--")
            ax.text(xmax, target, f"TARGET {target:,.2f} ", fontsize=8, color="#1a7f37", ha="right", va="bottom", weight="bold")
        ax.text(xmax, entry, f"ENTRY {entry:,.2f} ", fontsize=8, color="#1f6feb", ha="right", va="top", weight="bold")
    # LEFT: the entry candle only up to the fill (nothing after the entry is shown), risk / reward boxes ahead
    i_e = int(np.searchsorted(bt, t_in))
    if i_e < len(bt):
        axL.vlines([t_in], min(bo[i_e], entry), max(bo[i_e], entry), colors="#1f6feb", lw=2.0)
    xl = axL.get_xlim()[1]
    if stop == stop:
        axL.fill_between([t_in, xl], entry, stop, color="#c01c28", alpha=0.10)
    if target == target:
        axL.fill_between([t_in, xl], entry, target, color="#1a7f37", alpha=0.10)
    axL.scatter([t_in], [entry], marker="^" if side > 0 else "v", s=180, color="#1a7f37" if side > 0 else "#c01c28",
                edgecolor="black", zorder=5)
    axL.axvline(t_in, color="#1f6feb", lw=0.8, alpha=0.6)
    # RIGHT: the path from entry to exit
    axR.fill_between([t_in, t_out], entry, exit_px, color=rc, alpha=0.12)
    axR.scatter([t_in], [entry], marker="^" if side > 0 else "v", s=120, color="#1f6feb", edgecolor="black", zorder=5)
    axR.scatter([t_out], [exit_px], marker="o" if is_open else "X", s=120 if is_open else 200, color=rc,
                edgecolor="black", zorder=6)
    axR.annotate(f"{'NOW' if is_open else 'EXIT'} {exit_px:,.2f}", (t_out, exit_px), xytext=(-10, 14 if win == (side > 0) else -18),
                 textcoords="offset points", ha="right", fontsize=9, weight="bold", color=rc)
    axR.axvline(t_in, color="#1f6feb", lw=0.8, alpha=0.6)

    # text panel
    ax = fig.add_subplot(gs[1, :])
    ax.axis("off")
    col = lambda x, y, head, body, c="#222": (ax.text(x, y, head, fontsize=11, weight="bold", color="#1f3b73",
                                                     transform=ax.transAxes, va="top"),
                                             ax.text(x, y - 0.17, body, fontsize=9.8, color=c, transform=ax.transAxes,
                                                     va="top", linespacing=1.45))
    col(0.0, 1.0, "WHY IT TOOK THE TRADE", textwrap.fill(job["why"], 58))
    stop_s = f"{stop:,.2f}" if stop == stop else "-"
    tgt_s = f"{target:,.2f}" if target == target else "none (exit by the rules)"
    col(0.36, 1.0, "THE TRADE",
        f"Entry   {hhmm(t_in)}  at {entry:,.2f}\nStop    {stop_s}\nTarget  {tgt_s}\n"
        + (f"Now     {hhmm(t_out)}  at {exit_px:,.2f}  (STILL OPEN)\n" if is_open else
           f"Exit    {hhmm(t_out)}  at {exit_px:,.2f}  ({REASON.get(job['reason'], job['reason'])})\n")
        + f"Moved   {job['pts']:+,.2f} points (after costs){' so far' if is_open else ''}")
    vtxt, vcol = verdict(job["usd"], job["R"], job["reason"])
    if is_open:
        vtxt, vcol = "OPEN - waiting for the exit (stop, target or the plan's own exit rule)", "#1f6feb"
    col(0.66, 1.0, "RISK AND RESULT",
        f"Risked  ${job['risk_usd']:,.0f} = {job['risk_pct']:.1f}% of ${job['bal_before']:,.0f}\n"
        f"        ({job['dist']:,.1f} pts x {job['micros']} {MICRO[job['sym']]} x ${MICRO_PER_POINT[job['sym']]:g}/pt)\n"
        + ("        (no hard stop: sized on a risk unit of the day's range)\n" if job["stop"] != job["stop"] else "")
        + f"{'So far' if is_open else 'Result'}  {'+' if job['usd'] >= 0 else '-'}${abs(job['usd']):,.0f}  =  {job['R']:+.2f}x the risk\n"
        f"Case    #{job['case']} {job['phase']}, balance ${job['bal_after']:,.0f}")
    ax.text(0.66, 0.02, textwrap.fill(vtxt, 50), fontsize=10.5, weight="bold", color=vcol, transform=ax.transAxes, va="bottom")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.png")
    fig.savefig(tmp, dpi=80)
    plt.close(fig)
    os.replace(tmp, out)
    return "drawn"


def _draw_many(jobs):
    return [draw_trade(j) for j in jobs]


# ---------------------------------------------------------------------------------------------------- data
def simulate_with_trades(gev, rules, pol):
    """account.simulate, but also returns, for every trade the case took, the engine trade + case details."""
    import plan_replay as PR
    prices = PR.prices_fn()
    acc = account.PropAccount(rules, pol)
    rows = []
    for d, events in gev:
        acc.start_day(d)
        pend = {}
        for ev in events:
            px = (lambda sym, T, _d=d: prices(_d, sym, T))
            tr = ev.trade
            k = (tr.sym, tr.tag, tr.t_in, tr.side)
            if ev.kind == "entry":
                bal, ph, case, rp = acc.balance, acc.phase, acc.case_no, acc.current_risk() if acc.phase in ("test", "funded") else 0
                n = acc.on_entry(d, ev.T, tr)
                if n:
                    pend[k] = (bal, ph, case, rp, n)
            else:
                nt, nl = len(acc.trades), len(acc.log)
                acc.on_exit(d, ev.T, tr, px)
                if k in pend and len(acc.trades) > nt:
                    bal, ph, case, rp, n = pend.pop(k)
                    row = acc.trades[nt]
                    ev_txt = [e["event"] for e in acc.log[nl:] if e["event"] in ("case LOST", "TEST PASSED", "PAYOUT")]
                    rows.append({"d": d, "trade": tr, "case": case, "phase": ph, "bal_before": bal, "risk_pct": 100 * rp,
                                 "micros": n, "usd": row["usd"], "bal_after": row["balance"],
                                 "event": {"case LOST": "CASE LOST on this trade", "TEST PASSED": "TEST PASSED on this trade",
                                           "PAYOUT": "PAYOUT after this trade"}.get(ev_txt[0], "") if ev_txt else ""})
        acc.open.clear()
    return acc, rows


def plan_rows(plan: str, start: date, end: date, live: bool):
    import plan_replay as PR
    pairs = [p for p in PR.pairs() if start <= p[0].d <= end]
    if live:                                            # few days: replay directly (no cache)
        from dth.lab import daily
        sig = daily.signals()
        res = [(es.d, *engine.replay_day(es, nq, sig.loc[es.d] if es.d in sig.index else None, plan)) for es, nq in pairs]
    else:
        res = [x for x in PR.replay_all(plan) if start <= x[0] <= end]
    gev = PR.gated(res, plan)
    signals = {d: sum(e.kind == "entry" for e in ev) for d, ev, _ in res}
    taken = {d: sum(e.kind == "entry" for e in ev) for d, ev in gev}
    rules = account.PropRules(daily_loss_stop=None, **account.PLAN_RULES)
    acc, rows = simulate_with_trades(gev, rules, account.risk_for(plan))
    return acc, rows, {d: signals[d] - taken.get(d, 0) for d in signals}, {es.d: (es, nq) for es, nq in pairs}


def safe(s: str) -> str:
    return "".join(ch for ch in s if ch not in '<>:"/\\|?*').strip()


def build_plan(plan: str, start: date, end: date, root: Path, live: bool, workers: int, force: bool) -> pd.DataFrame:
    name = system.PLAN_NAMES[plan]
    acc, rows, skipped, days = plan_rows(plan, start, end, live)
    base = root / FOLDER[plan]
    jobs, table = [], []
    for i, r in enumerate(rows, 1):
        tr, d = r["trade"], r["d"]
        day = days[d][0 if tr.sym == "ES" else 1]
        dist = account.risk_distance(tr)
        R = tr.pts / dist
        res = "WIN" if r["usd"] > 0 else ("FLAT" if r["usd"] == 0 else "LOSS")
        fn = safe(f"{i:04d} {d} {tr.sym} {tr.tag.replace('_', ' ')} {'LONG' if tr.side > 0 else 'SHORT'} {res} "
                  f"{'+' if r['usd'] >= 0 else '-'}${abs(r['usd']):,.0f}") + ".png"
        mdir = base / f"{d.year}" / f"{d:%Y-%m} {MONTHS[d.month]}"
        b = day.rth
        jobs.append({"path": str(mdir / fn), "force": force, "plan_name": name + (" - LIVE PAPER" if live else ""),
                     "n": i, "d": d, "sym": tr.sym, "tag": tr.tag, "side": tr.side, "t_in": tr.t_in, "t_out": tr.t_out,
                     "entry": tr.entry, "stop": tr.stop, "target": tr.target, "exit": tr.exit, "reason": tr.reason,
                     "pts": tr.pts, "dist": dist, "R": R, "usd": r["usd"], "micros": r["micros"],
                     "risk_usd": r["micros"] * dist * MICRO_PER_POINT[tr.sym], "risk_pct": r["risk_pct"],
                     "bal_before": r["bal_before"], "bal_after": r["bal_after"], "case": r["case"], "phase": r["phase"],
                     "event": r["event"], "why": why(tr.tag, tr.side), "levels": levels(tr.tag, day),
                     "bars": {"t": b.t, "o": b.o, "h": b.h, "l": b.l, "c": b.c}})
        table.append({"#": i, "date": d, "month": f"{d:%Y-%m}", "year": d.year, "symbol": tr.sym,
                      "strategy": NAMES.get(tr.tag, tr.tag), "side": "LONG" if tr.side > 0 else "SHORT",
                      "entry time": hhmm(tr.t_in), "entry": round(tr.entry, 2), "stop": round(tr.stop, 2) if tr.stop == tr.stop else None,
                      "target": round(tr.target, 2) if tr.target == tr.target else None, "exit time": hhmm(tr.t_out),
                      "exit": round(tr.exit, 2), "exit reason": REASON.get(tr.reason, tr.reason), "micros": r["micros"],
                      "risk $": round(r["micros"] * dist * MICRO_PER_POINT[tr.sym]), "risk %": round(r["risk_pct"], 1),
                      "result": res, "$": round(r["usd"]), "R": round(R, 2), "case": r["case"], "phase": r["phase"],
                      "balance after": round(r["bal_after"]), "event": r["event"], "picture": str(Path(jobs[-1]["path"]).relative_to(root))})
    print(f"[{name}] {len(jobs)} trades -> drawing pictures ({workers} workers) ...", flush=True)
    todo = [j for j in jobs if force or not Path(j["path"]).exists()]
    if todo:
        if workers > 1 and len(todo) > 20:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                list(ex.map(_draw_many, [todo[i::workers] for i in range(workers)]))
        else:
            _draw_many(todo)
    print(f"[{name}] {len(todo)} new pictures, {len(jobs) - len(todo)} already there", flush=True)
    t = pd.DataFrame(table)
    log = pd.DataFrame(acc.log)
    summaries(t, log, skipped, base, name + (" - LIVE PAPER" if live else ""), root)
    t.attrs.update(plan=plan, name=name, folder=FOLDER[plan], net=acc.paid_to_user - acc.fees_paid,
                   paid=acc.paid_to_user, fees=acc.fees_paid, balance=acc.balance, phase=acc.phase,
                   cases=int((log.event == "case bought").sum()) if len(log) else 0,
                   lost=int((log.event == "case LOST").sum()) if len(log) else 0,
                   passed=int((log.event == "TEST PASSED").sum()) if len(log) else 0,
                   payouts=int((log.event == "PAYOUT").sum()) if len(log) else 0)
    return t


# ---------------------------------------------------------------------------------------------------- summaries
MONTH_COLS = ["#", "date", "symbol", "strategy", "side", "entry time", "entry", "stop", "target", "exit time", "exit",
              "exit reason", "micros", "risk $", "risk %", "result", "$", "R", "balance after", "event"]


def _cell(col, v) -> str:
    if v is None or (isinstance(v, float) and v != v) or v == "":
        return "-"
    if col == "$":
        return f"{'+' if v >= 0 else '-'}${abs(v):,.0f}"
    if col in ("risk $", "balance after"):
        return f"${v:,.0f}"
    if col == "risk %":
        return f"{v:.1f}%"
    return str(v)


def month_html(m: pd.DataFrame, name: str, title: str, head: str, month_usd: str, tot: float, sub: str) -> str:
    """The month page: everything is text (searchable, Ctrl+F works) except the graphs and the trade pictures.
    A table of ALL the month's trades (each row jumps to its picture), then every trade's text line + picture."""
    e = html.escape
    cols = [c for c in MONTH_COLS if c in m.columns]
    rows, secs = [], []
    for r in m.to_dict("records"):
        cls = "g" if r["$"] > 0 else ("r" if r["$"] < 0 else "")
        txt = {c: _cell(c, r.get(c)) for c in cols}
        pic = r.get("picture") or ""
        first = f"<a href='#t{r['#']}'>{e(txt['#'])}</a>" if pic else e(txt["#"])
        rows.append(f"<tr class='trade {cls}'><td>{first}</td>"
                    + "".join(f"<td>{e(txt[c])}</td>" for c in cols[1:]) + "</tr>")
        if pic:
            line = (f"{txt['date']} | {txt['symbol']} | {txt['strategy']} | {txt['side']} | in {txt['entry time']} at "
                    f"{txt['entry']} | stop {txt['stop']} | target {txt['target']} | out {txt['exit time']} at "
                    f"{txt['exit']} ({txt['exit reason']}) | {txt['micros']} micros | risk {txt['risk $']} "
                    f"({txt['risk %']}) | {txt['result']} {txt['$']} ({txt['R']} R) | balance after "
                    f"{txt['balance after']}" + (f" | {txt['event']}" if txt["event"] != "-" else ""))
            secs.append(f"<div class='trade sec' id='t{r['#']}'><h3>{e(Path(pic).stem)}</h3><p class='{cls}'>{e(line)}"
                        f" &nbsp; <a href='#list'>back to the list</a></p><img loading='lazy' src='{quote(Path(pic).name)}'"
                        f" alt='{e(Path(pic).stem)}'></div>")
    css = ("body{font-family:Segoe UI,Arial;margin:16px;background:#fafafa;color:#222}img{max-width:100%;"
           "border:1px solid #ccc;margin-bottom:8px}table{border-collapse:collapse;font-size:13px;margin:12px 0 28px}"
           "td,th{border:1px solid #ccc;padding:3px 6px;white-space:nowrap}th{background:#eef;position:sticky;top:0}"
           "tr.g td{background:#eefbf1}tr.r td{background:#fdf0ef}p.g{color:#1a7f37}p.r{color:#b42318}"
           ".big{font-size:26px;font-weight:bold}.big.g{color:#1a7f37}.big.r{color:#b42318}"
           "#q{font-size:16px;padding:6px 10px;width:420px;max-width:90%}.sec{margin-bottom:28px}")
    js = ("function f(){var q=document.getElementById('q').value.toLowerCase().split(' ').filter(Boolean),n=0;"
          "document.querySelectorAll('.trade').forEach(function(x){var ok=q.every(function(w){"
          "return x.textContent.toLowerCase().indexOf(w)>=0});x.style.display=ok?'':'none';"
          "if(ok&&x.tagName=='TR')n++});document.getElementById('n').textContent=n}")
    return (f"<!doctype html><meta charset='utf-8'><title>{e(name)} {e(title)}</title><style>{css}</style>"
            f"<h1>{e(name)} - {e(title)}</h1><p><b>{e(head)}</b></p>"
            f"<p class='big {'g' if tot >= 0 else 'r'}'>{e(month_usd)}</p><p>{e(sub)}</p>"
            f"<img src='_month.png' alt='graphs: balance after each trade, $ per trade'>"
            f"<h2 id='list'>All {len(m)} trades</h2><p><input id='q' oninput='f()' placeholder='Search: e.g. "
            f"LOSS, SHORT, opening drive, 2024-03-12, +$1' autofocus> <span id='n'>{len(m)}</span> shown "
            f"(words must all match; Ctrl+F also works)</p>"
            f"<table><tr>{''.join(f'<th>{e(c)}</th>' for c in cols)}</tr>{''.join(rows)}</table>"
            f"<h2>Every trade with its picture</h2>{''.join(secs)}<script>{js}</script>")


def summaries(t: pd.DataFrame, log: pd.DataFrame, skipped: dict, base: Path, name: str, root: Path):
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["text.parse_math"] = False      # "$" is money here, not math
    import matplotlib.pyplot as plt
    if not len(t):
        return
    if len(log):
        log = log.assign(month=pd.to_datetime(log.date).dt.strftime("%Y-%m"))
    for (y, mth), m in t.groupby(["year", "month"]):
        mdir = base / f"{y}" / f"{mth} {MONTHS[int(mth[5:])]}"
        mdir.mkdir(parents=True, exist_ok=True)
        m.drop(columns=["picture"]).to_csv(mdir / "_month.csv", index=False)
        ev = log[(log.month == mth) & log.event.isin(["case bought", "case LOST", "TEST PASSED", "PAYOUT"])] if len(log) else log
        sk = sum(v for d, v in skipped.items() if f"{d:%Y-%m}" == mth)
        wins, losses = int((m["$"] > 0).sum()), int((m["$"] < 0).sum())
        tot = m["$"].sum()
        head = f"{name}  |  {MONTHS[int(mth[5:])]} {y}  |  {len(m)} trades: {wins} wins, {losses} losses " \
               f"({100 * wins / max(len(m), 1):.0f}% win)"
        month_usd = f"MONTH {'+' if tot >= 0 else '-'}${abs(tot):,.0f}"
        events = ", ".join(f"{r.date:%d} {r.event}" for r in ev.itertuples()) if len(ev) else "none"
        sub = f"best +${m['$'].max():,.0f}  |  worst -${abs(m['$'].min()):,.0f}  |  signals skipped " \
              f"because a trade was already open: {sk}  |  case events: {events}"
        fig = plt.figure(figsize=(14, 5.2), dpi=80)          # graphs only - the trade list is text in _month.html
        gs = fig.add_gridspec(1, 2, left=0.06, right=0.98, top=0.80, bottom=0.10, wspace=0.18)
        fig.text(0.06, 0.92, head, fontsize=15, weight="bold")
        fig.text(0.98, 0.91, month_usd, fontsize=22, weight="bold", color="#1a7f37" if tot >= 0 else "#b42318", ha="right")
        fig.text(0.06, 0.855, sub, fontsize=10.5)
        a1, a2 = fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1])
        a1.plot(range(1, len(m) + 1), m["balance after"], marker="o", color="#1f6feb")
        a1.set_title("Case balance after each trade", fontsize=11, weight="bold")
        a1.grid(alpha=0.3)
        a1.set_xlabel("trade # in the month")
        a2.bar(range(1, len(m) + 1), m["$"], color=np.where(m["$"] >= 0, "#26a269", "#c01c28"))
        a2.set_title("$ per trade", fontsize=11, weight="bold")
        a2.grid(alpha=0.3, axis="y")
        a2.set_xlabel("trade # in the month")
        fig.savefig(mdir / "_month.png", dpi=80)
        plt.close(fig)
        (mdir / "_month.html").write_text(month_html(m, name, f"{MONTHS[int(mth[5:])]} {y}", head, month_usd, tot, sub),
                                          encoding="utf-8")
    for y, yt in t.groupby("year"):
        mo = yt.groupby("month")["$"].sum()
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(14, 6), dpi=80)
        fig.suptitle(f"{name}  |  {y}  |  {len(yt)} trades, {100 * (yt['$'] > 0).mean():.0f}% win  |  "
                     f"YEAR {'+' if yt['$'].sum() >= 0 else '-'}${abs(yt['$'].sum()):,.0f} (trading result of the case)",
                     fontsize=13, weight="bold")
        a1.bar([x[5:] for x in mo.index], mo.values, color=np.where(mo.values >= 0, "#26a269", "#c01c28"))
        a1.set_title("$ per month")
        a1.grid(alpha=0.3, axis="y")
        a2.plot(range(len(yt)), yt["balance after"].values, color="#1f6feb")
        a2.set_title("case balance after each trade (resets to 50k when a case is lost / a funded case starts)")
        a2.grid(alpha=0.3)
        fig.savefig(base / f"{y}" / f"_year_{y}.png", dpi=80)
        plt.close(fig)


def write_index(root: Path, tables: list, title: str, live: bool):
    parts = [f"<!doctype html><meta charset='utf-8'><title>{html.escape(title)}</title><style>"
             "body{font-family:Segoe UI,Arial;margin:16px;background:#fafafa;color:#222}table{border-collapse:collapse;"
             "margin-bottom:28px}td,th{border:1px solid #ccc;padding:4px 8px;text-align:right}th{background:#eef}"
             "td.l{text-align:left}.g{color:#1a7f37;font-weight:bold}.r{color:#b42318;font-weight:bold}</style>"
             f"<h1>{html.escape(title)}</h1><p>Every trade has a picture: LEFT = the chart at the second it took the "
             "trade (nothing after it), RIGHT = when it cashed out. Click a month to scroll through all its trades. "
             "Money = a simulated 50k prop-firm evaluation account ($3k payouts at 60k, 80% split), sized at each plan's risk per trade.</p>"]
    parts.append("<table><tr><th class=l>plan</th><th>trades</th><th>win %</th><th>trading $ (case)</th><th>cases bought</th>"
                 "<th>cases lost</th><th>tests passed</th><th>payouts</th><th>net $ to you (after case fees)</th></tr>")
    for t in tables:
        a = t.attrs
        if not len(t):
            parts.append(f"<tr><td class=l>{html.escape(a.get('name', ''))}</td><td colspan=8>no closed trades yet"
                         f"{' - 1 trade OPEN now (below)' if a.get('open_pics') else ''}</td></tr>")
            continue
        parts.append(f"<tr><td class=l><a href='#{a['plan']}'>{html.escape(a['name'])}</a></td><td>{len(t)}</td>"
                     f"<td>{100 * (t['$'] > 0).mean():.0f}</td><td>{t['$'].sum():+,.0f}</td><td>{a['cases']}</td>"
                     f"<td>{a['lost']}</td><td>{a['passed']}</td><td>{a['payouts']}</td>"
                     f"<td class={'g' if a['net'] >= 0 else 'r'}>{a['net']:+,.0f}</td></tr>")
    parts.append("</table>")
    for t in tables:
        a = t.attrs
        if not len(t) and not a.get("open_pics"):
            continue
        parts.append(f"<h2 id='{a['plan']}'>{html.escape(a['name'])}</h2>")
        if live and "case" in a:
            parts.append(f"<p>Paper case #{a['case']} ({html.escape(str(a['phase']))}), balance now "
                         f"<b>${a['balance']:,.0f}</b>.</p>")
        for p in a.get("open_pics", []):
            parts.append(f"<p class=g>OPEN NOW: <a href='{quote(p)}'>{html.escape(Path(p).stem)}</a></p>"
                         f"<a href='{quote(p)}'><img src='{quote(p)}' style='max-width:100%;border:2px solid #1f6feb'></a>")
        if not len(t):
            continue
        if live:
            parts.append(normal_range_html(a["plan"], t))
        parts.append("<table><tr><th class=l>month</th><th>trades</th><th>wins</th><th>losses</th><th>win %</th><th>$</th>"
                     "<th class=l>events</th><th class=l>open</th></tr>")
        for (y, mth), m in t.groupby(["year", "month"]):
            folder = f"{a['folder']}/{y}/{mth} {MONTHS[int(mth[5:])]}"
            ev = ", ".join(e for e in m.event if e)
            s = m["$"].sum()
            parts.append(f"<tr><td class=l>{mth}</td><td>{len(m)}</td><td>{int((m['$'] > 0).sum())}</td>"
                         f"<td>{int((m['$'] < 0).sum())}</td><td>{100 * (m['$'] > 0).mean():.0f}</td>"
                         f"<td class={'g' if s >= 0 else 'r'}>{s:+,.0f}</td><td class=l>{html.escape(ev)}</td>"
                         f"<td class=l><a href='{quote(folder)}/_month.html'>all trades</a> | "
                         f"<a href='{quote(folder)}/_month.png'>summary</a> | <a href='{quote(f'{a['folder']}/{y}/_year_{y}.png')}'>year {y}</a></td></tr>")
        parts.append("</table>")
    (root / "index.html").write_text("\n".join(parts), encoding="utf-8")


def normal_range_html(plan: str, live: pd.DataFrame) -> str:
    """LIVE vs the backtest: where the live average $ per trade (at the same fixed $500 risk) sits among random
    draws of the same number of backtest trades (all data)."""
    try:
        bt = pd.read_excel(ROOT / "reports" / "candidates_test.xlsx", sheet_name="Trades")
        r = bt[bt.plan == system.PLAN_NAMES[plan]].R.to_numpy()
    except Exception:
        return ""
    n = len(live)
    if n < 30:
        return f"<p><b>Live check:</b> {n} trades so far - too early to judge (need at least 30).</p>"
    rng = np.random.default_rng(1)
    draws = 500 * rng.choice(r, size=(5000, n)).mean(axis=1)
    lo, hi = np.percentile(draws, [5, 95])
    live_r = 500 * (live["R"].mean())
    word = "INSIDE the normal range" if live_r >= lo else "BELOW normal - worse than the backtest would expect"
    return (f"<p><b>Live check:</b> {n} trades, ${live_r:+.0f} per trade at $500 risk. For {n} backtest trades the "
            f"normal range (90%) is ${lo:+.0f} to ${hi:+.0f} -> <b>{word}</b>.</p>")


# ---------------------------------------------------------------------------------------------------- LIVE = the demo
# The LIVE book is exactly what the demo's paper cases did (scripts/autotrade.py): an "OPEN" picture the moment a trade
# starts (left = the chart at the entry, right = the chart so far), replaced by the WIN / LOSS picture the moment it
# closes; month pages + index.html are rewritten after every trade. Source: each PaperTrader's own account.
LIVE = BOOK / "LIVE"


def _side(s) -> str:
    return "LONG" if s > 0 else "SHORT"


def _fname(n, d, sym, tag, side, usd, is_open) -> str:
    res = "OPEN" if is_open else ("WIN" if usd > 0 else ("FLAT" if usd == 0 else "LOSS"))
    tail = "" if is_open else f" {'+' if usd >= 0 else '-'}${abs(usd):,.0f}"
    return safe(f"{n:04d} {d} {sym} {tag.replace('_', ' ')} {_side(side)} {res}{tail}") + ".png"


def _live_job(plan, n, r, day, path, is_open=False) -> dict:
    """r = an account trade row (with the details runner.PaperTrader adds) or an open trade's entry info + now."""
    sym, tag, b = r["sym"], r["setup"], day.rth
    return {"path": str(path), "force": is_open, "plan_name": system.PLAN_NAMES[plan] + " - LIVE DEMO", "open": is_open,
            "n": n, "d": r["date"], "sym": sym, "tag": tag, "side": r["side"], "t_in": r["t_in"], "t_out": r["t_out"],
            "entry": r["entry"], "stop": r["stop"], "target": r["target"], "exit": r["exit"], "reason": r["reason"],
            "pts": r["pts"], "dist": r["dist"], "R": r["R"], "usd": r["usd"], "micros": r["micros"],
            "risk_usd": r["micros"] * r["dist"] * MICRO_PER_POINT[sym], "risk_pct": r["risk_pct"],
            "bal_before": r["bal_before"], "bal_after": r["balance"], "case": r["case"], "phase": r["phase"],
            "event": r.get("event", ""), "why": why(tag, r["side"]), "levels": levels(tag, day),
            "bars": {"t": b.t, "o": b.o, "h": b.h, "l": b.l, "c": b.c}}


class _Days:
    """Day objects by (symbol, date): today's live Days if given, else built from the local store (cached)."""

    def __init__(self, days=None):
        self.live = {(v.sym, v.d): v for v in (days or {}).values() if v is not None}
        self.cache = {}

    def get(self, sym, d):
        if (sym, d) in self.live:
            return self.live[(sym, d)]
        if (sym, d) not in self.cache:
            from dth.lab.days import build_live
            self.cache[(sym, d)] = build_live(sym, d)
        return self.cache[(sym, d)]


def _backfill(plan, rows, dd: _Days, store: Path) -> dict:
    """Details for trade rows written before the trader kept them (e.g. the first demo day): replay that day from the
    store - the same minute-by-minute engine - and match (date, setup, side, exit minute). Cached in a json."""
    import json
    have = json.loads(store.read_text(encoding="utf-8")) if store.exists() else {}
    key = lambda r: f"{r['date']}|{r['time']}|{r['setup']}|{r['side']}"
    todo = [r for r in rows if "t_in" not in r and key(r) not in have]
    for d in sorted({r["date"] for r in todo}):
        es, nq = dd.get("ES", d), dd.get("NQ", d)
        if es is None or nq is None:
            continue
        try:
            from dth.lab import daily
            sig = daily.signals()
            row = sig.loc[d] if d in sig.index else None
        except Exception:
            row = None
        ev, _ = engine.replay_day(es, nq, row, plan)
        exits = [e for e in engine.plan_events(ev, plan) if e.kind == "exit"]
        for r in (x for x in todo if x["date"] == d):
            m = [e.trade for e in exits if e.T == r["time"] and e.trade.tag == r["setup"] and e.trade.side == r["side"]]
            if not m:
                continue
            tr = m[0]
            dist = account.risk_distance(tr)
            bal0 = r["balance"] - r["usd"]
            have[key(r)] = dict(t_in=tr.t_in, t_out=tr.t_out, entry=tr.entry, stop=tr.stop, target=tr.target,
                                exit=tr.exit, dist=dist, R=tr.pts / dist if dist else 0.0, bal_before=bal0,
                                risk_pct=100 * r["micros"] * dist * MICRO_PER_POINT[r["sym"]] / bal0, event="")
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps(have, default=str), encoding="utf-8")
    return {k: v for k, v in have.items()}


def live_update(traders: dict, days: dict | None = None, backfill: bool = False) -> int:
    """Redraw the LIVE book from the paper traders' own cases: {plan: PaperTrader}. Draws missing WIN/LOSS pictures,
    an OPEN picture for every open trade, the month pages and LIVE/index.html. Returns the number of pictures drawn."""
    from dth.lab.days import COST_PTS
    LIVE.mkdir(parents=True, exist_ok=True)
    dd = _Days(days)
    tables, drawn = [], 0
    for folder in [p for p in LIVE.iterdir() if p.is_dir() and p.name not in FOLDER.values()]:
        shutil.rmtree(folder, ignore_errors=True)           # books of plans / names that no longer exist
    for plan, tr in sorted(traders.items(), key=lambda x: x[0] != system.LIVE_PLAN):
        st, acc = tr.state, tr.state.acc
        if st.gate is None:
            continue
        name = system.PLAN_NAMES[plan]
        base = LIVE / FOLDER[plan]
        rows = [dict(r) for r in acc.trades]
        if backfill:
            have = _backfill(plan, rows, dd, base / "_details.json")
            for r in rows:
                r.update(have.get(f"{r['date']}|{r['time']}|{r['setup']}|{r['side']}", {}) if "t_in" not in r else {})
        jobs, table, keep = [], [], set()
        for n, r in enumerate(rows, 1):
            d = r["date"]
            mdir = base / f"{d.year}" / f"{d:%Y-%m} {MONTHS[d.month]}"
            path = mdir / _fname(n, d, r["sym"], r["setup"], r["side"], r["usd"], False)
            has = "t_in" in r
            if has:
                keep.add(path)
                if not path.exists():
                    day = dd.get(r["sym"], d)
                    if day is not None:
                        jobs.append(_live_job(plan, n, r, day, path))
            res = "WIN" if r["usd"] > 0 else ("FLAT" if r["usd"] == 0 else "LOSS")
            table.append({"#": n, "date": d, "month": f"{d:%Y-%m}", "year": d.year, "symbol": r["sym"],
                          "strategy": NAMES.get(r["setup"], r["setup"]), "side": _side(r["side"]),
                          "entry time": hhmm(r["t_in"]) if has else "", "entry": round(r["entry"], 2) if has else None,
                          "stop": round(r["stop"], 2) if has and r["stop"] == r["stop"] else None,
                          "target": round(r["target"], 2) if has and r["target"] == r["target"] else None,
                          "exit time": hhmm(r["t_out"] if has else r["time"]), "exit": round(r["exit"], 2) if has else None,
                          "exit reason": REASON.get(r["reason"], r["reason"]), "micros": r["micros"],
                          "risk $": round(r["micros"] * r["dist"] * MICRO_PER_POINT[r["sym"]]) if has else 0,
                          "risk %": round(r["risk_pct"], 1) if has else None, "result": res, "$": round(r["usd"]),
                          "R": round(r["R"], 2) if has else None, "case": r["case"], "phase": r["phase"],
                          "balance after": round(r["balance"]), "event": r.get("event", ""),
                          "picture": str(path.relative_to(LIVE)) if has else ""})
        # trades open right now: picture of the entry + the chart so far
        open_pics = []
        info = getattr(st, "entry_info", {}) or {}
        for k, p in acc.open.items():
            e = info.get(k)
            day = dd.get(p.sym, e["d"]) if e else None
            if e is None or day is None or e["d"] != st.day or not len(day.rth):
                continue
            last_t, last_c = int(day.rth.t[-1]), float(day.rth.c[-1])
            pts = e["side"] * (last_c - e["entry"]) - COST_PTS[p.sym]
            usd = pts * MICRO_PER_POINT[p.sym] * e["micros"]
            r = {**e, "date": e["d"], "t_out": max(last_t, e["t_in"]), "exit": last_c, "reason": "open", "pts": pts,
                 "usd": usd, "R": pts / e["dist"] if e["dist"] else 0.0, "balance": acc.balance, "event": ""}
            n = len(rows) + 1 + len(open_pics)
            path = (base / f"{e['d'].year}" / f"{e['d']:%Y-%m} {MONTHS[e['d'].month]}" /
                    _fname(n, e["d"], p.sym, e["setup"], e["side"], usd, True))
            keep.add(path)
            jobs.append(_live_job(plan, n, r, day, path, is_open=True))
            open_pics.append(path.relative_to(LIVE).as_posix())
        for j in jobs:
            draw_trade(j)
        drawn += len(jobs)
        for old in base.rglob("*.png") if base.exists() else []:     # OPEN pictures of trades that have closed, etc.
            if not old.name.startswith("_") and old not in keep:
                old.unlink(missing_ok=True)
        t = pd.DataFrame(table)
        log = pd.DataFrame(acc.log)
        if len(t):
            summaries(t, log, {}, base, name + " - LIVE DEMO", LIVE)
        t.attrs.update(plan=plan, name=name, folder=FOLDER[plan], net=acc.paid_to_user - acc.fees_paid,
                       cases=int((log.event == "case bought").sum()) if len(log) else 0,
                       lost=int((log.event == "case LOST").sum()) if len(log) else 0,
                       passed=int((log.event == "TEST PASSED").sum()) if len(log) else 0,
                       payouts=int((log.event == "PAYOUT").sum()) if len(log) else 0, open_pics=open_pics,
                       balance=acc.balance, phase=acc.phase, case=acc.case_no)
        tables.append(t)
    first = min((r["date"] for tr in traders.values() for r in tr.state.acc.trades), default=None)
    write_index(LIVE, tables, "LIVE DEMO - what the demo really did, trade by trade"
                + (f" (from {first})" if first else ""), True)
    share(LIVE)
    return drawn


def share(root: Path):
    f = ROOT / "settings_local.txt"
    if not f.exists():
        return
    conf = dict(l.split("=", 1) for l in f.read_text(encoding="utf-8").splitlines() if "=" in l and not l.startswith("#"))
    dest = conf.get("SHARE_TO", "").strip()
    if not dest:
        return
    target = Path(dest) / "trade_book" / root.relative_to(BOOK) if root != BOOK else Path(dest) / "trade_book"
    n = 0
    for p in root.rglob("*"):
        if p.is_file():
            q = target / p.relative_to(root)
            if not q.exists() or q.stat().st_mtime < p.stat().st_mtime or q.stat().st_size != p.stat().st_size:
                q.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, q)
                n += 1
    print(f"copied {n} new/changed files to {target}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="all", choices=PLANS + ["all"])
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--workers", type=int, default=max(1, min(12, (os.cpu_count() or 2) - 2)))
    a = ap.parse_args()
    if a.live:                                      # the demo's own paper cases (scripts/autotrade.py), not a re-simulation
        import autotrade as AT
        from dth.live import runner
        traders = {n: runner.PaperTrader(st, od, plan_name=n) for n, (st, od) in AT.FILES.items() if st.exists()}
        if not traders:
            print("no demo paper case yet (it starts on the first demo day) - nothing to draw")
            return
        n = live_update(traders, backfill=True)
        print(f"LIVE book: {n} pictures drawn -> {LIVE / 'index.html'}")
        return
    import plan_replay as PR
    days = [p[0].d for p in PR.pairs()]
    start = date.fromisoformat(a.start) if a.start else (FORWARD_START if a.live else date(2019, 1, 1))
    end = date.fromisoformat(a.end) if a.end else days[-1]
    root = BOOK / "LIVE" if a.live else BOOK
    root.mkdir(parents=True, exist_ok=True)
    plans = PLANS if a.plan == "all" else [a.plan]
    if a.live and end < start:
        print(f"no stored days after {start} yet (newest stored day {days[-1]}) - nothing to draw")
        return
    tables = [build_plan(p, start, end, root, a.live, a.workers, a.force) for p in plans]
    title = (f"LIVE PAPER trades (days the plans never saw) {start} -> {end}" if a.live else
             f"Trade picture book {start} -> {end} (all stored data)")
    write_index(root, tables, title, a.live)
    print(f"index: {root / 'index.html'}")
    if not a.no_zip and not a.live:                 # one zip per plan (all plans together is ~800 MB)
        import tempfile
        import zipfile
        for t in tables:
            z = ROOT / "reports" / f"trade_book_{t.attrs['name'].replace(' ', '_')}_{start:%Y-%m}_{end:%Y-%m}.zip"
            with tempfile.TemporaryDirectory() as tmp:
                write_index(Path(tmp), [t], f"{t.attrs['name']} - trade picture book {start} -> {end}", False)
                with zipfile.ZipFile(z, "w", zipfile.ZIP_STORED) as zf:      # PNGs are already compressed
                    zf.write(Path(tmp) / "index.html", "index.html")
                    for p in sorted((root / t.attrs["folder"]).rglob("*")):
                        if p.is_file():
                            zf.write(p, p.relative_to(root).as_posix())
            print(f"zip: {z} ({z.stat().st_size / 1e6:,.0f} MB)")
    share(root)


if __name__ == "__main__":
    main()
