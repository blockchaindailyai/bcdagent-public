import unittest

from openclaw_agent.capabilities.verification import (
    evaluate_scenario,
    make_verification_report,
    scenario_to_evidence,
    VerificationScenario,
)


def make_valid_scenario(scenario_id: str = "B-UNIT-01") -> VerificationScenario:
    return VerificationScenario(
        scenario_id=scenario_id,
        category="unit",
        input_artifacts={"intent": "intent-1", "config": "v1"},
        execution_artifacts={"plan": "plan-1", "receipt": "receipt-1"},
        reconciliation_artifacts={"record": "recon-1"},
        approval_artifacts={"lane": "auto"},
        defects=[],
        reviewer_signoff="qa@example.com",
    )


class Phase5VerificationTests(unittest.TestCase):
    def test_scenario_to_evidence_shape(self):
        scenario = make_valid_scenario("A-UNIT-01")
        evidence = scenario_to_evidence(scenario)
        self.assertEqual(evidence["scenario_id"], "A-UNIT-01")
        self.assertEqual(evidence["result"], "pass")

    def test_evaluate_scenario_pass(self):
        result = evaluate_scenario(make_valid_scenario())
        self.assertTrue(result.passed)
        self.assertEqual(result.errors, [])

    def test_evaluate_scenario_missing_signoff_fails(self):
        scenario = make_valid_scenario()
        scenario = VerificationScenario(
            **{**scenario.__dict__, "reviewer_signoff": ""},
        )
        result = evaluate_scenario(scenario)
        self.assertFalse(result.passed)
        self.assertIn("missing_evidence_field:reviewer_signoff", result.errors)

    def test_report_rejects_when_any_scenario_fails(self):
        passing = make_valid_scenario("B-UNIT-01")
        failing = VerificationScenario(
            **{**make_valid_scenario("ADV-05").__dict__, "defects": ["approval_spoof_detected"]},
        )
        report = make_verification_report([passing, failing])
        self.assertFalse(report.accepted)
        self.assertEqual(report.failed, 1)
        self.assertIn("failed_scenarios_present", report.acceptance_errors)
        self.assertIn("ADV-05", report.failed_scenarios)

    def test_report_rejects_for_hard_acceptance_gates(self):
        scenario = make_valid_scenario("C-INT-03")
        report = make_verification_report(
            [scenario],
            unresolved_critical_defects=1,
            unaudited_transaction_paths=1,
            deterministic_replay_parity=False,
        )
        self.assertFalse(report.accepted)
        self.assertIn("unresolved_critical_defects", report.acceptance_errors)
        self.assertIn("unaudited_transaction_paths", report.acceptance_errors)
        self.assertIn("deterministic_replay_parity_failed", report.acceptance_errors)


if __name__ == "__main__":
    unittest.main()
