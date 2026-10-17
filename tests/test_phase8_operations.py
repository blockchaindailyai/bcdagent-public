import unittest

from openclaw_agent.capabilities.operations import (
    AttestationCycle,
    Cadence,
    IncidentRecord,
    KPIRecord,
    checklist_for_cadence,
    evaluate_attestation_cycle,
    evaluate_incident_learning,
    evaluate_kpi,
    evaluate_kpi_set,
)


class Phase8OperationsTests(unittest.TestCase):
    def test_checklist_for_cadence(self):
        daily = checklist_for_cadence(Cadence.DAILY)
        self.assertIn("review_failed_or_aborted_actions", daily)
        self.assertIn("confirm_no_unauthorized_config_drift", daily)

    def test_kpi_evaluation_bounds(self):
        ok = evaluate_kpi(KPIRecord("policy_bypass_count", 0, target_min=0, target_max=0))
        self.assertTrue(ok.compliant)

        high = evaluate_kpi(KPIRecord("mttd", 30, target_max=20))
        self.assertFalse(high.compliant)
        self.assertEqual(high.reason, "above_max_target")

        low = evaluate_kpi(KPIRecord("approval_accuracy", 98, target_min=100))
        self.assertFalse(low.compliant)
        self.assertEqual(low.reason, "below_min_target")

    def test_kpi_set_evaluation(self):
        results = evaluate_kpi_set(
            [
                KPIRecord("policy_bypass_count", 0, target_max=0),
                KPIRecord("deny_failures", 0, target_max=0),
            ]
        )
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.compliant for r in results))

    def test_attestation_cycle_pass_and_fail(self):
        cycle = AttestationCycle(
            cycle_id="monthly-2026-03",
            cadence=Cadence.MONTHLY,
            evidence_bundle_ref="evidence://bundle/123",
            findings_delta_ref="findings://delta/123",
            remediation_status_ref="remediation://status/123",
            signoffs=["engineering", "security", "compliance"],
            autonomy_scope_statement="unchanged",
        )
        decision = evaluate_attestation_cycle(cycle)
        self.assertTrue(decision.passed)

        bad = AttestationCycle(
            cycle_id="",
            cadence=Cadence.MONTHLY,
            evidence_bundle_ref="",
            findings_delta_ref="",
            remediation_status_ref="",
            signoffs=["engineering"],
            autonomy_scope_statement="",
        )
        bad_decision = evaluate_attestation_cycle(bad)
        self.assertFalse(bad_decision.passed)
        self.assertIn("missing_cycle_id", bad_decision.reasons)
        self.assertIn("missing_signoffs:compliance,security", bad_decision.reasons)

    def test_incident_learning_requirements(self):
        incident = IncidentRecord(
            incident_id="inc-1",
            severity="high",
            has_root_cause=True,
            has_control_mapping=True,
            has_owner_and_deadline=True,
            has_regression_update=True,
            findings_linked=True,
        )
        result = evaluate_incident_learning(incident)
        self.assertTrue(result.complete)

        incomplete = IncidentRecord(
            incident_id="inc-2",
            severity="critical",
            has_root_cause=False,
            has_control_mapping=False,
            has_owner_and_deadline=False,
            has_regression_update=False,
            findings_linked=False,
        )
        failed = evaluate_incident_learning(incomplete)
        self.assertFalse(failed.complete)
        self.assertIn("root_cause_analysis", failed.missing)
        self.assertIn("control_mapping", failed.missing)
        self.assertIn("owner_and_deadline", failed.missing)
        self.assertIn("regression_update", failed.missing)
        self.assertIn("findings_register_link", failed.missing)


if __name__ == "__main__":
    unittest.main()
