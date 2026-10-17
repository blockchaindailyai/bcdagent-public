import unittest

from openclaw_agent.capabilities.policy_controls import ApprovalTier
from openclaw_agent.capabilities.execution_adapter import ExecutionPlanV2, ExecutionStep, PolicyDecisionV2
from openclaw_agent.turnkey import JTRXDecisionEngine, JTRXDecisionIntent, JTRXExecutor, load_turnkey_config
from openclaw_agent.turnkey.jtrx_engine import normalize_jtrx_action


class JTRXDecisionEngineTests(unittest.TestCase):
    def test_normalize_jtrx_action_aliases(self):
        self.assertEqual(normalize_jtrx_action("redeemUnderlying"), "redeem_underlying")
        self.assertEqual(normalize_jtrx_action("repayBorrow"), "repay_borrow")

    def test_unsupported_action_blocked(self):
        engine = JTRXDecisionEngine()
        decision = engine.evaluate(
            JTRXDecisionIntent(
                intent_id="jtrx-1",
                action="flash_loan",
                amount_sun=1,
                requested_size_nav_pct=0.1,
                daily_cumulative_nav_pct=0.1,
            )
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.approval_tier, ApprovalTier.BLOCKED)

    def test_build_mint_plan_uses_trx_strategy_path(self):
        engine = JTRXDecisionEngine()
        cfg = load_turnkey_config()
        intent = JTRXDecisionIntent(
            intent_id="jtrx-2",
            action="mint",
            amount_sun=2_000_000,
            requested_size_nav_pct=0.5,
            daily_cumulative_nav_pct=1.0,
        )
        decision = engine.evaluate(intent)
        self.assertTrue(decision.allowed)

        plan = engine.build_plan(intent, decision, cfg, now_epoch_s=1_700_000_000)
        step = plan.execution_plan.ordered_steps[0]
        self.assertEqual(step.params["strategy_method"], "supplyTRXFromOps")
        self.assertEqual(step.params["call_data"], "0x1249c58b")
        self.assertEqual(step.params["strategy_amount_sun"], "0")
        self.assertEqual(step.params["call_value_sun"], "0")
        self.assertEqual(step.params["strategy_module"], cfg.addresses.justlend_ops_module)
        self.assertEqual(plan.execution_plan.required_approvals, 0)

    def test_build_borrow_plan_uses_borrow_path(self):
        engine = JTRXDecisionEngine()
        cfg = load_turnkey_config()
        intent = JTRXDecisionIntent(
            intent_id="jtrx-3",
            action="borrow",
            amount_sun=50_000_000,
            requested_size_nav_pct=1.8,
            daily_cumulative_nav_pct=1.5,
        )
        decision = engine.evaluate(intent)
        plan = engine.build_plan(intent, decision, cfg, now_epoch_s=1_700_000_000)
        step_params = plan.execution_plan.ordered_steps[0].params
        self.assertEqual(step_params["strategy_method"], "borrowFromOps")
        self.assertEqual(step_params["collateral_asset"], "jTRX")
        self.assertEqual(step_params["borrow_asset"], "TRX")
        self.assertEqual(step_params["strategy_module"], cfg.addresses.justlend_ops_module)
        self.assertEqual(plan.execution_plan.required_approvals, 1)

    def test_build_repay_borrow_plan_accepts_camel_case_action(self):
        engine = JTRXDecisionEngine()
        cfg = load_turnkey_config()
        intent = JTRXDecisionIntent(
            intent_id="jtrx-6",
            action="repayBorrow",
            amount_sun=1,
            requested_size_nav_pct=0.2,
            daily_cumulative_nav_pct=0.2,
        )
        decision = engine.evaluate(intent)
        self.assertTrue(decision.allowed)

        plan = engine.build_plan(intent, decision, cfg, now_epoch_s=1_700_000_000)
        step = plan.execution_plan.ordered_steps[0]
        self.assertEqual(step.params["selector"], "0x0e752702")
        self.assertEqual(step.params["call_data"], "0x0e752702")
        self.assertEqual(step.params["strategy_method"], "repayBorrowFromOps")
        self.assertEqual(step.params["strategy_amount_sun"], "0")
        self.assertEqual(step.params["call_value_sun"], "0")


    def test_build_redeem_underlying_plan_uses_zero_call_value(self):
        engine = JTRXDecisionEngine()
        cfg = load_turnkey_config()
        intent = JTRXDecisionIntent(
            intent_id="jtrx-7",
            action="redeemUnderlying",
            amount_sun=1_000_000,
            requested_size_nav_pct=0.5,
            daily_cumulative_nav_pct=0.5,
        )
        decision = engine.evaluate(intent)
        self.assertTrue(decision.allowed)

        plan = engine.build_plan(intent, decision, cfg, now_epoch_s=1_700_000_000)
        step = plan.execution_plan.ordered_steps[0]
        self.assertEqual(step.params["strategy_method"], "executeCallWithToken")
        self.assertEqual(step.params["asset"], cfg.addresses.jtrx_market)
        self.assertEqual(step.params["strategy_amount_sun"], "1000000")
        self.assertEqual(step.params["selector"], "0x852a12e3")
        self.assertEqual(step.params["call_value_sun"], "0")




    def test_build_token_mint_plan_uses_supply_token_from_ops(self):
        engine = JTRXDecisionEngine()
        cfg = load_turnkey_config()
        intent = JTRXDecisionIntent(
            intent_id="jtrx-9",
            action="mint",
            amount_sun=1_500_000,
            requested_size_nav_pct=0.2,
            daily_cumulative_nav_pct=0.2,
            market="TPKHZjesmxmZEuXUzNEAR7wc2M6kAeAx8J",
            underlying_asset="USDT",
        )
        decision = engine.evaluate(intent)
        self.assertTrue(decision.allowed)

        plan = engine.build_plan(intent, decision, cfg, now_epoch_s=1_700_000_000)
        step = plan.execution_plan.ordered_steps[0]
        self.assertEqual(step.params["strategy_method"], "supplyTokenFromOps")
        self.assertEqual(step.params["selector"], "0xa0712d68")
        self.assertEqual(step.params["target"], "TPKHZjesmxmZEuXUzNEAR7wc2M6kAeAx8J")
        self.assertEqual(step.params["asset"], "USDT")


    def test_build_token_borrow_plan_uses_market_override_and_token_policy_assets(self):
        engine = JTRXDecisionEngine()
        cfg = load_turnkey_config()
        intent = JTRXDecisionIntent(
            intent_id="jtrx-11",
            action="borrow",
            amount_sun=2_000_000,
            requested_size_nav_pct=0.2,
            daily_cumulative_nav_pct=0.2,
            market="TPKHZjesmxmZEuXUzNEAR7wc2M6kAeAx8J",
            underlying_asset="USDT",
        )
        decision = engine.evaluate(intent)
        self.assertTrue(decision.allowed)

        plan = engine.build_plan(intent, decision, cfg, now_epoch_s=1_700_000_000)
        step = plan.execution_plan.ordered_steps[0]
        self.assertEqual(step.params["strategy_method"], "borrowFromOps")
        self.assertEqual(step.params["target"], "TPKHZjesmxmZEuXUzNEAR7wc2M6kAeAx8J")
        self.assertEqual(step.params["borrow_asset"], "USDT")
        self.assertEqual(step.params["collateral_asset"], "jUSDT")

    def test_build_strx_borrow_plan_maps_to_jstrx_policy_assets(self):
        engine = JTRXDecisionEngine()
        cfg = load_turnkey_config()
        intent = JTRXDecisionIntent(
            intent_id="jtrx-13",
            action="borrow",
            amount_sun=2_000_000,
            requested_size_nav_pct=0.2,
            daily_cumulative_nav_pct=0.2,
            market="TJSTRX_TOKEN_ADDRESS",
            underlying_asset="sTRX",
        )
        decision = engine.evaluate(intent)
        self.assertTrue(decision.allowed)

        plan = engine.build_plan(intent, decision, cfg, now_epoch_s=1_700_000_000)
        step = plan.execution_plan.ordered_steps[0]
        self.assertEqual(step.params["strategy_method"], "borrowFromOps")
        self.assertEqual(step.params["target"], "TJSTRX_TOKEN_ADDRESS")
        self.assertEqual(step.params["borrow_asset"], "sTRX")
        self.assertEqual(step.params["collateral_asset"], "jsTRX")

    def test_build_susdd_borrow_plan_maps_to_jsusdd_policy_assets(self):
        engine = JTRXDecisionEngine()
        cfg = load_turnkey_config()
        intent = JTRXDecisionIntent(
            intent_id="jtrx-14",
            action="borrow",
            amount_sun=2_000_000,
            requested_size_nav_pct=0.2,
            daily_cumulative_nav_pct=0.2,
            market="TJSUSDD_TOKEN_ADDRESS",
            underlying_asset="sUSDD",
        )
        decision = engine.evaluate(intent)
        self.assertTrue(decision.allowed)

        plan = engine.build_plan(intent, decision, cfg, now_epoch_s=1_700_000_000)
        step = plan.execution_plan.ordered_steps[0]
        self.assertEqual(step.params["strategy_method"], "borrowFromOps")
        self.assertEqual(step.params["target"], "TJSUSDD_TOKEN_ADDRESS")
        self.assertEqual(step.params["borrow_asset"], "SUSDD")
        self.assertEqual(step.params["collateral_asset"], "jSUSDD")

    def test_build_token_repay_plan_uses_token_repay_path(self):
        engine = JTRXDecisionEngine()
        cfg = load_turnkey_config()
        intent = JTRXDecisionIntent(
            intent_id="jtrx-10",
            action="repayBorrow",
            amount_sun=1_000_000,
            requested_size_nav_pct=0.2,
            daily_cumulative_nav_pct=0.2,
            market="TPKHZjesmxmZEuXUzNEAR7wc2M6kAeAx8J",
            underlying_asset="USDT",
        )
        decision = engine.evaluate(intent)
        self.assertTrue(decision.allowed)

        plan = engine.build_plan(intent, decision, cfg, now_epoch_s=1_700_000_000)
        step = plan.execution_plan.ordered_steps[0]
        self.assertEqual(step.params["strategy_method"], "repayBorrowTokenFromOps")
        self.assertEqual(step.params["target"], "TPKHZjesmxmZEuXUzNEAR7wc2M6kAeAx8J")
        self.assertEqual(step.params["asset"], "USDT")


    def test_token_borrow_with_unmapped_address_is_blocked(self):
        engine = JTRXDecisionEngine()
        intent = JTRXDecisionIntent(
            intent_id="jtrx-12",
            action="borrow",
            amount_sun=1_000_000,
            requested_size_nav_pct=0.2,
            daily_cumulative_nav_pct=0.2,
            market="TPKHZjesmxmZEuXUzNEAR7wc2M6kAeAx8J",
            underlying_asset="TQHyk9zmKKdqs9Q5NFj8ervEWo1paDre8L",
        )

        decision = engine.evaluate(intent)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.approval_tier, ApprovalTier.BLOCKED)

    def test_build_redeem_underlying_plan_accepts_strategy_amount_override(self):
        engine = JTRXDecisionEngine()
        cfg = load_turnkey_config()
        intent = JTRXDecisionIntent(
            intent_id="jtrx-8",
            action="redeemUnderlying",
            amount_sun=1_000_000,
            strategy_amount_sun=9_000_000_000,
            requested_size_nav_pct=0.5,
            daily_cumulative_nav_pct=0.5,
        )
        decision = engine.evaluate(intent)
        self.assertTrue(decision.allowed)

        plan = engine.build_plan(intent, decision, cfg, now_epoch_s=1_700_000_000)
        step = plan.execution_plan.ordered_steps[0]
        self.assertEqual(step.params["strategy_amount_sun"], "9000000000")




