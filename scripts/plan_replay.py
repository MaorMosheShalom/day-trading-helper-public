# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Replay EVERY stored day minute by minute on the LIVE code path for one plan (engine.replay_day -> no look-ahead),
cached per data version + code version, in parallel (ProcessPoolExecutor). Shared by the validation scripts and
scripts/trade_book.py, so the tests and the pictures show exactly the same trades.

  replay_all("example") -> [(date, events_before_gate, look_ahead_violations), ...]   (all stored days)
  gated(res, "example")  -> [(date, events after the one-at-a-time gate), ...]          (what the trader trades)
"""
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

from dth import runcache
from dth.lab import daily, evaluate
from dth.live import engine

CODE = [ROOT / "dth" / "lab" / f for f in ("system.py", "examples.py", "sim.py", "days.py")] + \
       [ROOT / "dth" / "live" / "engine.py"]
_PAIRS = None


def pairs():
    global _PAIRS
    if _PAIRS is None:
        _PAIRS = evaluate.load_pairs()
    return _PAIRS


def _chunk(args):
    items, plan = args
    out = []
    for es, nq, row in items:
        ev, viol = engine.replay_day(es, nq, row, plan)
        out.append((es.d, ev, viol))
    return out


def replay_all(plan: str, workers: int = 16):
    f = runcache.CACHE / f"replay_{plan}_{runcache.data_version()}{runcache._hash_files(CODE)}.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    sig = daily.signals()
    items = [(es, nq, sig.loc[es.d] if es.d in sig.index else None) for es, nq in pairs()]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        res = [r for part in ex.map(_chunk, [(items[i::workers], plan) for i in range(workers)]) for r in part]
    res.sort(key=lambda x: x[0])
    f.write_bytes(pickle.dumps(res))
    return res


def gated(res, plan: str):
    return [(d, engine.plan_events(ev, plan)) for d, ev, _ in res]


def prices_fn():
    rth = {(d.sym, d.d): d.rth for es, nq in pairs() for d in (es, nq)}
    return lambda d, sym, T: float(rth[(sym, d)].c[max(rth[(sym, d)].idx(T) - 1, 0)])
