from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import Any

from .condition_model import (
    ICD10_SYSTEM,
    ICD9_SYSTEM,
    IMO_SYSTEM,
    SNOMED_SYSTEM,
    ConditionRecord,
    FhirCondition,
)
from .condition_store import ConditionStore
from .monitoring import BatchMetrics, MonitoringDashboard

logger = logging.getLogger("fhir_conditions_manager.ingestion")

ADMIN_CODE_INDICATORS = {"admin code", "*new member", "prompt authorization"}

VAGUE_ENTRY_KEYWORDS = {"encounter for", "elective procedure", "initial encounter"}

CLINICAL_STATUS_NORMALIZATION = {
    "active": "active",
    "active (qualifier value)": "active",
    "resolved": "resolved",
}


def _extract_codes_by_system(condition: FhirCondition) -> dict[str, set[str]]:
    codes: dict[str, set[str]] = {
        ICD10_SYSTEM: set(),
        SNOMED_SYSTEM: set(),
        ICD9_SYSTEM: set(),
        IMO_SYSTEM: set(),
    }
    all_codes: set[str] = set()
    if condition.code:
        for coding in condition.code.coding:
            if coding.code and coding.system:
                codes.setdefault(coding.system, set()).add(coding.code)
                all_codes.add(coding.code)
    return {**codes, "_all": all_codes}


def _normalize_clinical_status(condition: FhirCondition) -> str:
    if condition.clinicalStatus is None:
        return "unknown"
    raw = (condition.clinicalStatus.text or "").strip().lower()
    return CLINICAL_STATUS_NORMALIZATION.get(raw, raw or "unknown")


def _build_searchable_text(condition: FhirCondition) -> str:
    parts: list[str] = []
    if condition.code:
        if condition.code.text:
            parts.append(condition.code.text)
        for coding in condition.code.coding:
            if coding.display:
                parts.append(coding.display)
            if coding.code:
                parts.append(coding.code)
    return " ".join(parts).lower()


def _pick_display_name(condition: FhirCondition) -> str:
    if condition.code and condition.code.text:
        return condition.code.text
    if condition.code:
        for coding in condition.code.coding:
            if coding.display:
                return coding.display
    return "Unknown condition"


def _parse_onset_dates(
    condition: FhirCondition,
) -> tuple[datetime | None, datetime | None]:
    if condition.onsetPeriod is None:
        return None, None
    start = end = None
    if condition.onsetPeriod.start:
        try:
            start = datetime.fromisoformat(condition.onsetPeriod.start)
        except ValueError:
            pass
    if condition.onsetPeriod.end:
        try:
            end = datetime.fromisoformat(condition.onsetPeriod.end)
        except ValueError:
            pass
    return start, end


def _detect_quality_flags(
    condition: FhirCondition,
    code_sets: dict[str, set[str]],
    normalized_status: str,
    onset_start: datetime | None,
    onset_end: datetime | None,
) -> list[str]:
    flags: list[str] = []
    searchable = _build_searchable_text(condition)

    if condition.clinicalStatus is None:
        flags.append("missing_clinical_status")

    has_end_date = onset_end is not None
    if normalized_status == "active" and has_end_date:
        flags.append("inconsistent_status")
    elif normalized_status == "resolved" and not has_end_date:
        flags.append("inconsistent_status")

    if any(indicator in searchable for indicator in ADMIN_CODE_INDICATORS):
        flags.append("admin_code")
    if any(keyword in searchable for keyword in VAGUE_ENTRY_KEYWORDS):
        flags.append("vague_entry")
    if onset_start and onset_end:
        duration = (onset_end - onset_start).total_seconds()
        if 0 < duration < 86400:
            flags.append("short_duration")
    if not code_sets.get(ICD10_SYSTEM):
        flags.append("missing_icd10")

    return flags


DERIVED_FROM_EXTENSION_URL = "http://hl7.org/fhir/StructureDefinition/artifact-relatedArtifact"


def _extract_derived_from_ids(condition: FhirCondition) -> list[str]:
    ids: list[str] = []
    for ext in condition.extension:
        if ext.url != DERIVED_FROM_EXTENSION_URL:
            continue
        artifact = ext.valueRelatedArtifact
        if artifact is None or artifact.type != "derived-from" or not artifact.display:
            continue
        reference = artifact.display
        if reference.startswith("Condition/"):
            ids.append(reference.removeprefix("Condition/"))
    return ids


def build_condition_record(raw: dict[str, Any], batch_number: int) -> ConditionRecord:
    condition = FhirCondition.model_validate(raw)
    code_sets = _extract_codes_by_system(condition)
    onset_start, onset_end = _parse_onset_dates(condition)
    normalized_status = _normalize_clinical_status(condition)

    return ConditionRecord(
        resource_id=condition.id,
        fhir_condition=condition,
        icd10_codes=code_sets.get(ICD10_SYSTEM, set()),
        snomed_codes=code_sets.get(SNOMED_SYSTEM, set()),
        icd9_codes=code_sets.get(ICD9_SYSTEM, set()),
        imo_codes=code_sets.get(IMO_SYSTEM, set()),
        all_codes=code_sets.get("_all", set()),
        normalized_status=normalized_status,
        searchable_text=_build_searchable_text(condition),
        display_name=_pick_display_name(condition),
        onset_start=onset_start,
        onset_end=onset_end,
        quality_flags=_detect_quality_flags(
            condition, code_sets, normalized_status, onset_start, onset_end
        ),
        derived_from_ids=_extract_derived_from_ids(condition),
        ingestion_batch=batch_number,
    )


def ingest_batch(
    raw_conditions: list[dict[str, Any]],
    batch_number: int,
    store: ConditionStore,
    dashboard: MonitoringDashboard,
) -> BatchMetrics:
    metrics = BatchMetrics(batch_number=batch_number, received=len(raw_conditions))
    flag_counts: dict[str, int] = {}

    for raw in raw_conditions:
        try:
            record = build_condition_record(raw, batch_number)
        except Exception as exc:
            logger.error("Failed to parse condition %s: %s", raw.get("id", "?"), exc)
            metrics.errored += 1
            continue

        if store.add(record):
            metrics.added += 1
            for flag in record.quality_flags:
                flag_counts[flag] = flag_counts.get(flag, 0) + 1
                logger.info("Flag [%s] on %s: %s", flag, record.resource_id, record.display_name)
        else:
            metrics.skipped_duplicate += 1
            logger.warning("Duplicate resource ID skipped: %s", record.resource_id)

    metrics.flags = flag_counts
    dashboard.record_batch(metrics)

    logger.info(
        "Batch %d complete: %d received, %d added, %d duplicates, %d errors, flags=%s",
        batch_number, metrics.received, metrics.added,
        metrics.skipped_duplicate, metrics.errored, flag_counts,
    )
    return metrics


def split_into_batches(
    all_conditions: list[dict[str, Any]], seed: int = 42
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    shuffled = list(all_conditions)
    random.Random(seed).shuffle(shuffled)
    mid = len(shuffled) // 2 + len(shuffled) % 2
    return shuffled[:mid], shuffled[mid:]
