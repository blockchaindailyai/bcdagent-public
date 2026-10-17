import json
import unittest
from io import StringIO
from unittest.mock import patch

from openclaw_agent.turnkey.__main__ import main
from openclaw_agent.turnkey.config import load_turnkey_config
from openclaw_agent.turnkey.mcp_setup import build_nile_mcp_server_setup_plan


class TurnkeyMCPSetupTests(unittest.TestCase):
    def test_build_nile_mcp_server_setup_plan_defaults(self):
        cfg = load_turnkey_config("openclaw_agent/config/testnet_turnkey.json")
        plan = build_nile_mcp_server_setup_plan(cfg)

        self.assertEqual(plan.network, "tron-nile")
        self.assertTrue(plan.use_mcp_server_tron)
        self.assertEqual(plan.required_environment_variables["OPENCLAW_USE_MCP_SERVER_TRON"], "true")
        self.assertIn("operator_readiness_checklist", plan.__dict__)
        self.assertEqual(plan.subgraph_indexer_url, "http://127.0.0.1:3000/ops/indexer/events")

    def test_build_nile_mcp_server_setup_plan_custom_urls(self):
        cfg = load_turnkey_config("openclaw_agent/config/testnet_turnkey.json")
        plan = build_nile_mcp_server_setup_plan(
            cfg,
            mcp_server_tron_url="https://mcp.nile.example/",
            subgraph_indexer_url="https://indexer.nile.example/",
        )

        self.assertEqual(plan.mcp_server_tron_url, "https://mcp.nile.example")
        self.assertEqual(plan.subgraph_indexer_url, "https://indexer.nile.example")

    def test_cli_setup_mcp_testnet_outputs_json(self):
        argv = [
            "prog",
            "setup-mcp-testnet",
            "--config",
            "openclaw_agent/config/testnet_turnkey.json",
            "--mcp-server-tron-url",
            "https://mcp.nile.example",
        ]

        with patch("sys.argv", argv), patch("sys.stdout", new_callable=StringIO) as stdout:
            code = main()

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["mcp_server_tron_url"], "https://mcp.nile.example")
        self.assertTrue(payload["use_mcp_server_tron"])
        self.assertIn("investor_readiness_summary", payload)


if __name__ == "__main__":
    unittest.main()
