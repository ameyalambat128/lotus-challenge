from __future__ import annotations

import json
import logging
from pathlib import Path

from .condition_store import ConditionStore
from .corrections import CorrectionEngine
from .ingestion import ingest_batch, split_into_batches
from .monitoring import MonitoringDashboard, configure_logging
from .mcp_tools import mcp, wire

logger = logging.getLogger("fhir_conditions_manager.main")

CONDITIONS_FILE = Path(__file__).resolve().parent.parent / "conditions.json"


def run_ingestion_simulation(store: ConditionStore, dashboard: MonitoringDashboard) -> None:
    logger.info("Loading conditions from %s", CONDITIONS_FILE)
    with open(CONDITIONS_FILE) as f:
        all_conditions = json.load(f)
    logger.info("Loaded %d raw conditions", len(all_conditions))

    batch_one, batch_two = split_into_batches(all_conditions, seed=42)
    logger.info("Split into batch 1 (%d) and batch 2 (%d)", len(batch_one), len(batch_two))

    logger.info("=" * 60)
    logger.info("DAY 1 INGESTION — Batch 1 (%d conditions)", len(batch_one))
    logger.info("=" * 60)
    ingest_batch(batch_one, batch_number=1, store=store, dashboard=dashboard)
    logger.info("Store after batch 1: %d conditions", store.total_count)

    logger.info("=" * 60)
    logger.info("DAY 2 INGESTION — Batch 2 (%d conditions)", len(batch_two))
    logger.info("=" * 60)
    ingest_batch(batch_two, batch_number=2, store=store, dashboard=dashboard)
    logger.info("Store after batch 2: %d conditions", store.total_count)

    status = dashboard.get_system_status(
        store_active=store.active_count, store_removed=store.removed_count
    )
    logger.info("=" * 60)
    logger.info("INGESTION COMPLETE")
    logger.info(
        "Total: %d loaded, %d active, %d removed",
        status["total_conditions_loaded"], status["total_active"], status["total_removed"],
    )
    logger.info("Quality flags: %s", status["quality_flags_total"])
    logger.info("=" * 60)


def main() -> None:
    configure_logging()

    store = ConditionStore()
    dashboard = MonitoringDashboard()
    corrections = CorrectionEngine(store, dashboard)

    run_ingestion_simulation(store, dashboard)

    wire(store, dashboard, corrections)

    logger.info("Starting MCP server...")
    mcp.run()


if __name__ == "__main__":
    main()
