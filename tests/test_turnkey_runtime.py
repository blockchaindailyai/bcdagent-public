import json
import os
import tempfile
import unittest
from unittest.mock import patch

from openclaw_agent.turnkey.config import load_turnkey_config
from openclaw_agent.turnkey.runtime import JustLendMonitor, build_runtime_status, check_jtrx_borrow_readiness, monitor_account_health


class TurnkeyConfigTests(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "OPENCLAW_MCP_SERVER_TRON_URL": "",
            "OPENCLAW_USE_MCP_SERVER_TRON": "",
            "OPENCLAW_SUBGRAPH_INDEXER_URL": "",
        },
        clear=False,
    )
    def test_load_config_and_env_overrides(self):
        payload = {
            "network": "tron-nile",
            "chain_id": "nile",
            "rpc_url": "https://nile.trongrid.io",
            "signer_rpc_url": "https://nile.trongrid.io",
            "tronscan_api": "https://nileapi.tronscan.org",
            "mcp_server_tron_url": "",
            "use_mcp_server_tron": False,
            "subgraph_indexer_url": "",
            "enable_local_signing_fallback": True,
            "addresses": {
                "treasury": "Ttreasury",
                "strategy_module": "Tmodule",
                "operator": "Toperator",
                "justlend_controller": "Tcontroller",
                "jtrx_market": "Tjtrx",
                "jusdt_market": "Tjusdt",
            },
            "allowed_assets": {"TRX": "TRX"},
        }

        with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
            json.dump(payload, tmp)
            path = tmp.name

        try:
            os.environ["OPENCLAW_STRATEGY_MODULE_ADDRESS"] = "Toverridden"
            cfg = load_turnkey_config(path)
            self.assertEqual(cfg.addresses.strategy_module, "Toverridden")
            self.assertEqual(cfg.addresses.justlend_ops_module, "Toverridden")
            self.assertEqual(cfg.addresses.treasury, "Ttreasury")
            self.assertEqual(cfg.addresses.jusdt_market, "Tjusdt")
            self.assertEqual(cfg.signer_rpc_url, "https://nile.trongrid.io")
            self.assertTrue(cfg.enable_local_signing_fallback)
            self.assertEqual(cfg.borrower_account_mode, "justlend_ops_module")
            self.assertEqual(cfg.mcp_server_tron_url, "")
            self.assertFalse(cfg.use_mcp_server_tron)
            self.assertEqual(cfg.subgraph_indexer_url, "")
            self.assertEqual(cfg.permission_id, 0)
        finally:
            os.environ.pop("OPENCLAW_STRATEGY_MODULE_ADDRESS", None)
            os.unlink(path)

    def test_permission_id_env_override(self):
        payload = {
            "network": "tron-nile",
            "chain_id": "nile",
            "rpc_url": "https://nile.trongrid.io",
            "signer_rpc_url": "https://nile.trongrid.io",
            "tronscan_api": "https://nileapi.tronscan.org",
            "enable_local_signing_fallback": True,
            "permission_id": 0,
            "addresses": {
                "treasury": "Ttreasury",
                "strategy_module": "Tmodule",
                "operator": "Toperator",
                "justlend_controller": "Tcontroller",
                "jtrx_market": "Tjtrx",
            },
            "allowed_assets": {"TRX": "TRX"},
        }

        with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
            json.dump(payload, tmp)
            path = tmp.name

        try:
            os.environ["OPENCLAW_PERMISSION_ID"] = "2"
            cfg = load_turnkey_config(path)
            self.assertEqual(cfg.permission_id, 2)
        finally:
            os.environ.pop("OPENCLAW_PERMISSION_ID", None)
            os.unlink(path)


    def test_testnet_template_defaults_permission_id_to_zero(self):
        cfg = load_turnkey_config("openclaw_agent/config/testnet_turnkey.json")
        self.assertEqual(cfg.permission_id, 0)

