from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


QUALITY_FLAG_METADATA: dict[str, dict[str, str]] = {
    "admin_code":              {"severity": "high",   "description": "Non-clinical admin entry"},
    "vague_entry":             {"severity": "high",   "description": "Encounter/procedure code, not a condition"},
    "missing_clinical_status": {"severity": "medium", "description": "No active/resolved status provided"},
    "inconsistent_status":     {"severity": "medium", "description": "End date vs. clinical status mismatch"},
    "short_duration":          {"severity": "low",    "description": "Condition duration under 24 hours"},
    "missing_icd10":           {"severity": "low",    "description": "No ICD-10 code, SNOMED only"},
}


class FhirCoding(BaseModel):
    system: Optional[str] = None
    code: Optional[str] = None
    display: Optional[str] = None


class FhirCodeableConcept(BaseModel):
    text: Optional[str] = None
    coding: list[FhirCoding] = []


class FhirPeriod(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None


class FhirReference(BaseModel):
    reference: Optional[str] = None


class FhirIdentifier(BaseModel):
    system: Optional[str] = None
    value: Optional[str] = None


class FhirRelatedArtifact(BaseModel):
    type: Optional[str] = None
    display: Optional[str] = None


class FhirExtension(BaseModel):
    url: Optional[str] = None
    valueRelatedArtifact: Optional[FhirRelatedArtifact] = None


class FhirCondition(BaseModel):
    resourceType: str = "Condition"
    id: str
    identifier: list[FhirIdentifier] = []
    clinicalStatus: Optional[FhirCodeableConcept] = None
    code: Optional[FhirCodeableConcept] = None
    onsetPeriod: Optional[FhirPeriod] = None
    category: list[FhirCodeableConcept] = []
    subject: Optional[FhirReference] = None
    recorder: Optional[FhirReference] = None
    extension: list[FhirExtension] = []


ICD10_SYSTEM = "http://hl7.org/fhir/sid/icd-10-cm"
SNOMED_SYSTEM = "http://snomed.info/sct"
ICD9_SYSTEM = "http://terminology.hl7.org/CodeSystem/ICD-9CM-diagnosiscodes"
IMO_SYSTEM = "http://terminology.hl7.org/CodeSystem-IMO.html"


@dataclass
class ConditionRecord:
    resource_id: str
    fhir_condition: FhirCondition

    icd10_codes: set[str] = field(default_factory=set)
    snomed_codes: set[str] = field(default_factory=set)
    icd9_codes: set[str] = field(default_factory=set)
    imo_codes: set[str] = field(default_factory=set)
    all_codes: set[str] = field(default_factory=set)

    normalized_status: str = "unknown"
    searchable_text: str = ""
    display_name: str = ""

    onset_start: Optional[datetime] = None
    onset_end: Optional[datetime] = None

    quality_flags: list[str] = field(default_factory=list)

    derived_from_ids: list[str] = field(default_factory=list)

    is_removed: bool = False
    removal_reason: Optional[str] = None
    removal_timestamp: Optional[datetime] = None

    ingestion_batch: Optional[int] = None
