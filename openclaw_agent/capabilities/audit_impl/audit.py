from __future__ import annotations

from collections import Counter
from typing import Iterable, List, Sequence

from openclaw_agent.capabilities.verification import VerificationReport

from .models import AuditFinding, FindingSeverity, FindingStatus, FindingsSummary, ReleaseGateDecision


def validate_finding(finding: AuditFinding) -> List[str]:
    errors: List[str] = []
    if not finding.finding_id:
        errors.append("missing_finding_id")
    if not finding.track:
        errors.append("missing_track")
    if not finding.description:
        errors.append("missing_description")
    if not finding.evidence_refs:
        errors.append("missing_evidence_refs")
    if not finding.owner:
        errors.append("missing_owner")
    if not finding.target_fix_date:
        errors.append("missing_target_fix_date")
    if finding.status == FindingStatus.ACCEPTED and not finding.residual_risk_statement:
        errors.append("accepted_missing_residual_risk_statement")
    return errors


def summarize_findings(findings: Iterable[AuditFinding]) -> FindingsSummary:
    findings_list = list(findings)
    open_findings = [f for f in findings_list if f.status in {FindingStatus.OPEN, FindingStatus.IN_PROGRESS}]
    open_by_severity = Counter(f.severity.value for f in open_findings)

    critical_open = sum(1 for f in open_findings if f.severity == FindingSeverity.CRITICAL)
    high_open = sum(1 for f in open_findings if f.severity == FindingSeverity.HIGH)
    high_accepted = sum(
        1
        for f in findings_list
        if f.severity == FindingSeverity.HIGH and f.status == FindingStatus.ACCEPTED
    )

    return FindingsSummary(
        total=len(findings_list),
        critical_open=critical_open,
        high_open=high_open,
        high_accepted=high_accepted,
        open_by_severity=dict(open_by_severity),
    )


def evaluate_release_gates(
    findings: Sequence[AuditFinding],
    verification_report: VerificationReport,
    *,
    signoffs: Sequence[str],
    deterministic_borrow_deny_validated: bool,
    reconciliation_hard_halt_verified: bool,
) -> ReleaseGateDecision:
    reasons: List[str] = []

    # Findings schema must be valid before gate evaluation.
    for finding in findings:
        validation_errors = validate_finding(finding)
        if validation_errors:
            reasons.append(f"invalid_finding:{finding.finding_id}:{','.join(validation_errors)}")

    summary = summarize_findings(findings)
    if summary.critical_open > 0:
        reasons.append("unresolved_critical_findings")
    if summary.high_open > 0:
        reasons.append("unresolved_high_findings")

    if not verification_report.accepted:
        reasons.append("phase5_verification_not_accepted")

    if not deterministic_borrow_deny_validated:
        reasons.append("borrow_directionality_deny_not_validated")

    if not reconciliation_hard_halt_verified:
        reasons.append("reconciliation_hard_halt_not_verified")

    required_signoffs = {"engineering", "security", "compliance"}
    missing = sorted(required_signoffs - set(signoffs))
    if missing:
        reasons.append(f"missing_signoffs:{','.join(missing)}")

    return ReleaseGateDecision(allowed=not reasons, reasons=reasons)
