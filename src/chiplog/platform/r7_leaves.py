"""Registered capability-equivalent broker leaves for R7 production and evaluation."""

from __future__ import annotations

import time
from pathlib import Path


class ProductionClock:
    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


class EvaluationClock:
    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


class ProductionPlanningStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path


class EvaluationPlanningStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path


__all__ = [
    "EvaluationClock",
    "EvaluationPlanningStore",
    "ProductionClock",
    "ProductionPlanningStore",
]
