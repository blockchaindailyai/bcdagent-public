import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openclaw_agent.operator_client import OpenClawOperatorClient


class OperatorClientTests(unittest.TestCase):
    def test_initialize_nile_profile_sets_expected_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = OpenClawOperatorClient(root=Path(tmp))
            profile = client.initialize_nile_profile()
            self.assertEqual(profile["OPENCLAW_RPC_URL"], "https://nile.trongrid.io")
            self.assertEqual(profile["OPENCLAW_PERMISSION_ID"], "2")

    @patch("openclaw_agent.operator_client.subprocess.run")
    def test_run_turnkey_json_invokes_turnkey_module(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = json.dumps({"status": "ok"})
        mock_run.return_value.stderr = ""
        with tempfile.TemporaryDirectory() as tmp:
            client = OpenClawOperatorClient(root=Path(tmp))
            payload = client.run_turnkey_json(["health", "--config", "openclaw_agent/config/testnet_turnkey.json"])
            self.assertEqual(payload["status"], "ok")
            self.assertIn("openclaw_agent.turnkey", mock_run.call_args.args[0])

    def test_read_recent_events_uses_profile_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            events = data_root / "events.jsonl"
            events.write_text("\n".join([
                json.dumps({"event": "one"}),
                json.dumps({"event": "two"}),
            ]), encoding="utf-8")
            client = OpenClawOperatorClient(root=data_root / "operator")
            client.save_env_profile({"OPENCLAW_SUBGRAPH_INDEXER_LOCAL_PATH": str(events)})

            recent = client.read_recent_events(limit=1)
            self.assertEqual(recent, [{"event": "two"}])


if __name__ == "__main__":
    unittest.main()
