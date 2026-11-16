# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
"""Broker interface. The trader only ever asks for a target NET position per symbol (in micros).

PaperBroker logs the orders that would reach the market (reports/paper_orders.csv). A real broker adapter
(dth/live/projectx.py ProjectXBroker for Topstep) implements the same two methods - nothing else changes.
"""
from __future__ import annotations

import csv
from pathlib import Path

MICRO = {"ES": "MES", "NQ": "MNQ"}


class Broker:
    def position(self, sym: str) -> int:
        raise NotImplementedError

    def set_position(self, sym: str, target: int, when: str, why: str, stop: float | None = None) -> None:
        """stop: the protective stop price for the open trade (a real broker keeps a stop order there)."""
        raise NotImplementedError


class PaperBroker(Broker):
    def __init__(self, log_path: Path, positions: dict | None = None):
        self.log_path = Path(log_path)
        self.positions = dict(positions or {})

    def position(self, sym: str) -> int:
        return self.positions.get(sym, 0)

    def set_position(self, sym: str, target: int, when: str, why: str, stop: float | None = None) -> None:
        qty = target - self.position(sym)
        if qty == 0:
            return
        new = not self.log_path.exists()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "contract", "action", "qty", "net after", "why"])
            w.writerow([when, MICRO[sym], "BUY" if qty > 0 else "SELL", abs(qty), target, why])
        self.positions[sym] = target
