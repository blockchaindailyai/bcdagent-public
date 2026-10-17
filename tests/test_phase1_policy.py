import unittest

from openclaw_agent.capabilities.policy_controls import (
    ApprovalTier,
    DecisionIntent,
    approval_tier,
    evaluate_intent,
    is_borrow_pair_allowed,
    reconciliation_status,
)


class Phase1PolicyTests(unittest.TestCase):
    def test_allowed_borrow_pairs(self):
        self.assertTrue(is_borrow_pair_allowed("jsTRX", "TRX"))
        self.assertTrue(is_borrow_pair_allowed("jsTRX", "sTRX"))
        self.assertTrue(is_borrow_pair_allowed("jUSDD", "USDD"))
        self.assertTrue(is_borrow_pair_allowed("jUSDT", "USDT"))
        self.assertTrue(is_borrow_pair_allowed("jsUSDD", "USDD"))
        self.assertTrue(is_borrow_pair_allowed("jsUSDD", "USDT"))
        self.assertTrue(is_borrow_pair_allowed("jsUSDD", "sUSDD"))

    def test_denied_borrow_pairs(self):
        self.assertFalse(is_borrow_pair_allowed("TRX", "jsTRX"))
        self.assertFalse(is_borrow_pair_allowed("USDD", "jUSDD"))
        self.assertFalse(is_borrow_pair_allowed("USDT", "jUSDD"))

    def test_approval_tier_boundaries(self):
        self.assertEqual(approval_tier(1.5, 4.0), ApprovalTier.AUTO)
        self.assertEqual(approval_tier(1.6, 4.0), ApprovalTier.MANUAL)
        self.assertEqual(approval_tier(6.0, 0.5), ApprovalTier.DUAL)
        self.assertEqual(approval_tier(0.5, 0.5, degraded_data=True), ApprovalTier.MANUAL)

    def test_reconciliation_states(self):
        self.assertEqual(reconciliation_status(0.05, 0), "ok")
        self.assertEqual(reconciliation_status(0.11, 0), "soft_breach")
        self.assertEqual(reconciliation_status(0.31, 0), "hard_halt")
        self.assertEqual(reconciliation_status(0.05, 3), "hard_halt")

    def test_evaluate_intent_blocks_disallowed_borrow_direction(self):
        intent = DecisionIntent(
            intent_id="i1",
            asset="USDD",
            action_type="borrow",
            requested_size_nav_pct=0.4,
            daily_cumulative_nav_pct=0.7,
            collateral_asset="USDD",
            borrow_asset="sUSDD",
        )
        decision = evaluate_intent(intent)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.tier, ApprovalTier.BLOCKED)
        self.assertIn("borrow_pair_denied:USDD->SUSDD", decision.reasons)

    def test_evaluate_intent_auto_for_allowed_small_borrow(self):
        intent = DecisionIntent(
            intent_id="i2",
            asset="TRX",
            action_type="borrow",
            requested_size_nav_pct=0.5,
            daily_cumulative_nav_pct=0.9,
            collateral_asset="jTRX",
            borrow_asset="TRX",
        )
        decision = evaluate_intent(intent)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.tier, ApprovalTier.AUTO)


    def test_evaluate_intent_allows_strx_borrow_pair(self):
        intent = DecisionIntent(
            intent_id="i3",
            asset="sTRX",
            action_type="borrow",
            requested_size_nav_pct=0.5,
            daily_cumulative_nav_pct=0.9,
            collateral_asset="jsTRX",
            borrow_asset="sTRX",
        )
        decision = evaluate_intent(intent)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.tier, ApprovalTier.AUTO)



if __name__ == "__main__":
    unittest.main()
