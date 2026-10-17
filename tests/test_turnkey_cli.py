import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from openclaw_agent.turnkey.__main__ import _derive_metrics_for_autonomous_cycle, _normalize_underlying_asset, _run_autonomous_jtrx, main, maybe_sweep_ops_to_treasury
from openclaw_agent.turnkey.onchain import TronBroadcastError
from openclaw_agent.turnkey.sunswap_executor import SmartRouterSwapError


class TurnkeyCliTests(unittest.TestCase):
    @patch("openclaw_agent.turnkey.__main__._run_check_borrow_ready", return_value=0)
    @patch("sys.argv", ["openclaw-agent", "check-borrow-ready"])
    def test_main_check_borrow_ready_command(self, mock_run):
        rc = main()
        self.assertEqual(rc, 0)
        mock_run.assert_called_once()

    @patch("openclaw_agent.turnkey.__main__._run_monitor_account", return_value=0)
    @patch("sys.argv", ["openclaw-agent", "monitor-account"])
    def test_main_monitor_account_command(self, mock_run):
        rc = main()
        self.assertEqual(rc, 0)
        mock_run.assert_called_once()



    @patch("openclaw_agent.turnkey.__main__._run_strategy_trx", return_value=0)
    @patch("sys.argv", ["openclaw-agent", "run-strategy-trx", "--intent-id", "i", "--plan-id", "p", "--amount-sun", "1", "--target", "TAutUnFJhvocKb3egsma3vBEEEkznPbokk", "--call-data", "0xd0e30db0", "--permission-id", "2"])
    def test_main_parses_strategy_permission_id(self, mock_run):
        rc = main()
        self.assertEqual(rc, 0)
        self.assertEqual(mock_run.call_args.args[0].permission_id, 2)

    @patch("openclaw_agent.turnkey.__main__._run_jtrx", return_value=0)
    @patch("sys.argv", ["openclaw-agent", "run-jtrx", "--intent-id", "i", "--action", "mint", "--amount-sun", "1", "--requested-size-nav-pct", "0.1", "--daily-cumulative-nav-pct", "0.1", "--permission-id", "2"])
    def test_main_parses_jtrx_permission_id(self, mock_run):
        rc = main()
        self.assertEqual(rc, 0)
        self.assertEqual(mock_run.call_args.args[0].permission_id, 2)



    @patch("openclaw_agent.turnkey.__main__._run_jtrx", return_value=0)
    @patch("sys.argv", ["openclaw-agent", "run-jtrx", "--intent-id", "i", "--action", "mint", "--amount-sun", "1", "--requested-size-nav-pct", "0.1", "--daily-cumulative-nav-pct", "0.1", "--concise-output"])
    def test_main_parses_jtrx_concise_output_flag(self, mock_run):
        rc = main()
        self.assertEqual(rc, 0)
        self.assertTrue(mock_run.call_args.args[0].concise_output)


    @patch("openclaw_agent.turnkey.__main__._run_autonomous_jtrx", return_value=0)
    @patch("sys.argv", ["openclaw-agent", "run-autonomous-jtrx", "--amount-sun", "1", "--requested-size-nav-pct", "0.1", "--daily-cumulative-nav-pct", "0.1", "--concise-output"])
    def test_main_parses_autonomous_concise_output_flag(self, mock_run):
        rc = main()
        self.assertEqual(rc, 0)
        self.assertTrue(mock_run.call_args.args[0].concise_output)

    @patch("openclaw_agent.turnkey.__main__._run_autonomous_jtrx", return_value=0)
    @patch("sys.argv", ["openclaw-agent", "run-autonomous-jtrx", "--amount-sun", "1", "--requested-size-nav-pct", "0.1", "--daily-cumulative-nav-pct", "0.1", "--permission-id", "2"])
    def test_main_parses_autonomous_permission_id(self, mock_run):
        rc = main()
        self.assertEqual(rc, 0)
        self.assertEqual(mock_run.call_args.args[0].permission_id, 2)


    @patch("openclaw_agent.turnkey.__main__._run_autonomous_jtrx", return_value=0)
    @patch("sys.argv", ["openclaw-agent", "run-autonomous-jtrx", "--amount-sun", "1", "--usdt-amount-sun", "2", "--requested-size-nav-pct", "0.1", "--daily-cumulative-nav-pct", "0.1", "--strategy-mode", "adaptive", "--repay-threshold", "0.7", "--borrow-threshold", "0.2", "--usdt-repay-threshold", "0.8", "--usdt-borrow-threshold", "0.25"])
    def test_main_parses_autonomous_adaptive_flags(self, mock_run):
        rc = main()
        self.assertEqual(rc, 0)
        args = mock_run.call_args.args[0]
        self.assertEqual(args.strategy_mode, "adaptive")
        self.assertEqual(args.repay_threshold, 0.7)
        self.assertEqual(args.borrow_threshold, 0.2)
        self.assertEqual(args.usdt_repay_threshold, 0.8)
        self.assertEqual(args.usdt_borrow_threshold, 0.25)
        self.assertEqual(args.usdt_amount_sun, 2)

    @patch("openclaw_agent.turnkey.__main__._run_autonomous_jtrx", return_value=0)
    @patch("sys.argv", ["openclaw-agent", "run-autonomous-jtrx", "--amount-sun", "1", "--requested-size-nav-pct", "0.1", "--daily-cumulative-nav-pct", "0.1", "--strategy-mode", "adaptive", "--repay-collateral-ratio-below", "0.45", "--borrow-collateral-ratio-above", "0.7"])
    def test_main_parses_autonomous_collateral_ratio_flags(self, mock_run):
        rc = main()
        self.assertEqual(rc, 0)
        args = mock_run.call_args.args[0]
        self.assertEqual(args.repay_collateral_ratio_below, 0.45)
        self.assertEqual(args.borrow_collateral_ratio_above, 0.7)

    @patch("openclaw_agent.turnkey.__main__._run_autonomous_jtrx", return_value=0)
    @patch("sys.argv", ["openclaw-agent", "run-autonomous-jtrx", "--amount-sun", "1", "--requested-size-nav-pct", "0.1", "--daily-cumulative-nav-pct", "0.1", "--ops-sweep-threshold-ratio", "0.2"])
    def test_main_parses_autonomous_ops_sweep_threshold_ratio(self, mock_run):
        rc = main()
        self.assertEqual(rc, 0)
        self.assertEqual(mock_run.call_args.args[0].ops_sweep_threshold_ratio, 0.2)



    @patch("openclaw_agent.turnkey.__main__.logging.getLogger")
    @patch("openclaw_agent.turnkey.__main__.load_turnkey_config")
    @patch("openclaw_agent.turnkey.__main__.decide_action_from_metrics", side_effect=["borrow", "mint", "repayBorrow", "borrow"])
    @patch("openclaw_agent.turnkey.__main__._derive_metrics_for_autonomous_cycle")
    @patch("openclaw_agent.turnkey.__main__._run_jtrx", return_value=0)
    def test_autonomous_adaptive_refreshes_metrics_each_cycle(self, _mock_run_jtrx, mock_metrics, _mock_decide, mock_load_cfg, mock_get_logger):
        mock_load_cfg.return_value = SimpleNamespace(addresses=SimpleNamespace(jusdt_market="jusdt"))
        mock_metrics.side_effect = [
            {
                "trx_borrow_ratio": 0.1,
                "trx_collateral_ratio": 0.9,
                "usdt_borrow_ratio": 0.3,
                "usdt_collateral_ratio": 0.7,
                "selected_market": "jtrx",
                "observed_account": "acct",
                "trx_borrowed": 1.0,
                "jtrx_balance": 10.0,
                "jtrx_underlying": 1.2,
                "usdt_borrowed": 2.1,
                "jusdt_balance": 0.0,
                "jusdt_underlying": 0.0,
            },
            {
                "trx_borrow_ratio": 0.2,
                "trx_collateral_ratio": 0.8,
                "usdt_borrow_ratio": 0.4,
                "usdt_collateral_ratio": 0.6,
                "selected_market": "jtrx",
                "observed_account": "acct",
                "trx_borrowed": 2.0,
                "jtrx_balance": 11.0,
                "jtrx_underlying": 1.4,
                "usdt_borrowed": 3.1,
                "jusdt_balance": 1.0,
                "jusdt_underlying": 0.2,
            },
        ]

        args = SimpleNamespace(
            action_sequence="mint,borrow",
            intent_prefix="nile-adaptive",
            max_cycles=2,
            interval_s=0,
            stop_on_failure=True,
            strategy_mode="adaptive",
            concise_output=True,
            repay_threshold=0.7,
            borrow_threshold=0.2,
            repay_collateral_ratio_below=None,
            borrow_collateral_ratio_above=None,
            config="cfg.json",
            amount_sun=1,
        )

        rc = _run_autonomous_jtrx(args)

        self.assertEqual(rc, 0)
        self.assertEqual(mock_metrics.call_count, 2)
        self.assertEqual(_mock_run_jtrx.call_count, 4)

        concise_calls = [
            c for c in mock_get_logger.return_value.info.call_args_list
            if c.args and isinstance(c.args[0], str) and c.args[0].startswith("Adaptive cycle metrics")
        ]
        self.assertEqual(len(concise_calls), 2)
        self.assertAlmostEqual(float(concise_calls[0].args[9]), 1.0)
        self.assertAlmostEqual(float(concise_calls[0].args[12]), 2.1)
        self.assertAlmostEqual(float(concise_calls[1].args[9]), 2.0)
        self.assertAlmostEqual(float(concise_calls[1].args[12]), 3.1)
        self.assertAlmostEqual(float(concise_calls[0].args[11]), 1.2)
        self.assertAlmostEqual(float(concise_calls[1].args[11]), 1.4)
        self.assertAlmostEqual(float(concise_calls[1].args[14]), 0.2)

        delimiter_calls = [
            c for c in mock_get_logger.return_value.info.call_args_list
            if c.args and c.args[0] == "======"
        ]
        self.assertEqual(len(delimiter_calls), 2)

    @patch("openclaw_agent.turnkey.__main__.monitor_account_health")
    @patch("openclaw_agent.turnkey.__main__.load_turnkey_config")
    def test_derive_metrics_prefers_active_borrower_account_with_exposure(self, mock_load_cfg, mock_monitor):
        mock_load_cfg.return_value = SimpleNamespace(
            borrower_account_mode="justlend_ops_module",
            addresses=SimpleNamespace(
                jtrx_market="jtrx",
                jusdt_market="TGQND9cgWQKeCx27C6Ciq398pCqgrjaFqU",
                justlend_ops_module="ops",
                strategy_module="strategy",
                treasury="treasury",
            ),
        )
        mock_monitor.side_effect = [
            {"markets": [{"market": "jtrx", "jtoken_balance": 0, "borrow_balance": 0, "exchange_rate_mantissa": 10**18}]},
            {"markets": [{"market": "jtrx", "jtoken_balance": 50, "borrow_balance": 150, "exchange_rate_mantissa": 10**18}]},
        ]

        args = SimpleNamespace(owner_address="owner", config="cfg.json")
        out = _derive_metrics_for_autonomous_cycle(args)

        self.assertEqual(out["observed_source"], "justlend_ops_module")
        self.assertEqual(out["observed_account"], "ops")
        self.assertAlmostEqual(out["borrow_ratio"], 3.0)
        self.assertAlmostEqual(out["collateral_ratio"], 0.0)


    @patch("openclaw_agent.turnkey.__main__.monitor_account_health")
    @patch("openclaw_agent.turnkey.__main__.load_turnkey_config")
    def test_derive_metrics_converts_jtoken_to_underlying_with_exchange_rate(self, mock_load_cfg, mock_monitor):
        mock_load_cfg.return_value = SimpleNamespace(
            borrower_account_mode="justlend_ops_module",
            addresses=SimpleNamespace(
                jtrx_market="jtrx",
                jusdt_market="TGQND9cgWQKeCx27C6Ciq398pCqgrjaFqU",
                justlend_ops_module="ops",
                strategy_module="strategy",
                treasury="treasury",
            ),
        )
        # 89 jTRX collateral where exchange rate implies ~1 TRX underlying collateral exposure.
        mock_monitor.return_value = {
            "markets": [{
                "market": "jtrx",
                "jtoken_balance": 89_00000000,
                "borrow_balance": 1_000000,
                "exchange_rate_mantissa": int((1e18) / 8900),
            }]
        }

        args = SimpleNamespace(owner_address=None, config="cfg.json")
        out = _derive_metrics_for_autonomous_cycle(args)

        self.assertAlmostEqual(out["collateral_underlying_balance"], 1_000000, places=3)
        self.assertAlmostEqual(out["borrow_ratio"], 1.0, places=6)
        self.assertAlmostEqual(out["collateral_ratio"], 0.0, places=6)


    @patch("openclaw_agent.turnkey.__main__.monitor_account_health")
    @patch("openclaw_agent.turnkey.__main__.load_turnkey_config")
    def test_derive_metrics_reports_borrowed_and_wallet_jtoken_balances(self, mock_load_cfg, mock_monitor):
        mock_load_cfg.return_value = SimpleNamespace(
            borrower_account_mode="treasury",
            addresses=SimpleNamespace(
                jtrx_market="jtrx",
                jusdt_market="TGQND9cgWQKeCx27C6Ciq398pCqgrjaFqU",
                justlend_ops_module="ops",
                strategy_module="strategy",
                treasury="treasury",
            ),
        )
        mock_monitor.return_value = {
            "markets": [
                {
                    "market": "jtrx",
                    "jtoken_balance": 3_220_74000000,
                    "borrow_balance": 36_000000,
                    "exchange_rate_mantissa": 10**16,
                },
                {
                    "market": "TGQND9cgWQKeCx27C6Ciq398pCqgrjaFqU",
                    "jtoken_balance": 0,
                    "borrow_balance": 2_100000,
                    "exchange_rate_mantissa": 10**18,
                },
            ]
        }

        args = SimpleNamespace(owner_address=None, config="cfg.json")
        out = _derive_metrics_for_autonomous_cycle(args)

        self.assertAlmostEqual(out["trx_borrowed"], 36.0)
        self.assertAlmostEqual(out["jtrx_balance"], 3220.74)
        self.assertAlmostEqual(out["usdt_borrowed"], 2.1)
        self.assertAlmostEqual(out["jusdt_balance"], 0.0)
        self.assertAlmostEqual(out["jtrx_underlying"], 3220.74)
        self.assertAlmostEqual(out["jusdt_underlying"], 0.0)

    @patch("openclaw_agent.turnkey.__main__.monitor_account_health")
    @patch("openclaw_agent.turnkey.__main__.load_turnkey_config")
    def test_derive_metrics_reports_jusdt_supply_even_when_no_usdt_borrow(self, mock_load_cfg, mock_monitor):
        mock_load_cfg.return_value = SimpleNamespace(
            borrower_account_mode="treasury",
            addresses=SimpleNamespace(
                jtrx_market="jtrx",
                jusdt_market="TGQND9cgWQKeCx27C6Ciq398pCqgrjaFqU",
                justlend_ops_module="ops",
                strategy_module="strategy",
                treasury="treasury",
            ),
        )
        mock_monitor.return_value = {
            "markets": [
                {
                    "market": "jtrx",
                    "jtoken_balance": 3_220_74000000,
                    "borrow_balance": 10_000000,
                    "exchange_rate_mantissa": 10**16,
                },
                {
                    "market": "TGQND9cgWQKeCx27C6Ciq398pCqgrjaFqU",
                    "jtoken_balance": 2_100_00000000,
                    "borrow_balance": 0,
                    "exchange_rate_mantissa": 10**16,
                },
            ]
        }

        args = SimpleNamespace(owner_address=None, config="cfg.json")
        out = _derive_metrics_for_autonomous_cycle(args)

        self.assertAlmostEqual(out["usdt_borrowed"], 0.0)
        self.assertAlmostEqual(out["jusdt_balance"], 2100.0)
        self.assertAlmostEqual(out["jusdt_underlying"], 2100.0)

    def test_maybe_sweep_ops_to_treasury_triggers_when_ratio_exceeded(self):
        cfg = SimpleNamespace(
            rpc_url="http://rpc",
            addresses=SimpleNamespace(justlend_ops_module="ops", treasury="treasury"),
            allowed_assets={"USDT": "usdt-token"},
        )

        mock_broadcaster = unittest.mock.MagicMock()
        mock_broadcaster.broadcast_ops_sweep_trx.return_value = SimpleNamespace(txid="trx-tx")
        mock_broadcaster.broadcast_ops_sweep_token.return_value = SimpleNamespace(txid="usdt-tx")

        with patch("openclaw_agent.turnkey.__main__._read_trx_balance_sun", side_effect=[200, 1000]),              patch("openclaw_agent.turnkey.__main__._read_trc20_balance", side_effect=[70, 500]),              patch("openclaw_agent.turnkey.__main__.logging.getLogger") as mock_get_logger:
            out = maybe_sweep_ops_to_treasury(
                cfg=cfg,
                owner_address="owner",
                threshold_ratio=0.1,
                broadcaster=mock_broadcaster,
                log=mock_get_logger.return_value,
            )

        self.assertEqual(len(out["swept"]), 2)
        mock_broadcaster.broadcast_ops_sweep_trx.assert_called_once_with(owner_address="owner", ops_module="ops")
        mock_broadcaster.broadcast_ops_sweep_token.assert_called_once_with(owner_address="owner", ops_module="ops", token_address="usdt-token")

    def test_maybe_sweep_ops_to_treasury_skips_when_not_exceeded(self):
        cfg = SimpleNamespace(
            rpc_url="http://rpc",
            addresses=SimpleNamespace(justlend_ops_module="ops", treasury="treasury"),
            allowed_assets={"USDT": "usdt-token"},
        )

        mock_broadcaster = unittest.mock.MagicMock()

        with patch("openclaw_agent.turnkey.__main__._read_trx_balance_sun", side_effect=[50, 1000]),              patch("openclaw_agent.turnkey.__main__._read_trc20_balance", side_effect=[40, 500]),              patch("openclaw_agent.turnkey.__main__.logging.getLogger") as mock_get_logger:
            out = maybe_sweep_ops_to_treasury(
                cfg=cfg,
                owner_address="owner",
                threshold_ratio=0.1,
                broadcaster=mock_broadcaster,
                log=mock_get_logger.return_value,
            )

        self.assertEqual(out["swept"], [])
        mock_broadcaster.broadcast_ops_sweep_trx.assert_not_called()
        mock_broadcaster.broadcast_ops_sweep_token.assert_not_called()

    def test_normalize_underlying_asset_maps_known_address_to_symbol(self):
        out = _normalize_underlying_asset(
            "TQHyk9zmKKdqs9Q5NFj8ervEWo1paDre8L",
            {"USDT": "TQHyk9zmKKdqs9Q5NFj8ervEWo1paDre8L"},
        )
        self.assertEqual(out, "USDT")



    @patch("openclaw_agent.turnkey.__main__._run_smart_router_swap", return_value=0)
    @patch("sys.argv", ["openclaw-agent", "run-smart-router-swap", "--smart-router", "TXpEUUL1hUdFvMeJYqh3htmMzJ1LQVrNVf", "--token-in", "TXxNwTPmW8ABBnVPMVGX9ns5d2ZfoP1vy7", "--token-out", "TWVFxoeLsFWMsdN4QPcpTPoZi98stSNaDA", "--amount-in", "1000", "--to-address", "TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY", "--deadline", str(int(time.time()) + 1200), "--wait-for-receipt", "--receipt-timeout-s", "45"])
    def test_main_parses_smart_router_wait_for_receipt_flags(self, mock_run):
        rc = main()
        self.assertEqual(rc, 0)
        args = mock_run.call_args.args[0]
        self.assertTrue(args.wait_for_receipt)
        self.assertEqual(args.receipt_timeout_s, 45)




    @patch("openclaw_agent.turnkey.__main__._run_smart_router_swap", return_value=0)
    @patch("sys.argv", ["openclaw-agent", "run-smart-router-swap", "--smart-router", "TXpEUUL1hUdFvMeJYqh3htmMzJ1LQVrNVf", "--token-in", "TXxNwTPmW8ABBnVPMVGX9ns5d2ZfoP1vy7", "--token-out", "TWVFxoeLsFWMsdN4QPcpTPoZi98stSNaDA", "--amount-in", "1000", "--to-address", "TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY", "--deadline", str(int(time.time()) + 1200), "--auto-amount-out-min", "--quote-slippage-bps", "80"])
    def test_main_parses_smart_router_auto_amount_out_min_flags(self, mock_run):
        rc = main()
        self.assertEqual(rc, 0)
        args = mock_run.call_args.args[0]
        self.assertTrue(args.auto_amount_out_min)
        self.assertEqual(args.quote_slippage_bps, 80)

    @patch("openclaw_agent.turnkey.__main__.logging.getLogger")
    @patch("sys.argv", ["openclaw-agent", "run-smart-router-swap", "--smart-router", "TXpEUUL1hUdFvMeJYqh3htmMzJ1LQVrNVf", "--token-in", "TXxNwTPmW8ABBnVPMVGX9ns5d2ZfoP1vy7", "--token-out", "TWVFxoeLsFWMsdN4QPcpTPoZi98stSNaDA", "--amount-in", "1000", "--to-address", "TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY", "--deadline", str(int(time.time()) + 1200), "--receipt-timeout-s", "0"])
    def test_main_rejects_non_positive_smart_router_receipt_timeout(self, mock_get_logger):
        rc = main()
        self.assertEqual(rc, 2)
        mock_get_logger.return_value.error.assert_called_once()

    @patch("openclaw_agent.turnkey.__main__.logging.getLogger")
    @patch("sys.argv", ["openclaw-agent", "run-smart-router-swap", "--smart-router", "TXpEUUL1hUdFvMeJYqh3htmMzJ1LQVrNVf", "--token-in", "TXxNwTPmW8ABBnVPMVGX9ns5d2ZfoP1vy7", "--token-out", "TWVFxoeLsFWMsdN4QPcpTPoZi98stSNaDA", "--amount-in", "1000", "--to-address", "TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY", "--deadline", "1"])
    def test_main_rejects_expired_smart_router_deadline(self, mock_get_logger):
        rc = main()
        self.assertEqual(rc, 2)
        mock_get_logger.return_value.error.assert_called_once()

    @patch("openclaw_agent.turnkey.__main__.run_smart_router_swap", return_value={"status": "submitted", "txid": "abc"})
    @patch("openclaw_agent.turnkey.__main__.ensure_sufficient_trc20_balance", return_value={"token_in_balance": "1000"})
    @patch("openclaw_agent.turnkey.__main__.load_turnkey_config", return_value=SimpleNamespace(addresses=SimpleNamespace(treasury="TTreasury111111111111111111111111111")))
    @patch("sys.argv", ["openclaw-agent", "run-smart-router-swap", "--smart-router", "TXpEUUL1hUdFvMeJYqh3htmMzJ1LQVrNVf", "--token-in", "TXxNwTPmW8ABBnVPMVGX9ns5d2ZfoP1vy7", "--token-out", "TWVFxoeLsFWMsdN4QPcpTPoZi98stSNaDA", "--amount-in", "1000", "--to-address", "TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY", "--deadline", str(int(time.time()) + 1200)])
    def test_main_runs_smart_router_preflight_balance_check(self, _mock_cfg, mock_preflight, mock_run):
        rc = main()
        self.assertEqual(rc, 0)
        mock_preflight.assert_called_once()
        mock_run.assert_called_once()

    @patch("openclaw_agent.turnkey.__main__.ensure_sufficient_trc20_balance", side_effect=SmartRouterSwapError("insufficient_token_in_balance"))
    @patch("openclaw_agent.turnkey.__main__.logging.getLogger")
    @patch("sys.argv", ["openclaw-agent", "run-smart-router-swap", "--smart-router", "TXpEUUL1hUdFvMeJYqh3htmMzJ1LQVrNVf", "--token-in", "TXxNwTPmW8ABBnVPMVGX9ns5d2ZfoP1vy7", "--token-out", "TWVFxoeLsFWMsdN4QPcpTPoZi98stSNaDA", "--amount-in", "1000", "--to-address", "TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY", "--deadline", str(int(time.time()) + 1200)])
    def test_main_returns_error_when_smart_router_preflight_fails(self, mock_get_logger, _mock_preflight):
        rc = main()
        self.assertEqual(rc, 2)
        mock_get_logger.return_value.error.assert_called_once()


    @patch("openclaw_agent.turnkey.__main__._run_smart_router_swap", side_effect=SmartRouterSwapError("swap_reverted:REVERT opcode executed", diagnostics={"context": {"txid": "abc123"}}))
    @patch("openclaw_agent.turnkey.__main__.logging.getLogger")
    @patch("sys.argv", ["openclaw-agent", "run-smart-router-swap", "--smart-router", "TXpEUUL1hUdFvMeJYqh3htmMzJ1LQVrNVf", "--token-in", "TXxNwTPmW8ABBnVPMVGX9ns5d2ZfoP1vy7", "--token-out", "TWVFxoeLsFWMsdN4QPcpTPoZi98stSNaDA", "--amount-in", "1000", "--to-address", "TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY", "--deadline", str(int(time.time()) + 1200)])
    def test_main_logs_smart_router_diagnostics_when_available(self, mock_get_logger, _mock_run_swap):
        rc = main()
        self.assertEqual(rc, 2)
        logger = mock_get_logger.return_value
        self.assertEqual(logger.error.call_count, 2)

    @patch("openclaw_agent.turnkey.__main__._run_jtrx", side_effect=TronBroadcastError("boom"))
    @patch("openclaw_agent.turnkey.__main__.logging.getLogger")
    @patch("sys.argv", ["openclaw-agent", "run-jtrx", "--intent-id", "i", "--action", "mint", "--amount-sun", "1", "--requested-size-nav-pct", "0.1", "--daily-cumulative-nav-pct", "0.1"])
    def test_main_handles_broadcast_error_without_traceback(self, mock_get_logger, _mock_run_jtrx):
        rc = main()
        self.assertEqual(rc, 3)
        mock_get_logger.return_value.error.assert_called_once()

    @patch("openclaw_agent.turnkey.__main__._run_jtrx", side_effect=ValueError("invalid_approval_identity_entry:x"))
    @patch("openclaw_agent.turnkey.__main__.logging.getLogger")
    @patch("sys.argv", ["openclaw-agent", "run-jtrx", "--intent-id", "i", "--action", "mint", "--amount-sun", "1", "--requested-size-nav-pct", "0.1", "--daily-cumulative-nav-pct", "0.1"])
    def test_main_handles_invalid_input_value_error(self, mock_get_logger, _mock_run_jtrx):
        rc = main()
        self.assertEqual(rc, 2)
        mock_get_logger.return_value.error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
