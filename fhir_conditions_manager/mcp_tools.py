from __future__ import annotations

import json
from typing import Optional

from fastmcp import FastMCP

from .condition_store import ConditionStore
from .corrections import CorrectionEngine
from .monitoring import MonitoringDashboard
from .retrieval import retrieve

mcp = FastMCP("FHIR Conditions Manager")

_store: ConditionStore
_dashboard: MonitoringDashboard
_corrections: CorrectionEngine


def wire(
    store: ConditionStore,
    dashboard: MonitoringDashboard,
    corrections: CorrectionEngine,
) -> None:
    global _store, _dashboard, _corrections
    _store = store
    _dashboard = dashboard
    _corrections = corrections


@mcp.tool
def retrieve_conditions(
    query: Optional[str] = None,
    code: Optional[str] = None,
    code_system: Optional[str] = None,
    status: Optional[str] = None,
    max_results: int = 20,
) -> str:
    """Search the patient's conditions by text, medical code, and/or clinical status.

    Use this to answer questions like "do I have sleep apnea?", "what are my active
    conditions?", or to look up a specific ICD-10/SNOMED code.

    Args:
        query: Free text search (e.g. "sleep apnea", "back pain"). Matches against
               condition names, code displays, and code values.
        code: Exact medical code lookup (e.g. "M48.061" for ICD-10, "11999007" for SNOMED).
        code_system: Narrow code search to a specific system: "icd10", "snomed", "icd9", "imo".
        status: Filter by clinical status: "active", "resolved", or "unknown".
        max_results: Maximum number of grouped conditions to return (default 20).

    Returns:
        Formatted text summary of matching conditions, grouped by canonical code,
        with encounter counts and date ranges.
    """
    return retrieve(
        store=_store,
        dashboard=_dashboard,
        query=query,
        code=code,
        code_system=code_system,
        status=status,
        max_results=max_results,
    )


@mcp.tool
def correct_conditions(
    action: str,
    target: Optional[str] = None,
    code: Optional[str] = None,
    resource_id: Optional[str] = None,
    reason: str = "User correction",
) -> str:
    """Correct the patient's condition records by removing entries or viewing system state.

    Actions:
        remove_text: Remove conditions matching a text search (target required).
        remove_code: Remove conditions matching a medical code (code required).
        remove_id: Remove a specific condition by resource ID (resource_id required).
        remove_predicate: Remove conditions matching a named category like "tuberculosis"
                          or "admin_codes" (target required). Uses hybrid matching
                          combining text patterns and medical codes.
        list_corrections: Show all past corrections with timestamps and reasons.
        list_predicates: Show available named predicates for remove_predicate.
        status: Show full system status including ingestion metrics and quality flags.

    Args:
        action: One of the actions listed above.
        target: Text pattern or predicate name (for remove_text, remove_predicate).
        code: Medical code to match (for remove_code).
        resource_id: Specific FHIR resource UUID (for remove_id).
        reason: Explanation for the correction (recorded in audit trail).

    Returns:
        JSON summary of what was removed or current system state.
    """
    if action == "remove_text":
        if not target:
            return "Error: 'target' is required for remove_text action."
        result = _corrections.remove_by_text(target, reason)
    elif action == "remove_code":
        if not code:
            return "Error: 'code' is required for remove_code action."
        result = _corrections.remove_by_code(code, reason)
    elif action == "remove_id":
        if not resource_id:
            return "Error: 'resource_id' is required for remove_id action."
        result = _corrections.remove_by_id(resource_id, reason)
    elif action == "remove_predicate":
        if not target:
            return "Error: 'target' (predicate name) is required for remove_predicate action."
        result = _corrections.remove_by_predicate(target, reason)
    elif action == "list_corrections":
        result = _corrections.list_corrections()
    elif action == "list_predicates":
        result = _corrections.get_available_predicates()
    elif action == "status":
        result = _corrections.get_status()
    else:
        return (
            f"Unknown action: '{action}'. "
            "Valid actions: remove_text, remove_code, remove_id, remove_predicate, "
            "list_corrections, list_predicates, status"
        )

    return json.dumps(result, indent=2, default=str)
