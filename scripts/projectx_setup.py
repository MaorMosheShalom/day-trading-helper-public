# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Save the Topstep API login into settings_local.txt (this PC only, never committed) and run the connection check.
Run by scripts\\projectx_setup.bat."""
import getpass
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def set_kv(path: Path, key: str, value: str):
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [l for l in lines if not l.strip().startswith(key + "=")] + [f"{key}={value}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")



f = ROOT / "settings_local.txt"
print("Topstep demo setup. The answers are saved only on this PC, in", f)
user = input("1. Your TopstepX USER NAME (not the e-mail): ").strip()
key = getpass.getpass("2. Your API key (paste it; it won't show on screen): ").strip()
if user:
    set_kv(f, "PX_USER", user)
if key:
    set_kv(f, "PX_KEY", key)
print("Checking the connection and listing your accounts ...\n")
subprocess.call([sys.executable, str(ROOT / "scripts" / "projectx_check.py")])
acc = input("\n3. Type the PRACTICE account's name exactly as listed above (Enter = skip): ").strip()
if acc:
    set_kv(f, "PX_ACCOUNT", acc)
    print(f"Saved PX_ACCOUNT={acc}. When the market is open, test an order with:")
    print("   python scripts\\projectx_check.py --test-order")
