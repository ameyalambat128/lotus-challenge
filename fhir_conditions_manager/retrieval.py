from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .condition_model import ConditionRecord, QUALITY_FLAG_METADATA
from .condition_store import ConditionStore
from .monitoring import LatencyTracker, MonitoringDashboard

logger = logging.getLogger("fhir_conditions_manager.retrieval")


@dataclass
class ConditionGroup:
    canonical_code: str
    code_system_label: str
    display_name: str
    all_codes: dict[str, set[str]]
    statuses: set[str] = field(default_factory=set)
    encounter_count: int = 0
    earliest_onset: Optional[str] = None
    latest_onset: Optional[str] = None
    quality_flags: set[str] = field(default_factory=set)
    has_overlapping_dates: bool = False
    consolidated_record_count: int = 0
    derived_from_source_count: int = 0


def _canonical_code(record: ConditionRecord) -> tuple[str, str]:
    if record.icd10_codes:
        return next(iter(record.icd10_codes)), "ICD-10"
    if record.snomed_codes:
        return next(iter(record.snomed_codes)), "SNOMED"
    if record.icd9_codes:
        return next(iter(record.icd9_codes)), "ICD-9"
    return record.display_name.lower(), "text"


def _code_labels(record: ConditionRecord) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    if record.icd10_codes:
        result["ICD-10"] = set(record.icd10_codes)
    if record.snomed_codes:
        result["SNOMED"] = set(record.snomed_codes)
    if record.icd9_codes:
        result["ICD-9"] = set(record.icd9_codes)
    if record.imo_codes:
        result["IMO"] = set(record.imo_codes)
    return result


def _matches_text(record: ConditionRecord, query: str) -> bool:
    return query.lower() in record.searchable_text


def _matches_code(record: ConditionRecord, code: str, code_system: Optional[str]) -> bool:
    if code_system:
        system_map = {
            "icd10": record.icd10_codes,
            "icd-10": record.icd10_codes,
            "snomed": record.snomed_codes,
            "icd9": record.icd9_codes,
            "icd-9": record.icd9_codes,
            "imo": record.imo_codes,
        }
        target = system_map.get(code_system.lower())
        if target is not None:
            return code in target
    return code in record.all_codes


def _matches_status(record: ConditionRecord, status: str) -> bool:
    return record.normalized_status == status.lower()


def _detect_overlapping_onset_periods(
    onset_periods: list[tuple[str, str]],
) -> bool:
    sorted_periods = sorted(onset_periods, key=lambda p: p[0])
    for i in range(1, len(sorted_periods)):
        previous_end = sorted_periods[i - 1][1]
        current_start = sorted_periods[i][0]
        if current_start < previous_end:
            return True
    return False


def group_by_canonical_code(records: list[ConditionRecord]) -> list[ConditionGroup]:
    groups: dict[str, ConditionGroup] = {}
    onset_periods_by_group: dict[str, list[tuple[str, str]]] = {}

    for record in records:
        code, label = _canonical_code(record)

        if code not in groups:
            groups[code] = ConditionGroup(
                canonical_code=code,
                code_system_label=label,
                display_name=record.display_name,
                all_codes=_code_labels(record),
            )
            onset_periods_by_group[code] = []

        group = groups[code]
        group.encounter_count += 1
        group.statuses.add(record.normalized_status)
        group.quality_flags.update(record.quality_flags)

        if record.derived_from_ids:
            group.consolidated_record_count += 1
            group.derived_from_source_count += len(record.derived_from_ids)

        for sys_label, codes in _code_labels(record).items():
            group.all_codes.setdefault(sys_label, set()).update(codes)

        if record.onset_start:
            onset_str = record.onset_start.isoformat()[:10]
            if group.earliest_onset is None or onset_str < group.earliest_onset:
                group.earliest_onset = onset_str
            if group.latest_onset is None or onset_str > group.latest_onset:
                group.latest_onset = onset_str

        if record.onset_start and record.onset_end:
            onset_periods_by_group[code].append(
                (record.onset_start.isoformat(), record.onset_end.isoformat())
            )

    for code, group in groups.items():
        periods = onset_periods_by_group[code]
        if len(periods) >= 2 and _detect_overlapping_onset_periods(periods):
            group.has_overlapping_dates = True

    return sorted(groups.values(), key=lambda g: g.encounter_count, reverse=True)


def format_group_for_llm(group: ConditionGroup) -> str:
    code_parts = []
    for label, codes in sorted(group.all_codes.items()):
        code_parts.append(f"{label}: {', '.join(sorted(codes))}")
    code_str = " | ".join(code_parts)

    status_str = ", ".join(sorted(group.statuses))

    date_range = ""
    if group.earliest_onset:
        if group.latest_onset and group.earliest_onset != group.latest_onset:
            date_range = f" | {group.earliest_onset} to {group.latest_onset}"
        else:
            date_range = f" | {group.earliest_onset}"

    lines = [
        f"{group.display_name} ({code_str})",
        f"  Status: {status_str} | {group.encounter_count} encounter(s){date_range}",
    ]

    if group.has_overlapping_dates:
        lines.append("  Note: overlapping date ranges across encounters")

    if group.consolidated_record_count > 0:
        lines.append(
            f"  Note: includes {group.consolidated_record_count} consolidated "
            f"record(s) derived from {group.derived_from_source_count} source(s)"
        )

    notable_flags = [
        f for f in group.quality_flags
        if QUALITY_FLAG_METADATA.get(f, {}).get("severity") in ("high", "medium")
    ]
    if notable_flags:
        descs = [QUALITY_FLAG_METADATA[f]["description"] for f in notable_flags]
        lines.append(f"  Warning: {'; '.join(descs)}")

    return "\n".join(lines)


def retrieve(
    store: ConditionStore,
    dashboard: MonitoringDashboard,
    query: Optional[str] = None,
    code: Optional[str] = None,
    code_system: Optional[str] = None,
    status: Optional[str] = None,
    max_results: int = 20,
) -> str:
    with LatencyTracker(dashboard):
        candidates = store.get_all_active()

        if query:
            candidates = [r for r in candidates if _matches_text(r, query)]
        if code:
            candidates = [r for r in candidates if _matches_code(r, code, code_system)]
        if status:
            candidates = [r for r in candidates if _matches_status(r, status)]

        if not candidates:
            filters = []
            if query:
                filters.append(f'text="{query}"')
            if code:
                filters.append(f"code={code}")
            if status:
                filters.append(f"status={status}")
            return f"No conditions found matching: {', '.join(filters)}"

        grouped = group_by_canonical_code(candidates)
        limited = grouped[:max_results]

        sections = [format_group_for_llm(g) for g in limited]
        header = f"Found {len(candidates)} record(s) across {len(grouped)} condition(s)"
        if len(grouped) > max_results:
            header += f" (showing top {max_results})"
        header += f" — {store.active_count} active conditions total"

        logger.info(
            "Retrieval: query=%s code=%s status=%s -> %d records, %d groups",
            query, code, status, len(candidates), len(grouped),
        )

        return header + "\n\n" + "\n\n".join(sections)