class JTRXExecutorTests(unittest.TestCase):
    def test_executor_blocks_when_approval_missing(self):
        engine = JTRXDecisionEngine()
        cfg = load_turnkey_config()
        intent = JTRXDecisionIntent(
            intent_id="jtrx-4",
            action="redeem",
            amount_sun=1,
            requested_size_nav_pct=2.0,
            daily_cumulative_nav_pct=2.0,
        )
        decision = engine.evaluate(intent)
        plan = engine.build_plan(intent, decision, cfg, now_epoch_s=1_700_000_000)
        self.assertEqual(plan.execution_plan.ordered_steps[0].params["strategy_method"], "executeCallWithToken")
        self.assertEqual(plan.execution_plan.ordered_steps[0].params["asset"], cfg.addresses.jtrx_market)

        receipt = JTRXExecutor().execute(plan, approved_by=[])
        self.assertEqual(receipt.status, "blocked")
        self.assertIn("approval", receipt.failure_reason)

    def test_executor_submits_with_approval(self):
        engine = JTRXDecisionEngine()
        cfg = load_turnkey_config()
        intent = JTRXDecisionIntent(
            intent_id="jtrx-5",
            action="borrow",
            amount_sun=1,
            requested_size_nav_pct=2.0,
            daily_cumulative_nav_pct=2.0,
        )
        decision = engine.evaluate(intent)
        plan = engine.build_plan(intent, decision, cfg, now_epoch_s=1_700_000_000)

        receipt = JTRXExecutor().execute(plan, approved_by=["operator-1"])
        self.assertEqual(receipt.status, "simulated")
        self.assertTrue(receipt.tx_hashes)