class TurnkeyRuntimeTests(unittest.TestCase):
    @patch("openclaw_agent.turnkey.runtime.JustLendClient.healthy", return_value=True)
    @patch("openclaw_agent.turnkey.runtime.TronRpcClient.healthy", return_value=True)
    def test_build_runtime_status_ok(self, *_mocks):
        status = build_runtime_status()
        self.assertTrue(status.chain_healthy)
        self.assertTrue(status.justlend_healthy)
        self.assertEqual(status.details["network"], "tron-nile")
        self.assertIn("active_transport", status.details)
        self.assertIn("active_transport_api_url", status.details)
        self.assertIn("mcp_server_tron_url", status.details)
        self.assertIn("use_mcp_server_tron", status.details)
        self.assertIn("subgraph_indexer_url", status.details)
        self.assertIn("subgraph_indexer_local_path", status.details)
        self.assertIn("resolved_indexer_url", status.details)


if __name__ == "__main__":
    unittest.main()


class JustLendMonitorTests(unittest.TestCase):
    def test_justlend_monitor_decodes_get_account_snapshot(self):
        monitor = JustLendMonitor("https://nile.trongrid.io")
        one = "".join([f"{v:064x}" for v in [0, 10, 20, 30]])
        monitor._post = lambda _path, _payload: {"constant_result": [one]}

        out = monitor.account_snapshots(
            "TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY",
            ["TAutUnFJhvocKb3egsma3vBEEEkznPbokk"],
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].status, "ok")
        self.assertEqual(out[0].error_code, 0)
        self.assertIsNone(out[0].error_detail)
        self.assertEqual(out[0].jtoken_balance, 10)
        self.assertEqual(out[0].borrow_balance, 20)
        self.assertEqual(out[0].exchange_rate_mantissa, 30)
        self.assertEqual(out[0].exchange_rate, 30 / 1e18)

    def test_justlend_monitor_missing_constant_result_is_rpc_error(self):
        monitor = JustLendMonitor("https://nile.trongrid.io")
        monitor._post = lambda _path, _payload: {}

        out = monitor.account_snapshots(
            "TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY",
            ["TWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW"],
        )
        self.assertEqual(out[0].status, "rpc_error")
        self.assertIsNone(out[0].error_code)
        self.assertEqual(out[0].error_detail, "missing_constant_result")

    def test_justlend_monitor_short_decode_is_decode_error(self):
        monitor = JustLendMonitor("https://nile.trongrid.io")
        monitor._post = lambda _path, _payload: {"constant_result": ["01"]}

        out = monitor.account_snapshots(
            "TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY",
            ["TAutUnFJhvocKb3egsma3vBEEEkznPbokk"],
        )
        self.assertEqual(out[0].status, "decode_error")
        self.assertIsNone(out[0].error_code)
        self.assertEqual(out[0].error_detail, "invalid_snapshot_return")

    @patch("openclaw_agent.turnkey.runtime.JustLendMonitor.account_snapshots")
    def test_monitor_account_health_payload(self, mock_snaps):
        from openclaw_agent.turnkey.runtime import MarketSnapshot

        mock_snaps.return_value = [
            MarketSnapshot(
                market="TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
                status="ok",
                error_code=0,
                error_detail=None,
                jtoken_balance=1,
                borrow_balance=2,
                exchange_rate_mantissa=3,
            )
        ]
        payload = monitor_account_health("TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY")
        self.assertEqual(payload["owner_address"], "TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY")
        self.assertIn("treasury", payload)
        self.assertEqual(payload["markets"][0]["status"], "ok")
        self.assertEqual(payload["markets"][0]["borrow_balance"], 2)
        self.assertEqual(payload["markets"][0]["exchange_rate"], 3 / 1e18)


    @patch("openclaw_agent.turnkey.runtime.JustLendMonitor.account_snapshots")
    def test_monitor_account_health_defaults_to_treasury(self, mock_snaps):
        from openclaw_agent.turnkey.runtime import MarketSnapshot

        mock_snaps.return_value = [
            MarketSnapshot(
                market="m",
                status="ok",
                error_code=0,
                error_detail=None,
                jtoken_balance=0,
                borrow_balance=0,
                exchange_rate_mantissa=0,
            )
        ]
        payload = monitor_account_health("")
        self.assertEqual(payload["owner_address"], payload["treasury"])


    @patch("openclaw_agent.turnkey.runtime.load_turnkey_config")
    @patch("openclaw_agent.turnkey.runtime.JustLendMonitor.account_snapshots")
    def test_monitor_account_health_skips_inactive_optional_jusdd_market(self, mock_snaps, mock_load):
        from types import SimpleNamespace
        from openclaw_agent.turnkey.runtime import MarketSnapshot

        mock_load.return_value = SimpleNamespace(
            addresses=SimpleNamespace(
                treasury="Ttreasury",
                jtrx_market="TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
                jusdt_market="",
                jusdd_market="",
            )
        )
        mock_snaps.return_value = [
            MarketSnapshot(
                market="TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
                status="ok",
                error_code=0,
                error_detail=None,
                jtoken_balance=1,
                borrow_balance=2,
                exchange_rate_mantissa=3,
            )
        ]

        payload = monitor_account_health("TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY")

        mock_snaps.assert_called_once_with(
            "TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY",
            ["TAutUnFJhvocKb3egsma3vBEEEkznPbokk"],
        )
        self.assertEqual(payload["markets"][0]["status"], "ok")

    @patch("openclaw_agent.turnkey.runtime.JustLendMonitor.call_bool")
    def test_check_jtrx_borrow_readiness(self, mock_call_bool):
        mock_call_bool.return_value = True
        payload = check_jtrx_borrow_readiness()
        self.assertTrue(payload["ready"])
        self.assertIn("checks", payload)


