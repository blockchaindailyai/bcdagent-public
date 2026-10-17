import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from openclaw_agent.turnkey.__main__ import main


class TurnkeyEndToEndZeroToOutputsTests(unittest.TestCase):
    def _write_config(self, events_path: str) -> str:
        payload = {
            "network": "tron-nile",
            "chain_id": "nile",
            "rpc_url": "https://nile.trongrid.io",
            "signer_rpc_url": "https://nile.trongrid.io",
            "tronscan_api": "https://nileapi.tronscan.org",
            "mcp_server_tron_url": "",
            "use_mcp_server_tron": False,
            "subgraph_indexer_url": "",
            "subgraph_indexer_local_path": events_path,
            "enable_local_signing_fallback": True,
            "permission_id": 2,
            "addresses": {
                "treasury": "TKCC6pVJgyeS5NSmRwZUvg1Jho3rnUNach",
                "strategy_module": "TSe38kWGzDXPiJZpd7r9EqsESeMtVX1p28",
                "justlend_ops_module": "TPBMtQEQvFHz6jw9NmxkbpTvvw4D2Lafb4",
                "operator": "TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY",
                "justlend_controller": "TPEA5FhBK1n27Dr1zdirM33M4nvpCjkdDp",
                "jtrx_market": "TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
                "jusdt_market": "TGQND9cgWQKeCx27C6Ciq398pCqgrjaFqU",
            },
            "allowed_assets": {
                "TRX": "TRX",
                "sTRX": "TX4L98ZMqWkqRuHizNCY3taxBc4FWsU3Dd",
                "jTRX": "TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
                "USDT": "TQHyk9zmKKdqs9Q5NFj8ervEWo1paDre8L",
                "jUSDT": "TGQND9cgWQKeCx27C6Ciq398pCqgrjaFqU",
            },
            "policy": {"borrower_account_mode": "justlend_ops_module"},
            "signer_wallets": {
                "execution": "TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY",
                "emergency": "TJ3BRsyhiaRV35i5EH94huozh2JCEnBLak",
                "analytics": "TKCC6pVJgyeS5NSmRwZUvg1Jho3rnUNach",
            },
        }

        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        return path

    def _run_cli(self, argv: list[str]) -> tuple[int, dict]:
        buf = io.StringIO()
        with patch("sys.argv", argv), redirect_stdout(buf):
            rc = main()
        out = buf.getvalue().strip()
        return rc, json.loads(out) if out else {}

    def test_zero_to_outputs_e2e_for_current_turnkey_capabilities(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            events_path = str(Path(tmpdir) / "events.jsonl")
            config_path = self._write_config(events_path)

            env_from_zero = {
                "OPENCLAW_RPC_URL": "https://nile.trongrid.io",
                "OPENCLAW_SIGNER_RPC_URL": "https://nile.trongrid.io",
                "OPENCLAW_EXECUTION_PRIVATE_KEY": "11" * 32,
                "OPENCLAW_EMERGENCY_PRIVATE_KEY": "22" * 32,
                "OPENCLAW_ANALYTICS_PRIVATE_KEY": "33" * 32,
                "OPENCLAW_EXECUTION_ADDRESS": "TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY",
                "OPENCLAW_EMERGENCY_ADDRESS": "TJ3BRsyhiaRV35i5EH94huozh2JCEnBLak",
                "OPENCLAW_ANALYTICS_ADDRESS": "TKCC6pVJgyeS5NSmRwZUvg1Jho3rnUNach",
                "OPENCLAW_PERMISSION_ID": "2",
                "OPENCLAW_MCP_SERVER_TRON_URL": "",
                "OPENCLAW_USE_MCP_SERVER_TRON": "false",
                "OPENCLAW_SUBGRAPH_INDEXER_URL": "",
                "OPENCLAW_SUBGRAPH_INDEXER_LOCAL_PATH": events_path,
            }

            with patch.dict(os.environ, env_from_zero, clear=True):
                # 1) Health from absolute zero: mock runtime status to avoid network dependence.
                fake_status = type(
                    "_Status",
                    (),
                    {
                        "chain_healthy": True,
                        "justlend_healthy": True,
                        "details": {"rpc": "ok", "justlend": "ok"},
                    },
                )()
                with patch("openclaw_agent.turnkey.__main__.build_runtime_status", return_value=fake_status):
                    rc_health, health = self._run_cli(
                        [
                            "openclaw-agent",
                            "health",
                            "--config",
                            config_path,
                        ]
                    )
                self.assertEqual(rc_health, 0)
                self.assertTrue(health["chain_healthy"])
                self.assertTrue(health["justlend_healthy"])

                # 2) Setup plan output should include MCP/indexer env guidance.
                rc_setup, setup = self._run_cli(
                    [
                        "openclaw-agent",
                        "setup-mcp-testnet",
                        "--config",
                        config_path,
                        "--mcp-server-tron-url",
                        "http://127.0.0.1:3000",
                    ]
                )
                self.assertEqual(rc_setup, 0)
                self.assertEqual(setup["network"], "tron-nile")
                self.assertEqual(
                    setup["required_environment_variables"]["OPENCLAW_SUBGRAPH_INDEXER_URL"],
                    "http://127.0.0.1:3000/ops/indexer/events",
                )

                # 3) Simulate one jTRX run and verify deterministic output artifacts.
                intent_id = "e2e-zero-to-output-1"
                rc_run, run_payload = self._run_cli(
                    [
                        "openclaw-agent",
                        "run-jtrx",
                        "--config",
                        config_path,
                        "--intent-id",
                        intent_id,
                        "--action",
                        "mint",
                        "--amount-sun",
                        "1000",
                        "--requested-size-nav-pct",
                        "0.2",
                        "--daily-cumulative-nav-pct",
                        "0.2",
                        "--approved-by",
                        "operator-1",
                        "--approval-identities",
                        "operator-1|did:trc8004:operator-1|operator",
                    ]
                )
                self.assertEqual(rc_run, 0)
                self.assertEqual(run_payload["receipt"]["status"], "simulated")
                self.assertTrue(run_payload["audit_bundle"]["audit_bundle_id"])
                self.assertTrue(run_payload["audit_bundle"]["audit_bundle_hash"])

                # 4) Search output should return the event emitted in step 3.
                rc_search, search = self._run_cli(
                    [
                        "openclaw-agent",
                        "search-reviewer-operator",
                        "--config",
                        config_path,
                        "--query",
                        intent_id,
                        "--role",
                        "operator",
                        "--limit",
                        "20",
                    ]
                )
                self.assertEqual(rc_search, 0)
                self.assertGreaterEqual(search["total_hits"], 1)
                self.assertTrue(any(hit["intent_id"] == intent_id for hit in search["hits"]))

                # 5) Packaging output should assert Wave 1 and Wave 2 completeness.
                output_dir = "openclaw_agent/dist/openclaw-extension-e2e-test"
                rc_pkg, pkg = self._run_cli(
                    [
                        "openclaw-agent",
                        "package-openclaw-extension",
                        "--output-dir",
                        output_dir,
                    ]
                )
                self.assertEqual(rc_pkg, 0)
                self.assertTrue(pkg["wave_1_complete"])
                self.assertTrue(pkg["wave_2_complete"])

            os.unlink(config_path)


if __name__ == "__main__":
    unittest.main()
