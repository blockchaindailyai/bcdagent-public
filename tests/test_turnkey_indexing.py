import json
import tempfile
import unittest
from types import SimpleNamespace

from openclaw_agent.turnkey.indexing import LocalIndexer, NoopIndexer, SubgraphIndexer, build_indexer, resolve_indexer_endpoint


class TurnkeyIndexerResolutionTests(unittest.TestCase):
    def test_resolve_indexer_uses_explicit_subgraph_url(self):
        cfg = SimpleNamespace(
            subgraph_indexer_url="https://indexer.nile.example/events",
            use_mcp_server_tron=True,
            mcp_server_tron_url="http://127.0.0.1:3000",
        )

        self.assertEqual(
            resolve_indexer_endpoint(cfg),
            "https://indexer.nile.example/events",
        )

    def test_resolve_indexer_defaults_to_mcp_ops_endpoint(self):
        cfg = SimpleNamespace(
            subgraph_indexer_url="",
            use_mcp_server_tron=True,
            mcp_server_tron_url="http://127.0.0.1:3000/",
        )

        self.assertEqual(
            resolve_indexer_endpoint(cfg),
            "http://127.0.0.1:3000/ops/indexer/events",
        )

    def test_build_indexer_noop_when_not_configured(self):
        cfg = SimpleNamespace(
            subgraph_indexer_url="",
            use_mcp_server_tron=False,
            mcp_server_tron_url="",
            subgraph_indexer_local_path="",
        )

        indexer = build_indexer(cfg)
        self.assertIsInstance(indexer, NoopIndexer)

    def test_build_indexer_uses_local_indexer_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            local_file = f"{tmp}/events.jsonl"
            cfg = SimpleNamespace(
                subgraph_indexer_url="",
                use_mcp_server_tron=False,
                mcp_server_tron_url="",
                subgraph_indexer_local_path=local_file,
            )

            indexer = build_indexer(cfg)
            self.assertIsInstance(indexer, LocalIndexer)
            self.assertTrue(indexer.emit_execution_event({"event": "unit_test"}))
            with open(local_file, "r", encoding="utf-8") as f:
                payload = json.loads(f.readline())
            self.assertEqual(payload["event_payload"]["event"], "unit_test")

    def test_build_indexer_returns_subgraph_indexer_for_resolved_endpoint(self):
        cfg = SimpleNamespace(
            subgraph_indexer_url="",
            use_mcp_server_tron=True,
            mcp_server_tron_url="http://127.0.0.1:3000",
            subgraph_indexer_local_path="openclaw_agent/data/subgraph/events.jsonl",
        )

        indexer = build_indexer(cfg)
        self.assertIsInstance(indexer, SubgraphIndexer)
        self.assertEqual(indexer.endpoint, "http://127.0.0.1:3000/ops/indexer/events")


if __name__ == "__main__":
    unittest.main()