class _FakeBroadcaster:
    def broadcast_jtrx_plan(self, _plan, owner_address):
        class _Result:
            txid = "tx123"
        return _Result()

    def wait_for_receipt(self, _txid):
        return {
            "receipt": {"result": "REVERT"},
            "result": "REVERT opcode executed",
            "resMessage": "696e73756666696369656e7420636f6c6c61746572616c",
        }


class JTRXFailureReasonTests(unittest.TestCase):
    def test_executor_decodes_hex_res_message_for_failed_receipt(self):
        plan = type("_Plan", (), {})()
        plan.execution_plan = ExecutionPlanV2(
            intent_id="id",
            plan_id="pid",
            ordered_steps=[
                ExecutionStep(
                    name="justlend_strategy_call",
                    params={"selector": "0xc5ebeaec"},
                )
            ],
            max_slippage_bps=0,
            deadline_epoch_s=0,
            required_approvals=0,
        )
        plan.policy_decision = PolicyDecisionV2(
            intent_id="id",
            allowed=True,
            approval_tier=ApprovalTier.AUTO,
            denial_reasons=[],
            limits_applied=[],
            policy_version="jtrx-v1",
        )

        receipt = JTRXExecutor(broadcaster=_FakeBroadcaster(), owner_address="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY").execute(
            plan,
            approved_by=[],
            broadcast=True,
            wait_for_receipt=True,
        )

        self.assertEqual(receipt.status, "failed")
        self.assertEqual(receipt.failure_reason, "insufficient collateral")


