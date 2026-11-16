# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""DAILY JOB (scheduled by scripts/install_daily_pictures_task.bat): after the US close,
  1. download the newest ES/NQ 1-minute bars (+ any daily data the plans need) from Yahoo (free) into the project store;
  2. redraw the LIVE picture book from the demo's OWN paper cases (scripts/trade_book.py --live): anything the
     demo didn't draw itself during the day (it draws an OPEN picture at every entry and the WIN / LOSS picture at
     every exit), and older trades are backfilled by replaying their day from the store;
  3. copy the LIVE book to the shared Google Drive folder (settings_local.txt SHARE_TO=...), so both PCs see it.
A log is written to reports/daily_pictures.log.  Usage: python scripts/daily_pictures.py
"""
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
LOG = ROOT / "reports" / "daily_pictures.log"


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    from dth import data
    from dth.lab import daily
    log("=== daily pictures job started ===")
    try:
        added = data.collect(verbose=False)
        log(f"Yahoo futures update: {added}")
    except Exception as e:
        log(f"Yahoo futures update FAILED ({type(e).__name__}: {e}) - using the stored data")
    try:
        bad = daily.download()
        log(f"Yahoo daily update ok{'' if not bad else f' (failed: {bad})'}")
    except Exception as e:
        log(f"Yahoo daily update FAILED ({type(e).__name__}: {e})")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "trade_book.py"), "--live"], cwd=ROOT,
                       capture_output=True, text=True)
    for line in (r.stdout + r.stderr).splitlines()[-25:]:
        log("  " + line)
    log(f"=== done (exit {r.returncode}) ===")
    return r.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("CRASH:\n" + traceback.format_exc())
        sys.exit(1)
