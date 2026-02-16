from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Any


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    )
    root = logging.getLogger("fhir_conditions_manager")
    root.setLevel(logging.INFO)
    root.addHandler(handler)


@dataclass
class BatchMetrics:
    batch_number: int
    received: int = 0
    added: int = 0
    skipped_duplicate: int = 0
    errored: int = 0
    flags: dict[str, int] = field(default_factory=dict)


@dataclass
class CorrectionEntry:
    timestamp: str
    action: str
    target: str
    reason: str
    records_affected: int


class MonitoringDashboard:

    def __init__(self) -> None:
        self.ingestion_batches: list[BatchMetrics] = []
        self.quality_flags_total: dict[str, int] = {}
        self.corrections: list[CorrectionEntry] = []
        self.retrieval_latency_samples: list[float] = []

    def record_batch(self, metrics: BatchMetrics) -> None:
        self.ingestion_batches.append(metrics)
        for flag_name, count in metrics.flags.items():
            self.quality_flags_total[flag_name] = (
                self.quality_flags_total.get(flag_name, 0) + count
            )

    def record_correction(
        self, action: str, target: str, reason: str, records_affected: int
    ) -> None:
        from datetime import datetime, timezone

        self.corrections.append(
            CorrectionEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                action=action,
                target=target,
                reason=reason,
                records_affected=records_affected,
            )
        )

    def record_retrieval_latency(self, latency_ms: float) -> None:
        self.retrieval_latency_samples.append(latency_ms)

    def get_system_status(self, store_active: int, store_removed: int) -> dict[str, Any]:
        total_loaded = sum(b.received for b in self.ingestion_batches)

        avg_latency_ms = 0.0
        if self.retrieval_latency_samples:
            avg_latency_ms = sum(self.retrieval_latency_samples) / len(
                self.retrieval_latency_samples
            )

        return {
            "total_conditions_loaded": total_loaded,
            "total_active": store_active,
            "total_removed": store_removed,
            "ingestion_batches": [
                {
                    "batch": b.batch_number,
                    "received": b.received,
                    "added": b.added,
                    "skipped_duplicate": b.skipped_duplicate,
                    "errored": b.errored,
                    "flags": b.flags,
                }
                for b in self.ingestion_batches
            ],
            "quality_flags_total": dict(self.quality_flags_total),
            "corrections_applied": len(self.corrections),
            "conditions_removed": sum(c.records_affected for c in self.corrections),
            "avg_retrieval_latency_ms": round(avg_latency_ms, 2),
        }


class LatencyTracker:

    def __init__(self, dashboard: MonitoringDashboard) -> None:
        self.dashboard = dashboard
        self._start: float = 0.0

    def __enter__(self) -> LatencyTracker:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_: Any) -> None:
        elapsed_ms = (time.perf_counter() - self._start) * 1000
        self.dashboard.record_retrieval_latency(elapsed_ms)
