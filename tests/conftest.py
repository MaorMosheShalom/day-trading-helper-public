# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Every test runs on SYNTHETIC data (scripts/make_sample_data.py) in a temporary folder - never on real data."""
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="dth_test_"))
os.environ["DTH_DATA_DIR"] = str(_TMP / "raw")
os.environ["DTH_CACHE_DIR"] = str(_TMP / "cache")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import make_sample_data  # noqa: E402

make_sample_data.make(Path(os.environ["DTH_DATA_DIR"]), start="2025-03-03", days=40)
