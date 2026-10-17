from __future__ import annotations

from typing import Iterable, List

from .models import (
    AttestationCycle,
    AttestationDecision,
    Cadence,
    DEFAULT_OPERATIONAL_CHECKLIST,
    IncidentLearningResult,
    IncidentRecord,
    KPIEvaluation,
    KPIRecord,
)


def checklist_for_cadence(cadence: Cadence) -> List[str]:
    return list(DEFAULT_OPERATIONAL_CHECKLIST[cadence])


def evaluate_kpi(record: KPIRecord) -> KPIEvaluation:
    if record.target_min is not None and record.value < record.target_min:
        return KPIEvaluation(record.name, False, "below_min_target")
    if record.target_max is not None and record.value > record.target_max:
        return KPIEvaluation(record.name, False, "above_max_target")
    return KPIEvaluation(record.name, True, "")


def evaluate_kpi_set(records: Iterable[KPIRecord]) -> List[KPIEvaluation]:
    return [evaluate_kpi(r) for r in records]


def evaluate_attestation_cycle(cycle: AttestationCycle) -> AttestationDecision:
    reasons: list[str] = []

    if not cycle.cycle_id:
        reasons.append("missing_cycle_id")
    if not cycle.evidence_bundle_ref:
        reasons.append("missing_evidence_bundle")
    if not cycle.findings_delta_ref:
        reasons.append("missing_findings_delta")
    if not cycle.remediation_status_ref:
        reasons.append("missing_remediation_status")
    if not cycle.autonomy_scope_statement:
        reasons.append("missing_autonomy_scope_statement")

    required_signoffs = {"engineering", "security", "compliance"}
    missing_signoffs = sorted(required_signoffs - set(cycle.signoffs))
    if missing_signoffs:
        reasons.append(f"missing_signoffs:{','.join(missing_signoffs)}")

    return AttestationDecision(passed=not reasons, reasons=reasons)


def evaluate_incident_learning(incident: IncidentRecord) -> IncidentLearningResult:
    missing: list[str] = []

    if incident.severity.lower() not in {"medium", "high", "critical"}:
        return IncidentLearningResult(incident.incident_id, True, [])

    if not incident.has_root_cause:
        missing.append("root_cause_analysis")
    if not incident.has_control_mapping:
        missing.append("control_mapping")
    if not incident.has_owner_and_deadline:
        missing.append("owner_and_deadline")
    if not incident.has_regression_update:
        missing.append("regression_update")
    if not incident.findings_linked:
        missing.append("findings_register_link")

    return IncidentLearningResult(incident.incident_id, complete=not missing, missing=missing)
