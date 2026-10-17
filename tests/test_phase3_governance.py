import unittest

from openclaw_agent.capabilities.governance import GovernanceAction, route_governance_lane


class Phase3GovernanceTests(unittest.TestCase):
    def test_auto_lane(self):
        action = GovernanceAction("a1", "rebalance", 0.8, True)
        d = route_governance_lane(action)
        self.assertTrue(d.allowed)
        self.assertEqual(d.lane, "auto")
        self.assertEqual(d.required_approvals, 0)

    def test_manual_lane_on_threshold(self):
        action = GovernanceAction("a2", "rebalance", 2.0, True)
        d = route_governance_lane(action)
        self.assertEqual(d.lane, "manual")
        self.assertEqual(d.required_approvals, 1)

    def test_dual_lane(self):
        action = GovernanceAction("a3", "rebalance", 6.0, True)
        d = route_governance_lane(action)
        self.assertEqual(d.lane, "dual")
        self.assertEqual(d.required_approvals, 2)

    def test_manual_when_degraded_data(self):
        action = GovernanceAction("a4", "rebalance", 0.2, False)
        d = route_governance_lane(action)
        self.assertEqual(d.lane, "manual")
        self.assertIn("degraded_data_manual_required", d.reasons)

    def test_manual_when_config_change(self):
        action = GovernanceAction("a5", "config_update", 0.1, True, requires_config_change=True)
        d = route_governance_lane(action)
        self.assertEqual(d.lane, "manual")
        self.assertIn("config_change_manual_required", d.reasons)

    def test_blocked_on_invalid_size(self):
        action = GovernanceAction("a6", "rebalance", -1.0, True)
        d = route_governance_lane(action)
        self.assertFalse(d.allowed)
        self.assertEqual(d.lane, "blocked")


if __name__ == "__main__":
    unittest.main()
