import unittest

from openclaw_agent.capabilities.config_management import diff_configs, load_policy_config, validate_policy_config


BASE_PAYLOAD = {
    "version": "v1.0.0",
    "allowed_assets": ["TRX", "sTRX", "USDD", "sUSDD", "USDT"],
    "reserve_floor_nav_pct": 35.0,
    "max_daily_turnover_nav_pct": 12.0,
    "max_weekly_turnover_nav_pct": 30.0,
    "auto_max_action_nav_pct": 1.0,
    "auto_max_daily_nav_pct": 3.0,
    "dual_min_action_nav_pct": 5.0,
    "soft_drift_nav_pct": 0.10,
    "hard_drift_nav_pct": 0.30,
    "soft_breach_limit_24h": 3,
    "allowed_borrow_pairs": {
        "sTRX": ["TRX"],
        "sUSDD": ["USDD", "USDT"],
    },
}


class Phase4ConfigTests(unittest.TestCase):
    def test_load_and_validate_baseline(self):
        cfg = load_policy_config(BASE_PAYLOAD)
        result = validate_policy_config(cfg)
        self.assertTrue(result.valid)
        self.assertEqual(result.errors, [])

    def test_forbidden_direction_rejected(self):
        payload = dict(BASE_PAYLOAD)
        payload["allowed_borrow_pairs"] = {
            "sTRX": ["TRX"],
            "TRX": ["sTRX"],
            "sUSDD": ["USDD", "USDT"],
        }
        cfg = load_policy_config(payload)
        result = validate_policy_config(cfg)
        self.assertFalse(result.valid)
        self.assertIn("forbidden_borrow_direction_present:TRX->STRX", result.errors)

    def test_forbidden_stable_direction_rejected(self):
        payload = dict(BASE_PAYLOAD)
        payload["allowed_borrow_pairs"] = {
            "sTRX": ["TRX"],
            "USDD": ["sUSDD"],
            "sUSDD": ["USDD", "USDT"],
        }
        cfg = load_policy_config(payload)
        result = validate_policy_config(cfg)
        self.assertFalse(result.valid)
        self.assertIn("forbidden_borrow_direction_present:USDD->SUSDD", result.errors)

    def test_invalid_threshold_relationships(self):
        payload = dict(BASE_PAYLOAD)
        payload["auto_max_action_nav_pct"] = 4.0
        payload["auto_max_daily_nav_pct"] = 3.0
        payload["dual_min_action_nav_pct"] = 2.0
        cfg = load_policy_config(payload)
        result = validate_policy_config(cfg)
        self.assertFalse(result.valid)
        self.assertIn("auto_daily_below_auto_action", result.errors)
        self.assertIn("dual_threshold_below_auto_threshold", result.errors)

    def test_diff_configs(self):
        c1 = load_policy_config(BASE_PAYLOAD)
        p2 = dict(BASE_PAYLOAD)
        p2["version"] = "v1.1.0"
        p2["max_daily_turnover_nav_pct"] = 10.0
        c2 = load_policy_config(p2)
        d = diff_configs(c1, c2)
        self.assertEqual(d.from_version, "v1.0.0")
        self.assertEqual(d.to_version, "v1.1.0")
        self.assertIn("max_daily_turnover_nav_pct", d.changed_fields)


if __name__ == "__main__":
    unittest.main()
