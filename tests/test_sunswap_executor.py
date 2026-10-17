import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openclaw_agent.turnkey.sunswap_executor import (
    SmartRouterStrategySwapRequest,
    SmartRouterSwapError,
    SmartRouterSwapRequest,
    ensure_sufficient_trc20_balance,
    request_to_dict,
    run_smart_router_strategy_swap,
    run_smart_router_swap,
)


class SmartRouterSwapRequestTests(unittest.TestCase):
    def test_request_to_dict_contains_runtime_pointer(self):
        req = SmartRouterSwapRequest(
            full_host='https://api.nileex.io',
            smart_router='TXpEUUL1hUdFvMeJYqh3htmMzJ1LQVrNVf',
            token_in='TXxNwTPmW8ABBnVPMVGX9ns5d2ZfoP1vy7',
            token_out='TWVFxoeLsFWMsdN4QPcpTPoZi98stSNaDA',
            amount_in=1000,
            amount_out_min=1,
            to='TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY',
            deadline=1770000000,
            pool_version=['v2'],
            version_len=[2],
            fees=[0, 0],
        )

        payload = request_to_dict(req)
        self.assertEqual(payload['pool_version'], ['v2'])
        self.assertEqual(payload['version_len'], [2])
        self.assertTrue(payload['runtime']['node_script'].endswith('smart_router_swap_exact_input.js'))
        self.assertFalse(payload['wait_for_receipt'])
        self.assertEqual(payload['receipt_timeout_s'], 90)
        self.assertFalse(payload['auto_amount_out_min'])
        self.assertEqual(payload['quote_slippage_bps'], 100)


    @patch("openclaw_agent.turnkey.sunswap_executor.subprocess.run")
    def test_run_swap_forwards_wait_for_receipt_args(self, mock_run):
        mock_run.return_value = unittest.mock.Mock(returncode=0, stdout='{"status":"submitted","txid":"abc"}\n', stderr='')
        req = SmartRouterSwapRequest(
            full_host='https://api.nileex.io',
            smart_router='TXpEUUL1hUdFvMeJYqh3htmMzJ1LQVrNVf',
            token_in='TXxNwTPmW8ABBnVPMVGX9ns5d2ZfoP1vy7',
            token_out='TWVFxoeLsFWMsdN4QPcpTPoZi98stSNaDA',
            amount_in=1000,
            amount_out_min=1,
            to='TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY',
            deadline=1770000000,
            pool_version=['v2'],
            version_len=[2],
            fees=[0, 0],
            wait_for_receipt=True,
            receipt_timeout_s=45,
        )

        _ = run_smart_router_swap(req)
        cmd = mock_run.call_args.args[0]
        self.assertIn('--wait-for-receipt=true', cmd)
        self.assertIn('--receipt-timeout-s=45', cmd)




    @patch("openclaw_agent.turnkey.sunswap_executor.subprocess.run")
    def test_run_swap_forwards_auto_amount_out_min_args(self, mock_run):
        mock_run.return_value = unittest.mock.Mock(returncode=0, stdout='{"status":"submitted","txid":"abc"}\n', stderr='')
        req = SmartRouterSwapRequest(
            full_host='https://api.nileex.io',
            smart_router='TXpEUUL1hUdFvMeJYqh3htmMzJ1LQVrNVf',
            token_in='TXxNwTPmW8ABBnVPMVGX9ns5d2ZfoP1vy7',
            token_out='TWVFxoeLsFWMsdN4QPcpTPoZi98stSNaDA',
            amount_in=1000,
            amount_out_min=1,
            to='TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY',
            deadline=1770000000,
            pool_version=['v2'],
            version_len=[2],
            fees=[0, 0],
            auto_amount_out_min=True,
            quote_slippage_bps=75,
        )

        _ = run_smart_router_swap(req)
        cmd = mock_run.call_args.args[0]
        self.assertIn('--auto-amount-out-min=true', cmd)
        self.assertIn('--quote-slippage-bps=75', cmd)


    def test_invalid_quote_slippage_bps_rejected_before_subprocess(self):
        req = SmartRouterSwapRequest(
            full_host='https://api.nileex.io',
            smart_router='TXpEUUL1hUdFvMeJYqh3htmMzJ1LQVrNVf',
            token_in='TXxNwTPmW8ABBnVPMVGX9ns5d2ZfoP1vy7',
            token_out='TWVFxoeLsFWMsdN4QPcpTPoZi98stSNaDA',
            amount_in=1000,
            amount_out_min=1,
            to='TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY',
            deadline=1770000000,
            pool_version=['v2'],
            version_len=[2],
            fees=[0, 0],
            quote_slippage_bps=10_000,
        )
        with self.assertRaises(SmartRouterSwapError):
            run_smart_router_swap(req)


    @patch("openclaw_agent.turnkey.sunswap_executor.subprocess.run")
    def test_run_swap_attaches_error_diagnostics_context(self, mock_run):
        mock_run.return_value = unittest.mock.Mock(
            returncode=1,
            stdout='',
            stderr='{"status":"error","error":"swap_reverted:REVERT opcode executed","context":{"txid":"abc123","stage":"waiting_for_receipt"}}\n',
        )
        req = SmartRouterSwapRequest(
            full_host='https://api.nileex.io',
            smart_router='TXpEUUL1hUdFvMeJYqh3htmMzJ1LQVrNVf',
            token_in='TXxNwTPmW8ABBnVPMVGX9ns5d2ZfoP1vy7',
            token_out='TWVFxoeLsFWMsdN4QPcpTPoZi98stSNaDA',
            amount_in=1000,
            amount_out_min=1,
            to='TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY',
            deadline=1770000000,
            pool_version=['v2'],
            version_len=[2],
            fees=[0, 0],
            wait_for_receipt=True,
        )

        with self.assertRaises(SmartRouterSwapError) as ctx:
            run_smart_router_swap(req)

        self.assertEqual(str(ctx.exception), 'swap_reverted:REVERT opcode executed')
        self.assertEqual(ctx.exception.diagnostics.get('context', {}).get('txid'), 'abc123')

    @patch("openclaw_agent.turnkey.sunswap_executor.subprocess.run")
    def test_run_swap_extracts_error_from_json_stderr(self, mock_run):
        mock_run.return_value = unittest.mock.Mock(
            returncode=1,
            stdout='',
            stderr='{"status":"error","error":"swap_reverted:UniswapV2Router: INSUFFICIENT_OUTPUT_AMOUNT"}\n',
        )
        req = SmartRouterSwapRequest(
            full_host='https://api.nileex.io',
            smart_router='TXpEUUL1hUdFvMeJYqh3htmMzJ1LQVrNVf',
            token_in='TXxNwTPmW8ABBnVPMVGX9ns5d2ZfoP1vy7',
            token_out='TWVFxoeLsFWMsdN4QPcpTPoZi98stSNaDA',
            amount_in=1000,
            amount_out_min=1,
            to='TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY',
            deadline=1770000000,
            pool_version=['v2'],
            version_len=[2],
            fees=[0, 0],
            wait_for_receipt=True,
        )

        with self.assertRaises(SmartRouterSwapError) as ctx:
            run_smart_router_swap(req)

        self.assertEqual(str(ctx.exception), 'swap_reverted:UniswapV2Router: INSUFFICIENT_OUTPUT_AMOUNT')

    def test_invalid_amount_rejected_before_subprocess(self):
        req = SmartRouterSwapRequest(
            full_host='https://api.nileex.io',
            smart_router='TXpEUUL1hUdFvMeJYqh3htmMzJ1LQVrNVf',
            token_in='TXxNwTPmW8ABBnVPMVGX9ns5d2ZfoP1vy7',
            token_out='TWVFxoeLsFWMsdN4QPcpTPoZi98stSNaDA',
            amount_in=0,
            amount_out_min=1,
            to='TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY',
            deadline=1770000000,
            pool_version=['v2'],
            version_len=[2],
            fees=[0, 0],
        )
        with self.assertRaises(SmartRouterSwapError):
            run_smart_router_swap(req)