class BorrowReadinessModuleSelectionTests(unittest.TestCase):
    @patch("openclaw_agent.turnkey.runtime.load_turnkey_config")
    @patch("openclaw_agent.turnkey.runtime.JustLendMonitor.call_bool")
    def test_check_jtrx_borrow_readiness_prefers_ops_module(self, mock_call_bool, mock_load):
        from types import SimpleNamespace

        mock_call_bool.return_value = True
        mock_load.return_value = SimpleNamespace(
            rpc_url="https://nile.trongrid.io",
            borrower_account_mode="justlend_ops_module",
            addresses=SimpleNamespace(
                strategy_module="Tlegacy",
                justlend_ops_module="Topsmodule",
                jtrx_market="TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
            ),
            allowed_assets={"jTRX": "TAutUnFJhvocKb3egsma3vBEEEkznPbokk"},
        )

        payload = check_jtrx_borrow_readiness()

        self.assertTrue(payload["ready"])
        self.assertEqual(payload["active_module"], "Topsmodule")
        self.assertEqual(payload["borrower_account_mode"], "justlend_ops_module")
        self.assertIn("allowed_market_jtrx", payload["checks"])

    @patch("openclaw_agent.turnkey.runtime.load_turnkey_config")
    @patch("openclaw_agent.turnkey.runtime.JustLendMonitor.call_bool")
    def test_check_jtrx_borrow_readiness_strategy_mode_uses_selector_checks(self, mock_call_bool, mock_load):
        from types import SimpleNamespace

        mock_call_bool.return_value = True
        mock_load.return_value = SimpleNamespace(
            rpc_url="https://nile.trongrid.io",
            borrower_account_mode="strategy_module",
            addresses=SimpleNamespace(
                strategy_module="Tstrategy",
                justlend_ops_module="Topsmodule",
                jtrx_market="TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
            ),
            allowed_assets={"jTRX": "TAutUnFJhvocKb3egsma3vBEEEkznPbokk"},
        )

        payload = check_jtrx_borrow_readiness()

        self.assertTrue(payload["ready"])
        self.assertEqual(payload["active_module"], "Tstrategy")
        self.assertEqual(payload["borrower_account_mode"], "strategy_module")
        self.assertIn("selector_borrow_allowed", payload["checks"])
