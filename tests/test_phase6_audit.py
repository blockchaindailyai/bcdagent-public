import unittest

from openclaw_agent.capabilities.verification import VerificationScenario, make_verification_report
from openclaw_agent.capabilities.audit import (
    AuditFinding,
    FindingSeverity,
    FindingStatus,
    evaluate_release_gates,
    summarize_findings,
    validate_finding,
)


def valid_verification_report():
    scenario = VerificationScenario(
        scenario_id="C-INT-03",
        category="integration",
        input_artifacts={"intent": "i1", "config": "v1"},
        execution_artifacts={"plan": "p1", "receipt": "r1"},
        reconciliation_artifacts={"record": "rec1"},
        approval_artifacts={"approver": "op1"},
        defects=[],
        reviewer_signoff="qa@example.com",
    )
    return make_verification_report([scenario])


def make_finding(
    finding_id: str,
    severity: FindingSeverity,
    status: FindingStatus,
    *,
    residual_risk_statement: str = "",
):
    return AuditFinding(
        finding_id=finding_id,
        track="contract",
        severity=severity,
        description="test finding",
        evidence_refs=["A-UNIT-02"],
        owner="security",
        target_fix_date="2026-03-31",
        status=status,
        residual_risk_statement=residual_risk_statement,
    )


class Phase6AuditTests(unittest.TestCase):
    def test_validate_finding_requires_residual_risk_for_accepted(self):
        finding = make_finding("F-1", FindingSeverity.HIGH, FindingStatus.ACCEPTED)
        errors = validate_finding(finding)
        self.assertIn("accepted_missing_residual_risk_statement", errors)

    def test_summarize_findings_counts_open_and_accepted(self):
        findings = [
            make_finding("F-1", FindingSeverity.CRITICAL, FindingStatus.OPEN),
            make_finding("F-2", FindingSeverity.HIGH, FindingStatus.IN_PROGRESS),
            make_finding(
                "F-3",
                FindingSeverity.HIGH,
                FindingStatus.ACCEPTED,
                residual_risk_statement="accepted with guardrail",
            ),
        ]
        summary = summarize_findings(findings)
        self.assertEqual(summary.total, 3)
        self.assertEqual(summary.critical_open, 1)
        self.assertEqual(summary.high_open, 1)
        self.assertEqual(summary.high_accepted, 1)

    def test_release_gate_allows_with_clean_inputs(self):
        findings = [
            make_finding("F-1", FindingSeverity.MEDIUM, FindingStatus.MITIGATED),
            make_finding(
                "F-2",
                FindingSeverity.HIGH,
                FindingStatus.ACCEPTED,
                residual_risk_statement="accepted due to compensating controls",
            ),
        ]
        decision = evaluate_release_gates(
            findings,
            valid_verification_report(),
            signoffs=["engineering", "security", "compliance"],
            deterministic_borrow_deny_validated=True,
            reconciliation_hard_halt_verified=True,
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reasons, [])

    def test_release_gate_blocks_on_unresolved_critical_and_missing_signoff(self):
        findings = [
            make_finding("F-1", FindingSeverity.CRITICAL, FindingStatus.OPEN),
        ]
        decision = evaluate_release_gates(
            findings,
            valid_verification_report(),
            signoffs=["engineering", "security"],
            deterministic_borrow_deny_validated=True,
            reconciliation_hard_halt_verified=True,
        )
        self.assertFalse(decision.allowed)
        self.assertIn("unresolved_critical_findings", decision.reasons)
        self.assertIn("missing_signoffs:compliance", decision.reasons)

    def test_release_gate_blocks_when_phase5_not_accepted(self):
        scenario = VerificationScenario(
            scenario_id="ADV-05",
            category="adversarial",
            input_artifacts={"intent": "i1"},
            execution_artifacts={"plan": "p1"},
            reconciliation_artifacts={"record": "r1"},
            approval_artifacts={"approver": "op1"},
            defects=["approval_spoof_detected"],
            reviewer_signoff="qa@example.com",
        )
        failed_report = make_verification_report([scenario])
        decision = evaluate_release_gates(
            [],
            failed_report,
            signoffs=["engineering", "security", "compliance"],
            deterministic_borrow_deny_validated=False,
            reconciliation_hard_halt_verified=False,
        )
        self.assertFalse(decision.allowed)
        self.assertIn("phase5_verification_not_accepted", decision.reasons)
        self.assertIn("borrow_directionality_deny_not_validated", decision.reasons)
        self.assertIn("reconciliation_hard_halt_not_verified", decision.reasons)


if __name__ == "__main__":
    unittest.main()