class SmartRouterSwapPreflightTests(unittest.TestCase):
    @patch("openclaw_agent.turnkey.sunswap_executor._get_trc20_balance", return_value=1500)
    def test_ensure_sufficient_trc20_balance_returns_details_when_sufficient(self, _mock_balance):
        payload = ensure_sufficient_trc20_balance(
            "https://api.nileex.io",
            token="TXxNwTPmW8ABBnVPMVGX9ns5d2ZfoP1vy7",
            owner="TTreasury111111111111111111111111111",
            required_amount=1000,
        )

        self.assertEqual(payload["required_amount_in"], "1000")
        self.assertEqual(payload["token_in_balance"], "1500")

    @patch("openclaw_agent.turnkey.sunswap_executor._get_trc20_balance", return_value=700)
    def test_ensure_sufficient_trc20_balance_raises_with_diagnostics_when_insufficient(self, _mock_balance):
        with self.assertRaises(SmartRouterSwapError) as ctx:
            ensure_sufficient_trc20_balance(
                "https://api.nileex.io",
                token="TXxNwTPmW8ABBnVPMVGX9ns5d2ZfoP1vy7",
                owner="TTreasury111111111111111111111111111",
                required_amount=1000,
            )

        self.assertEqual(str(ctx.exception), "insufficient_token_in_balance")
        self.assertEqual(ctx.exception.diagnostics.get("token_in_balance"), "700")
        self.assertEqual(ctx.exception.diagnostics.get("token_in_deficit"), "300")


