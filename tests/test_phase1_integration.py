import unittest

from openclaw_agent.capabilities.policy_controls import ApprovalError, ApprovalTier, DecisionIntent, Phase1PolicyEngine


class Phase1IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = Phase1PolicyEngine()

    def test_blocked_disallowed_borrow_direction(self):
        intent = DecisionIntent(
            intent_id="blocked-1",
            asset="USDD",
            action_type="borrow",
            requested_size_nav_pct=0.4,
            daily_cumulative_nav_pct=0.8,
            collateral_asset="USDD",
            borrow_asset="jUSDD",
        )
        result = self.engine.run(intent)
        self.assertFalse(result["decision"].allowed)
        self.assertEqual(result["decision"].tier, ApprovalTier.BLOCKED)
        self.assertEqual(result["receipt"].status, "blocked")
        self.assertEqual(result["audit"].execution_status, "blocked")

    def test_manual_tier_requires_approval(self):
        intent = DecisionIntent(
            intent_id="manual-1",
            asset="TRX",
            action_type="borrow",
            requested_size_nav_pct=1.8,
            daily_cumulative_nav_pct=1.2,
            collateral_asset="jTRX",
            borrow_asset="TRX",
        )
        with self.assertRaises(ApprovalError):
            self.engine.run(intent, approved_by=[])

        result = self.engine.run(intent, approved_by=["operator"])
        self.assertEqual(result["decision"].tier, ApprovalTier.MANUAL)
        self.assertEqual(result["receipt"].status, "submitted")

    def test_dual_tier_requires_two_approvers(self):
        intent = DecisionIntent(
            intent_id="dual-1",
            asset="TRX",
            action_type="borrow",
            requested_size_nav_pct=6.0,
            daily_cumulative_nav_pct=5.0,
            collateral_asset="jTRX",
            borrow_asset="TRX",
        )
        with self.assertRaises(ApprovalError):
            self.engine.run(intent, approved_by=["operator"])

        result = self.engine.run(intent, approved_by=["operator", "admin"])
        self.assertEqual(result["decision"].tier, ApprovalTier.DUAL)
        self.assertEqual(result["receipt"].status, "submitted")

    def test_reconciliation_hard_halt_recorded(self):
        intent = DecisionIntent(
            intent_id="recon-1",
            asset="TRX",
            action_type="borrow",
            requested_size_nav_pct=0.5,
            daily_cumulative_nav_pct=0.5,
            collateral_asset="jTRX",
            borrow_asset="TRX",
        )
        result = self.engine.run(intent, approved_by=[], drift_nav_pct=0.31)
        self.assertEqual(result["reconciliation"].status, "hard_halt")
        self.assertEqual(result["audit"].reconciliation_status, "hard_halt")


if __name__ == "__main__":
    unittest.main()
