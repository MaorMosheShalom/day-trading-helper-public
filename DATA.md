# Market data

No market data is included in this repository: vendor terms don't allow redistribution. The tool reads 1-minute
OHLCV bars from a local store:

```
data/raw/ES_1m/<YYYY-MM>.csv      timestamp (UTC), open, high, low, close, volume
data/raw/NQ_1m/<YYYY-MM>.csv
data/calendar/events.csv          no-trade days (holidays, CPI / PPI / PCE / NFP / FOMC) - included
```

Ways to fill it:

| Source | How | Notes |
|---|---|---|
| **Synthetic** | `python scripts/make_sample_data.py` | Random walk with a realistic intraday volatility shape. For trying the tool and for the tests; it has no edge by construction. |
| **Yahoo Finance** (free) | `dth.data.collect()` | CME front-month continuous futures (`ES=F`, `NQ=F`). Yahoo serves only the last ~30 days of 1-minute bars, so run it regularly; the store only ever grows. |
| **Topstep / ProjectX** | `ProjectX.collect()` in `dth/live/projectx.py` | Real-time and recent history with an API subscription. |
| **Your own vendor** | Write the CSVs in the format above | Any source of CME 1-minute bars works. |

Set `DTH_DATA_DIR` to keep the store somewhere else (the tests use it to run on synthetic data in a temp folder).
