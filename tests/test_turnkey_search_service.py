import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from openclaw_agent.turnkey.__main__ import main
from openclaw_agent.turnkey.config import load_turnkey_config
from openclaw_agent.turnkey.search_service import search_events_local, search_reviewer_operator_events


class TurnkeySearchServiceTests(unittest.TestCase):
    def test_search_events_local_filters_reviewer(self):
        with tempfile.TemporaryDirectory() as td:
            events_file = Path(td) / "events.jsonl"
            events_file.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "event_id": "evt-1",
                                "observed_at": "2026-01-01T00:00:00Z",
                                "event": "jtrx_audit_bundle",
                                "owner": "Toperator",
                                "plan_id": "plan-1",
                                "intent_id": "intent-1",
                                "approval_artifacts": {
                                    "approvers": ["alice", "bob"],
                                    "evidence": [
                                        {"approver_id": "alice", "role": "reviewer"},
                                        {"approver_id": "bob", "role": "operator"},
                                    ],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "event_id": "evt-2",
                                "observed_at": "2026-01-01T00:00:01Z",
                                "event": "jtrx_execution_receipt",
                                "owner": "Toperator",
                                "plan_id": "plan-2",
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            payload = search_events_local(str(events_file), query="alice", role_filter="reviewer", limit=10)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["total_hits"], 1)
            self.assertEqual(payload["hits"][0]["plan_id"], "plan-1")

    def test_search_reviewer_operator_events_falls_back_to_local(self):
        cfg = load_turnkey_config("openclaw_agent/config/testnet_turnkey.json")
        with tempfile.TemporaryDirectory() as td:
            events_file = Path(td) / "events.jsonl"
            events_file.write_text("", encoding="utf-8")
            cfg = cfg.__class__(
                **{
                    **cfg.__dict__,
                    "use_mcp_server_tron": False,
                    "subgraph_indexer_local_path": str(events_file),
                }
            )
            payload = search_reviewer_operator_events(cfg, query="", role_filter="all", limit=5)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["source"], "local")

    def test_cli_search_reviewer_operator_outputs_json(self):
        argv = [
            "prog",
            "search-reviewer-operator",
            "--config",
            "openclaw_agent/config/testnet_turnkey.json",
            "--limit",
            "1",
        ]
        with patch("sys.argv", argv), patch("sys.stdout", new_callable=StringIO) as stdout:
            code = main()
        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["ok"])


if __name__ == "__main__":
    unittest.main()
