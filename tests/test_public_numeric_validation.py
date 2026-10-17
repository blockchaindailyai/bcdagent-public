import unittest
from dataclasses import replace
from openclaw_agent.capabilities.governance import GovernanceAction, route_governance_lane
from openclaw_agent.capabilities.policy_controls import ApprovalTier, DecisionIntent, approval_tier, evaluate_intent, reconciliation_status
from openclaw_agent.capabilities.config_management_impl.models import PolicyConfigV1
from openclaw_agent.capabilities.config_management_impl.config import validate_policy_config


class NumericValidationTests(unittest.TestCase):
    def test_nonfinite_and_negative_sizes_never_authorize(self):
        for value in (float('nan'), float('inf'), float('-inf'), -1):
            with self.subTest(value=value):
                self.assertFalse(route_governance_lane(GovernanceAction('test', 'rebalance', value, True)).allowed)
                self.assertEqual(approval_tier(value, 0, emergency=True), ApprovalTier.BLOCKED)
                self.assertEqual(approval_tier(0, value), ApprovalTier.BLOCKED)
                intent = DecisionIntent('test', 'TRX', 'rebalance', value, 0)
                self.assertFalse(evaluate_intent(intent).allowed)

    def test_nonfinite_drift_halts(self):
        for value in (float('nan'), float('inf'), float('-inf'), -1):
            self.assertEqual(reconciliation_status(value, 0), 'hard_halt')

    def test_nonfinite_configuration_rejected(self):
        cfg=PolicyConfigV1('example', ['TRX'], 20, 4, 20, 1.5, 4, 6, .1, .3, 3, {})
        self.assertTrue(validate_policy_config(cfg).valid)
        for name in ('reserve_floor_nav_pct','max_daily_turnover_nav_pct','max_weekly_turnover_nav_pct',
                     'auto_max_action_nav_pct','auto_max_daily_nav_pct','dual_min_action_nav_pct',
                     'soft_drift_nav_pct','hard_drift_nav_pct','soft_breach_limit_24h'):
            for value in (float('nan'), float('inf'), float('-inf')):
                with self.subTest(name=name,value=value):
                    self.assertFalse(validate_policy_config(replace(cfg, **{name:value})).valid)
