import unittest

from openclaw_agent.turnkey.autonomous import (
    AutonomousCyclePlan,
    build_cycle_plan,
    decide_action_from_metrics,
    override_cycle_action,
    parse_action_sequence,
    resolve_effective_thresholds,
    run_autonomous_cycles,
)


class TurnkeyAutonomousTests(unittest.TestCase):
    def test_parse_action_sequence_rejects_empty(self):
        with self.assertRaises(ValueError):
            parse_action_sequence("")

    def test_build_cycle_plan_rotates_actions(self):
        actions = ["mint", "borrow"]
        first = build_cycle_plan(cycle_index=0, actions=actions, intent_prefix="auto", epoch_s=1700000000)
        second = build_cycle_plan(cycle_index=1, actions=actions, intent_prefix="auto", epoch_s=1700000000)
        third = build_cycle_plan(cycle_index=2, actions=actions, intent_prefix="auto", epoch_s=1700000000)

        self.assertEqual(first.action, "mint")
        self.assertEqual(second.action, "borrow")
        self.assertEqual(third.action, "mint")
        self.assertEqual(first.intent_id, "auto-1700000000-0000-mint")


    def test_decide_action_from_metrics_adaptive_rules(self):
        self.assertEqual(
            decide_action_from_metrics(metrics={"borrow_ratio": 0.8, "collateral_ratio": 0.2}, repay_threshold=0.6, borrow_threshold=0.3),
            "repayBorrow",
        )
        self.assertEqual(
            decide_action_from_metrics(metrics={"borrow_ratio": 0.1, "collateral_ratio": 0.9}, repay_threshold=0.6, borrow_threshold=0.3),
            "borrow",
        )
        self.assertEqual(
            decide_action_from_metrics(metrics={"borrow_ratio": 0.0, "collateral_ratio": 0.0}, repay_threshold=0.6, borrow_threshold=0.0),
            "mint",
        )
        self.assertEqual(
            decide_action_from_metrics(metrics={"borrow_ratio": 0.4, "collateral_ratio": 0.6}, repay_threshold=0.6, borrow_threshold=0.3),
            "mint",
        )
        self.assertEqual(
            decide_action_from_metrics(
                metrics={"borrow_ratio": 0.51, "collateral_ratio": 0.49},
                repay_threshold=0.99,
                borrow_threshold=0.01,
                repay_collateral_ratio_below=0.50,
            ),
            "repayBorrow",
        )
        self.assertEqual(
            decide_action_from_metrics(
                metrics={"borrow_ratio": 0.35, "collateral_ratio": 0.65},
                repay_threshold=0.99,
                borrow_threshold=0.01,
                borrow_collateral_ratio_above=0.60,
            ),
            "borrow",
        )


    def test_resolve_effective_thresholds_prefers_collateral_wrappers(self):
        repay_threshold, borrow_threshold = resolve_effective_thresholds(
            repay_threshold=0.85,
            borrow_threshold=0.2,
            repay_collateral_ratio_below=0.55,
            borrow_collateral_ratio_above=0.75,
        )

        self.assertAlmostEqual(repay_threshold, 0.45)
        self.assertAlmostEqual(borrow_threshold, 0.25)

    def test_run_autonomous_cycles_stop_on_failure(self):
        calls = []

        def runner(plan: AutonomousCyclePlan) -> int:
            calls.append(plan.action)
            return 2 if plan.cycle_index == 1 else 0

        summary = run_autonomous_cycles(
            actions=["mint", "borrow", "repayBorrow"],
            intent_prefix="auto",
            max_cycles=5,
            interval_s=0,
            stop_on_failure=True,
            cycle_runner=runner,
            sleep_fn=lambda _x: None,
        )

        self.assertEqual(summary.total_cycles, 2)
        self.assertEqual(summary.succeeded_cycles, 1)
        self.assertEqual(summary.failed_cycles, 1)
        self.assertEqual(calls, ["mint", "borrow"])

    def test_run_autonomous_cycles_uses_action_selector(self):
        seen = []

        def runner(plan: AutonomousCyclePlan) -> int:
            seen.append(plan.action)
            return 0

        summary = run_autonomous_cycles(
            actions=["mint"],
            intent_prefix="auto",
            max_cycles=3,
            interval_s=0,
            stop_on_failure=False,
            cycle_runner=runner,
            action_selector=lambda i: "repayBorrow" if i == 1 else "borrow",
            sleep_fn=lambda _x: None,
        )

        self.assertEqual(summary.total_cycles, 3)
        self.assertEqual(seen, ["borrow", "repayBorrow", "borrow"])

    def test_override_cycle_action_rewrites_intent_suffix(self):
        plan = AutonomousCyclePlan(cycle_index=2, action="mint", intent_id="auto-1700000000-0002-mint")
        updated = override_cycle_action(plan, "repayBorrow")

        self.assertEqual(updated.action, "repayBorrow")
        self.assertEqual(updated.intent_id, "auto-1700000000-0002-repayBorrow")



if __name__ == "__main__":
    unittest.main()