class JTRXContractResultFailureReasonTests(unittest.TestCase):
    def test_executor_decodes_contract_result_error_string(self):
        class _ContractResultBroadcaster:
            def broadcast_jtrx_plan(self, _plan, owner_address):
                class _Result:
                    txid = "tx456"
                return _Result()

            def wait_for_receipt(self, _txid):
                return {
                    "receipt": {"result": "REVERT"},
                    "contractResult": [
                        "08c379a0"
                        "0000000000000000000000000000000000000000000000000000000000000020"
                        "000000000000000000000000000000000000000000000000000000000000000f"
                        "544f4b454e5f53454e445f4641494c0000000000000000000000000000000000"
                    ],
                }

        plan = type("_Plan", (), {})()
        plan.execution_plan = ExecutionPlanV2(
            intent_id="id",
            plan_id="pid",
            ordered_steps=[
                ExecutionStep(
                    name="justlend_strategy_call",
                    params={"selector": "0xa0712d68"},
                )
            ],
            max_slippage_bps=0,
            deadline_epoch_s=0,
            required_approvals=0,
        )
        plan.policy_decision = PolicyDecisionV2(
            intent_id="id",
            allowed=True,
            approval_tier=ApprovalTier.AUTO,
            denial_reasons=[],
            limits_applied=[],
            policy_version="jtrx-v1",
        )

        receipt = JTRXExecutor(broadcaster=_ContractResultBroadcaster(), owner_address="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY").execute(
            plan,
            approved_by=[],
            broadcast=True,
            wait_for_receipt=True,
        )

        self.assertEqual(receipt.status, "failed")
        self.assertEqual(receipt.failure_reason, "TOKEN_SEND_FAIL")


if __name__ == "__main__":
    unittest.main()
