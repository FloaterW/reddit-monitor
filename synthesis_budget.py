"""Bounded workload-aware AI time with protected planning/writing headroom."""

import math
import threading
import time


class SynthesisBudget:
    def __init__(self, batches, *, maximum=2400, available=None, deadline=None, editorial=True, clock=None):
        self.clock = clock or time.monotonic
        self.started = self.clock()
        requested = max(1500, 900 + 80 * math.ceil(batches / 2))
        limits = [requested, maximum]
        if available is not None:
            limits.append(available)
        if deadline is not None:
            if not math.isfinite(deadline):
                raise ValueError("Job deadline must be finite")
            limits.append(deadline - self.started)
        self.seconds = max(0, min(limits))
        self.deadline = self.started + self.seconds
        self.batches = batches
        self.phase = "extraction"
        self.reserves = {"extraction": 1200, "review": 1020, "planning": 600, "writing": 0} if editorial else {}
        self.lock = threading.Lock()
        self.events = []

    def timeout(self, limit):
        remaining = self.deadline - self.clock() - self.reserves.get(self.phase, 0)
        if remaining <= 0:
            raise RuntimeError(f"AI {self.phase} time budget exhausted; completed checkpoints are saved. Later-stage time is reserved, not borrowed by retries.")
        return min(limit, remaining)

    def record(self, stage, outcome, seconds=0):
        with self.lock:
            self.events.append({"phase": self.phase, "stage": stage, "outcome": outcome, "seconds": round(seconds, 3)})

    def snapshot(self):
        with self.lock:
            return {"version": 1, "extraction_batches": self.batches, "budget_seconds": self.seconds,
                    "elapsed_seconds": round(self.clock()-self.started, 3), "phase": self.phase,
                    "reserved_seconds": self.reserves, "events": list(self.events)}
