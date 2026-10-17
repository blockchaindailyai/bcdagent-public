import unittest

from openclaw_agent.capabilities.policy_controls import ApprovalTier, DecisionIntent, evaluate_intent
from openclaw_agent.capabilities.execution_adapter import (
    JustLendAdapter,
    make_execution_plan,
    plan_to_dict,
    to_policy_decision_v2,
    validate_plan_shape,
)


class Phase2IntegrationSpecTests(unittest.TestCase):
    def test_translate_policy_decision(self):
        intent = DecisionIntent(
            intent_id="p2-1",
            asset="TRX",
            action_type="borrow",
            requested_size_nav_pct=0.5,
            daily_cumulative_nav_pct=1.0,
            collateral_asset="jTRX",
            borrow_asset="TRX",
        )
        d = evaluate_intent(intent)
        dv2 = to_policy_decision_v2(intent, d)
        self.assertTrue(dv2.allowed)
        self.assertEqual(dv2.approval_tier, ApprovalTier.AUTO)
        self.assertEqual(dv2.intent_id, "p2-1")

    def test_cannot_build_plan_for_disallowed(self):
        intent = DecisionIntent(
            intent_id="p2-2",
            asset="USDD",
            action_type="borrow",
            requested_size_nav_pct=0.2,
            daily_cumulative_nav_pct=0.2,
            collateral_asset="USDD",
            borrow_asset="jUSDD",
        )
        d = evaluate_intent(intent)
        dv2 = to_policy_decision_v2(intent, d)
        self.assertFalse(dv2.allowed)
        with self.assertRaises(ValueError):
            make_execution_plan(intent, dv2, now_epoch_s=1_700_000_000)

    def test_plan_shape_and_adapter_simulate(self):
        intent = DecisionIntent(
            intent_id="p2-3",
            asset="TRX",
            action_type="borrow",
            requested_size_nav_pct=1.5,
            daily_cumulative_nav_pct=1.6,
            collateral_asset="jTRX",
            borrow_asset="TRX",
        )
        d = evaluate_intent(intent)
        dv2 = to_policy_decision_v2(intent, d)
        plan = make_execution_plan(intent, dv2, now_epoch_s=1_700_000_000)
        validate_plan_shape(plan)

        payload = plan_to_dict(plan)
        self.assertEqual(payload["schema"], "execution_plan_v2")

        adapter = JustLendAdapter()
        sim = adapter.simulate(plan)
        self.assertEqual(sim.status, "simulated")

    def test_method_allowlist_validation(self):
        adapter = JustLendAdapter()
        self.assertTrue(adapter.validate_method("borrow").allowed)
        invalid = adapter.validate_method("flashloan")
        self.assertFalse(invalid.allowed)
        self.assertIn("method_not_allowlisted", invalid.reason)


if __name__ == "__main__":
    unittest.main()
