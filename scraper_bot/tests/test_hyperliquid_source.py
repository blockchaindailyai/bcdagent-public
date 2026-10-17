import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scraper_bot.sources.hyperliquid_source import HyperliquidSource


class HyperliquidSourceTest(unittest.TestCase):
    def test_fetch_emits_recent_trades_only(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = str(Path(td) / "hyperliquid_state.json")
                os.environ.update(
                    {
                        "INTEL_API_BASE": "http://localhost",
                        "INGEST_TOKEN": "x",
                        "HYPERLIQUID_STATE_PATH": state_path,
                        "HYPERLIQUID_MARKETS": "BTC",
                        "HYPERLIQUID_MAJOR_TRADE_MIN_USD": "1000000",
                    }
                )

                source = HyperliquidSource()
                now_ms = 4102444800000
                sample = [
                    {
                        "coin": "BTC",
                        "side": "A",
                        "px": "70000",
                        "sz": "20",
                        "time": now_ms,
                        "hash": "0xabc",
                        "tid": 111,
                        "users": ["0x1", "0x2"],
                    },
                    {
                        "coin": "BTC",
                        "side": "B",
                        "px": "70000",
                        "sz": "0.2",
                        "time": now_ms + 1000,
                        "hash": "0xdef",
                        "tid": 112,
                        "users": ["0x3", "0x2"],
                    },
                ]

                with patch.object(source, "_fetch_recent_trades", return_value=sample):
                    items = source.fetch()

                self.assertEqual(len(items), 2)
                self.assertTrue(all(item.item_type == "onchain_trade" for item in items))
                self.assertEqual(items[0].data["tradeId"], 111)
                self.assertEqual(items[1].data["tradeId"], 112)
                self.assertNotIn("market_flow_snapshot", [item.item_type for item in items])
                self.assertIs(items[0].data["isMajor"], True)
                self.assertIs(items[1].data["isMajor"], False)
                self.assertEqual(items[0].data["kind"], "recent_trade")
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_fetch_skips_previously_seen_trade_ids(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = Path(td) / "hyperliquid_state.json"
                state_path.write_text('{"seen_trade_ids":[111]}', encoding="utf-8")
                os.environ.update(
                    {
                        "INTEL_API_BASE": "http://localhost",
                        "INGEST_TOKEN": "x",
                        "HYPERLIQUID_STATE_PATH": str(state_path),
                        "HYPERLIQUID_MARKETS": "BTC",
                    }
                )
                source = HyperliquidSource()

                sample = [
                    {
                        "coin": "BTC",
                        "side": "A",
                        "px": "70000",
                        "sz": "20",
                        "time": 1730000000000,
                        "hash": "0xabc",
                        "tid": 111,
                        "users": ["0x1", "0x2"],
                    },
                ]

                with patch.object(source, "_fetch_recent_trades", return_value=sample):
                    items = source.fetch()

                self.assertEqual(items, [])
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_state_only_stores_seen_trade_ids(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = Path(td) / "hyperliquid_state.json"
                os.environ.update(
                    {
                        "INTEL_API_BASE": "http://localhost",
                        "INGEST_TOKEN": "x",
                        "HYPERLIQUID_STATE_PATH": str(state_path),
                        "HYPERLIQUID_MARKETS": "BTC",
                        "HYPERLIQUID_MAX_SEEN_TRADE_IDS": "2",
                    }
                )

                source = HyperliquidSource()

                sample = [
                    {
                        "coin": "BTC",
                        "side": "A",
                        "px": "100",
                        "sz": "1",
                        "time": 1,
                        "hash": "0x1",
                        "tid": 1,
                        "users": ["0x1"],
                    },
                    {
                        "coin": "BTC",
                        "side": "A",
                        "px": "100",
                        "sz": "1",
                        "time": 2,
                        "hash": "0x2",
                        "tid": 2,
                        "users": ["0x1"],
                    },
                    {
                        "coin": "BTC",
                        "side": "A",
                        "px": "100",
                        "sz": "1",
                        "time": 3,
                        "hash": "0x3",
                        "tid": 3,
                        "users": ["0x1"],
                    },
                ]

                with patch.object(source, "_fetch_recent_trades", return_value=sample):
                    source.fetch()

                payload = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(sorted(payload.keys()), ["seen_trade_ids"])
                self.assertEqual(payload["seen_trade_ids"], [2, 3])
        finally:
            os.environ.clear()
            os.environ.update(old_env)


if __name__ == "__main__":
    unittest.main()
