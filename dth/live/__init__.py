# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Live trading: the same strategy code as the backtest, stepped one closed minute at a time.

engine.py    snapshots holding only closed bars + the Stepper that turns strategy output into entry / exit events and
             records any look-ahead; the one-trade-at-a-time gate
account.py   the prop-firm evaluation account ("case"): risk-% sizing, trailing limit, test, funded, payouts
runner.py    PaperTrader: restartable live session (state saved every minute), sends NET positions to a broker
broker.py    broker interface + paper broker
projectx.py  Topstep / ProjectX Gateway REST adapter: market entries, broker-side protective stops, reconcile
"""
