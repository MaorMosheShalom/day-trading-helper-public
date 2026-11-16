# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Research layer: market data as Day objects, the trade simulator, strategies and the plan registry.

days.py      one Day per CME trade date: regular-session / overnight 1-minute arrays + context known before the open
sim.py       the shared trade simulator (stop / target / time exit, costs, conservative fills)
examples.py  textbook strategies used by the public example plan
system.py    the plan registry (which strategies a plan trades, their priority) + optional private plans
evaluate.py  loading every stored trade date as (ES, NQ) pairs
"""
