import unittest

from openclaw_agent.capabilities.rollout import (
    RolloutStage,
    RolloutStatus,
    evaluate_rollback,
    evaluate_stage_promotion,
    get_stage_guardrail,
)


def clean_status(stage: RolloutStage) -> RolloutStatus:
    return RolloutStatus(
        stage=stage,
        unresolved_critical_findings=0,
        unresolved_high_incidents=0,
        policy_bypass_incidents=0,
        reconciliation_hard_breaches=0,
        repeated_soft_breach_escalations=0,
        missing_manual_dual_approval_evidence=0,
        deterministic_deny_failures=0,
    )


class Phase7RolloutTests(unittest.TestCase):
    def test_stage_guardrails_match_plan_baseline(self):
        g1 = get_stage_guardrail(RolloutStage.STAGE_1_TESTNET_PILOT)
        self.assertEqual(g1.max_action_nav_pct, 0.5)
        self.assertEqual(g1.max_daily_nav_pct, 1.5)
        self.assertFalse(g1.autonomous_execution_enabled)

        g3 = get_stage_guardrail(RolloutStage.STAGE_3_LIMITED_AUTONOMY)
        self.assertEqual(g3.max_action_nav_pct, 1.0)
        self.assertEqual(g3.max_daily_nav_pct, 3.0)
        self.assertTrue(g3.autonomous_execution_enabled)

    def test_rollback_on_policy_bypass(self):
        status = clean_status(RolloutStage.STAGE_3_LIMITED_AUTONOMY)
        status = RolloutStatus(**{**status.__dict__, "policy_bypass_incidents": 1})
        rollback = evaluate_rollback(status)
        self.assertTrue(rollback.rollback_required)
        self.assertIn("policy_bypass_incident", rollback.reasons)

    def test_rollback_on_deterministic_deny_failure(self):
        status = clean_status(RolloutStage.STAGE_3_LIMITED_AUTONOMY)
        status = RolloutStatus(**{**status.__dict__, "deterministic_deny_failures": 1})
        rollback = evaluate_rollback(status)
        self.assertTrue(rollback.rollback_required)
        self.assertIn("deterministic_deny_failure", rollback.reasons)

    def test_stage2_holds_when_parity_or_sla_fails(self):
        status = clean_status(RolloutStage.STAGE_2_MAINNET_SHADOW)
        decision = evaluate_stage_promotion(
            RolloutStage.STAGE_2_MAINNET_SHADOW,
            status,
            decision_execution_parity_ok=False,
            approval_sla_ok=False,
        )
        self.assertEqual(decision.outcome, "hold_and_remediate")
        self.assertIn("decision_execution_parity_below_threshold", decision.reasons)
        self.assertIn("approval_sla_outside_target", decision.reasons)

    def test_stage3_holds_on_unresolved_high_incident(self):
        status = clean_status(RolloutStage.STAGE_3_LIMITED_AUTONOMY)
        status = RolloutStatus(**{**status.__dict__, "unresolved_high_incidents": 1})
        decision = evaluate_stage_promotion(RolloutStage.STAGE_3_LIMITED_AUTONOMY, status)
        self.assertEqual(decision.outcome, "hold_and_remediate")
        self.assertIn("unresolved_high_incidents", decision.reasons)

    def test_stage3_promotes_on_clean_status(self):
        status = clean_status(RolloutStage.STAGE_3_LIMITED_AUTONOMY)
        decision = evaluate_stage_promotion(RolloutStage.STAGE_3_LIMITED_AUTONOMY, status)
        self.assertEqual(decision.outcome, "promote")
        self.assertEqual(decision.reasons, [])

    def test_stage4_requires_stability_and_residual_risk_refresh(self):
        status = clean_status(RolloutStage.STAGE_4_SCALED_AUTONOMY)
        decision = evaluate_stage_promotion(
            RolloutStage.STAGE_4_SCALED_AUTONOMY,
            status,
            sustained_stability_ok=False,
            residual_risk_reapproved=False,
        )
        self.assertEqual(decision.outcome, "hold_and_remediate")
        self.assertIn("stability_window_not_met", decision.reasons)
        self.assertIn("residual_risk_not_reapproved", decision.reasons)


if __name__ == "__main__":
    unittest.main()
