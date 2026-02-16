from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .condition_model import ConditionRecord
from .condition_store import ConditionStore
from .monitoring import MonitoringDashboard

logger = logging.getLogger("fhir_conditions_manager.corrections")


@dataclass
class RemovalPredicate:
    name: str
    description: str
    text_patterns: list[str] = field(default_factory=list)
    icd10_codes: set[str] = field(default_factory=set)
    snomed_codes: set[str] = field(default_factory=set)
    quality_flags: list[str] = field(default_factory=list)


PREDICATES: dict[str, RemovalPredicate] = {
    "tuberculosis": RemovalPredicate(
        name="tuberculosis",
        description="All TB-related conditions (latent TB, history of TB)",
        text_patterns=["tuberculosis", "latent tb", "hx of latent tb"],
        icd10_codes={"Z22.7", "Z86.15"},
        snomed_codes={"11999007", "428934008"},
    ),
    "admin_codes": RemovalPredicate(
        name="admin_codes",
        description="Non-clinical administrative entries",
        quality_flags=["admin_code"],
    ),
}


def _matches_predicate(record: ConditionRecord, pred: RemovalPredicate) -> bool:
    for pattern in pred.text_patterns:
        if pattern in record.searchable_text:
            return True
    if pred.icd10_codes and record.icd10_codes & pred.icd10_codes:
        return True
    if pred.snomed_codes and record.snomed_codes & pred.snomed_codes:
        return True
    for flag in pred.quality_flags:
        if flag in record.quality_flags:
            return True
    return False


class CorrectionEngine:

    def __init__(self, store: ConditionStore, dashboard: MonitoringDashboard) -> None:
        self.store = store
        self.dashboard = dashboard

    def remove_by_text(self, target: str, reason: str) -> dict[str, Any]:
        target_lower = target.lower()
        matched = [r for r in self.store.get_all_active() if target_lower in r.searchable_text]
        return self._apply_removals(matched, "remove_by_text", target, reason)

    def remove_by_code(self, code: str, reason: str) -> dict[str, Any]:
        matched = [r for r in self.store.get_all_active() if code in r.all_codes]
        return self._apply_removals(matched, "remove_by_code", code, reason)

    def remove_by_id(self, resource_id: str, reason: str) -> dict[str, Any]:
        record = self.store.get_by_id(resource_id)
        if record is None:
            return self._no_match(resource_id)
        if record.is_removed:
            return self._already_removed(resource_id)
        return self._apply_removals([record], "remove_by_id", resource_id, reason)

    def remove_by_predicate(self, predicate_name: str, reason: str) -> dict[str, Any]:
        pred = PREDICATES.get(predicate_name)
        if pred is None:
            available = ", ".join(sorted(PREDICATES.keys()))
            return {
                "success": False,
                "message": f'Unknown predicate "{predicate_name}". Available: {available}',
                "records_removed": 0,
                "active_remaining": self.store.active_count,
            }

        matched = [r for r in self.store.get_all_active() if _matches_predicate(r, pred)]
        return self._apply_removals(matched, "remove_by_predicate", predicate_name, reason)

    def list_corrections(self) -> dict[str, Any]:
        return {
            "corrections": [
                {
                    "timestamp": c.timestamp,
                    "action": c.action,
                    "target": c.target,
                    "reason": c.reason,
                    "records_affected": c.records_affected,
                }
                for c in self.dashboard.corrections
            ],
            "total_corrections": len(self.dashboard.corrections),
            "total_records_removed": self.store.removed_count,
        }

    def get_status(self) -> dict[str, Any]:
        return self.dashboard.get_system_status(
            store_active=self.store.active_count,
            store_removed=self.store.removed_count,
        )

    def get_available_predicates(self) -> dict[str, str]:
        return {name: p.description for name, p in PREDICATES.items()}

    def _apply_removals(
        self,
        matched: list[ConditionRecord],
        action: str,
        target: str,
        reason: str,
    ) -> dict[str, Any]:
        if not matched:
            return self._no_match(target)

        removed: list[str] = []
        for record in matched:
            if self.store.soft_remove(record.resource_id, reason):
                removed.append(f"{record.display_name} ({record.resource_id[:8]})")
                logger.info("Removed: %s [%s] — reason: %s", record.display_name, record.resource_id, reason)

        self.dashboard.record_correction(action, target, reason, len(removed))

        return {
            "success": True,
            "message": f"Removed {len(removed)} condition(s). {self.store.active_count} active remaining.",
            "records_removed": len(removed),
            "removed_conditions": removed,
            "active_remaining": self.store.active_count,
        }

    def _no_match(self, target: str) -> dict[str, Any]:
        return {
            "success": False,
            "message": f'No active conditions found matching "{target}".',
            "records_removed": 0,
            "active_remaining": self.store.active_count,
        }

    def _already_removed(self, target: str) -> dict[str, Any]:
        return {
            "success": False,
            "message": f'Condition "{target}" is already removed.',
            "records_removed": 0,
            "active_remaining": self.store.active_count,
        }