class SmartRouterStrategySwapPreflightTests(unittest.TestCase):
    def _build_request(self) -> SmartRouterStrategySwapRequest:
        cfg = SimpleNamespace(
            addresses=SimpleNamespace(treasury="TTreasury111111111111111111111111111"),
            allowed_assets={},
        )
        return SmartRouterStrategySwapRequest(
            config=cfg,
            full_host="https://api.nileex.io",
            strategy_module="TJRh2ZXVpqiGuxkno8v3ypgPcDPBFmJFsc",
            smart_router="TXpEUUL1hUdFvMeJYqh3htmMzJ1LQVrNVf",
            token_in="TXxNwTPmW8ABBnVPMVGX9ns5d2ZfoP1vy7",
            token_out="TWVFxoeLsFWMsdN4QPcpTPoZi98stSNaDA",
            amount_in=1000,
            amount_out_min=1,
            to="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY",
            deadline=1770000000,
            pool_version=["v2"],
            version_len=[2],
            fees=[0, 0],
            owner_address="TOwner111111111111111111111111111111",
            intent_id="intent-1",
            plan_id="plan-1",
        )

    @patch("openclaw_agent.turnkey.sunswap_executor.prepare_smart_router_strategy_swap")
    @patch("openclaw_agent.turnkey.sunswap_executor.ensure_sufficient_trc20_balance")
    @patch("openclaw_agent.turnkey.sunswap_executor._ensure_wtrx_liquidity_for_swap", return_value=None)
    def test_strategy_swap_runs_balance_preflight_before_broadcast(self, _mock_wtrx, mock_balance, mock_prepare):
        req = self._build_request()
        mock_prepare.return_value = {
            "strategyPayload": {
                "callData": "0x1234",
                "amountIn": "1000",
                "target": req.smart_router,
                "selector": "0xcef95229",
            }
        }
        mock_balance.return_value = {"token_in_balance": "1000"}
        broadcaster = Mock()
        broadcaster.broadcast_execute_call_with_token.return_value = SimpleNamespace(txid="abc", raw_response={"result": True})

        out = run_smart_router_strategy_swap(req, broadcaster=broadcaster, permission_id=0)

        mock_balance.assert_called_once_with(
            req.full_host,
            token=req.token_in,
            owner=req.config.addresses.treasury,
            required_amount=req.amount_in,
        )
        broadcaster.broadcast_execute_call_with_token.assert_called_once()
        self.assertEqual(out["status"], "submitted")
        self.assertIn("treasury_balance_preflight", out)

    @patch("openclaw_agent.turnkey.sunswap_executor.prepare_smart_router_strategy_swap")
    @patch("openclaw_agent.turnkey.sunswap_executor.ensure_sufficient_trc20_balance", side_effect=SmartRouterSwapError("insufficient_token_in_balance"))
    @patch("openclaw_agent.turnkey.sunswap_executor._ensure_wtrx_liquidity_for_swap", return_value=None)
    def test_strategy_swap_stops_before_broadcast_when_balance_preflight_fails(self, _mock_wtrx, _mock_balance, mock_prepare):
        req = self._build_request()
        broadcaster = Mock()

        with self.assertRaises(SmartRouterSwapError) as ctx:
            run_smart_router_strategy_swap(req, broadcaster=broadcaster, permission_id=0)

        self.assertEqual(str(ctx.exception), "insufficient_token_in_balance")
        mock_prepare.assert_not_called()
        broadcaster.broadcast_execute_call_with_token.assert_not_called()


if __name__ == '__main__':
    unittest.main()
