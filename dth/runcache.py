# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Content-addressed cache keys: expensive results (built Day objects, full minute-by-minute replays) are stored in
results/cache/ under a key made from a hash of the data files and of the code that produced them, so they are
recomputed exactly when - and only when - the data or that code changes."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("DTH_CACHE_DIR", ROOT / "results" / "cache"))


def _hash_files(paths) -> str:
    h = hashlib.sha1()
    for p in sorted(paths):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


def data_version() -> str:
    from . import data
    cal = ROOT / "data" / "calendar" / "events.csv"
    return _hash_files(list(data.RAW_DIR.glob("*/*.csv")) + ([cal] if cal.exists() else []))


def code_version(files=("lab/days.py",)) -> str:
    return _hash_files([ROOT / "dth" / f for f in files])
