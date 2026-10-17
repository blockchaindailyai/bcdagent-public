import httpx
import os
import tempfile
import unittest
from unittest import mock

from scraper_bot.sources.btc_source import BtcSource, SATOSHIS_PER_BTC
from scraper_bot.sources.price_backoff import reset_price_lookup_backoff_for_tests


class BtcSourceTest(unittest.TestCase):
    def setUp(self):
        reset_price_lookup_backoff_for_tests()

    def tearDown(self):
        reset_price_lookup_backoff_for_tests()

    def test_fetch_emits_major_transfer(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["BTC_STATE_PATH"] = os.path.join(td, "btc_state.json")
                os.environ["BTC_LOOKBACK_BLOCKS_ON_BOOT"] = "1"
                os.environ["BTC_MIN_TRANSFER_USD"] = "1000000"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = BtcSource()
                source._get_tip_block_height = lambda client: 100
                source._get_btc_usd_price = lambda client: 100000.0
                source._get_block_hash_by_height = lambda client, block_height: "blockhash"
                source._get_block_txids_with_retries = lambda client, block_hash: ["tx1"]
                source._get_transaction = lambda client, txid: {
                    "status": {"block_time": 1735689600, "block_hash": "abc"},
                    "vin": [
                        {
                            "prevout": {
                                "scriptpubkey_address": "bc1qfrom",
                            }
                        }
                    ],
                    "vout": [
                        {
                            "scriptpubkey_address": "bc1qto",
                            "value": 20 * SATOSHIS_PER_BTC,
                        }
                    ],
                }

                items = source.fetch()
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0].source, "btc")
                self.assertEqual(items[0].data["amountBtc"], 20)
                self.assertGreaterEqual(items[0].data["amountUsd"], 1000000)
        finally:
            os.environ.clear()
            os.environ.update(old_env)



    def test_fetch_cold_start_begins_at_tip_not_lookback_window(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["BTC_STATE_PATH"] = os.path.join(td, "btc_state.json")
                os.environ["BTC_LOOKBACK_BLOCKS_ON_BOOT"] = "500"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = BtcSource()
                source._get_tip_block_height = lambda client: 100
                source._get_btc_usd_price = lambda client: 100000.0

                seen_blocks = []

                def _hash(client, block_height):
                    seen_blocks.append(block_height)
                    return f"hash-{block_height}"

                source._get_block_hash_by_height = _hash
                source._get_block_txids_with_retries = lambda client, block_hash: []

                items = source.fetch()
                self.assertEqual(items, [])
                self.assertEqual(seen_blocks, [100])
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_fetch_respects_cycle_time_budget_and_persists_progress(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["BTC_STATE_PATH"] = os.path.join(td, "btc_state.json")
                os.environ["BTC_LOOKBACK_BLOCKS_ON_BOOT"] = "5"
                os.environ["BTC_CYCLE_TIME_BUDGET_SECONDS"] = "1"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = BtcSource()
                source.state_path.write_text('{"last_scanned_block": 5}', encoding="utf-8")
                source._get_tip_block_height = lambda client: 10
                source._get_btc_usd_price = lambda client: 100000.0
                source._get_block_hash_by_height = lambda client, block_height: f"hash-{block_height}"
                source._get_block_txids_with_retries = lambda client, block_hash: []

                monotonic_values = iter([0.0, 0.0, 0.3, 1.2])
                with mock.patch("scraper_bot.sources.btc_source.time.monotonic", side_effect=lambda: next(monotonic_values)):
                    items = source.fetch()

                self.assertEqual(items, [])
                state = source.state_path.read_text(encoding="utf-8")
                self.assertIn('"last_scanned_block": 7', state)
        finally:
            os.environ.clear()
            os.environ.update(old_env)


    def test_fetch_processes_all_transactions_with_parallel_workers(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["BTC_STATE_PATH"] = os.path.join(td, "btc_state.json")
                os.environ["BTC_PARALLEL_TX_FETCH_WORKERS"] = "2"
                os.environ["BTC_MIN_TRANSFER_USD"] = "1"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = BtcSource()
                source._get_tip_block_height = lambda client: 100
                source._get_btc_usd_price = lambda client: 100000.0
                source._get_block_hash_by_height = lambda client, block_height: "blockhash"
                source._get_block_txids_with_retries = lambda client, block_hash: ["tx1", "tx2", "tx3"]

                seen_txids = []

                def _tx(client, txid):
                    seen_txids.append(txid)
                    return {
                        "status": {"block_time": 1735689600, "block_hash": "abc"},
                        "vin": [{"prevout": {"scriptpubkey_address": "bc1qfrom"}}],
                        "vout": [{"scriptpubkey_address": "bc1qto", "value": SATOSHIS_PER_BTC}],
                    }

                source._get_transaction = _tx

                source.fetch()
                self.assertEqual(sorted(seen_txids), ["tx1", "tx2", "tx3"])
        finally:
            os.environ.clear()
            os.environ.update(old_env)


    def test_fetch_does_not_advance_state_on_partial_tx_fetch_failure(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["BTC_STATE_PATH"] = os.path.join(td, "btc_state.json")
                os.environ["BTC_PARALLEL_TX_FETCH_WORKERS"] = "4"
                os.environ["BTC_MIN_TRANSFER_USD"] = "1"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = BtcSource()
                source.state_path.write_text('{"last_scanned_block": 100}', encoding="utf-8")
                source._get_tip_block_height = lambda client: 101
                source._get_btc_usd_price = lambda client: 100000.0
                source._get_block_hash_by_height = lambda client, block_height: "blockhash"
                source._get_block_txids_with_retries = lambda client, block_hash: ["tx-ok", "tx-fail"]

                def _tx_with_failure(client, txid):
                    if txid == "tx-fail":
                        raise httpx.HTTPStatusError(
                            "rate limited",
                            request=httpx.Request("GET", f"https://mempool.space/api/tx/{txid}"),
                            response=httpx.Response(429),
                        )
                    return {
                        "status": {"block_time": 1735689600, "block_hash": "abc"},
                        "vin": [{"prevout": {"scriptpubkey_address": "bc1qfrom"}}],
                        "vout": [{"scriptpubkey_address": "bc1qto", "value": SATOSHIS_PER_BTC}],
                    }

                source._get_transaction = _tx_with_failure

                items = source.fetch()
                self.assertEqual(items, [])
                state = source.state_path.read_text(encoding="utf-8")
                self.assertIn('"last_scanned_block": 100', state)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_get_btc_usd_price_uses_global_backoff_after_429(self):
        old_env = dict(os.environ)
        try:
            os.environ["BTC_PRICE_CACHE_TTL_SECONDS"] = "0"
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            source = BtcSource()
            source._cached_btc_usd = 78000.0
            source._cached_btc_usd_monotonic = 0.0
            calls = {"count": 0}

            class _Client:
                def get(self, *args, **kwargs):
                    calls["count"] += 1
                    request = httpx.Request("GET", "https://api.coingecko.com/api/v3/simple/price")
                    response = httpx.Response(429, request=request)
                    raise httpx.HTTPStatusError("rate limited", request=request, response=response)

            first = source._get_btc_usd_price(_Client())
            second = source._get_btc_usd_price(_Client())
            self.assertEqual(first, 78000.0)
            self.assertEqual(second, 78000.0)
            self.assertEqual(calls["count"], 1)
        finally:
            reset_price_lookup_backoff_for_tests()
            os.environ.clear()
            os.environ.update(old_env)


if __name__ == "__main__":
    unittest.main()
