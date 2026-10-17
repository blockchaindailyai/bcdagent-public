import httpx
import os
import tempfile
import unittest

from scraper_bot.sources.price_backoff import reset_price_lookup_backoff_for_tests
from scraper_bot.sources.token_price import reset_token_price_backoff_for_tests
from scraper_bot.sources.tron_source import (
    TRACKED_TRC20_TOKENS,
    TronSource,
    _extract_native_transfers,
    _extract_trc20_transfers,
)


class TronSourceTest(unittest.TestCase):
    def setUp(self):
        reset_price_lookup_backoff_for_tests()
        reset_token_price_backoff_for_tests()

    def tearDown(self):
        reset_price_lookup_backoff_for_tests()
        reset_token_price_backoff_for_tests()

    def test_extract_native_transfers_only(self):
        tx = {
            "raw_data": {
                "contract": [
                    {
                        "type": "TransferContract",
                        "parameter": {
                            "value": {
                                "owner_address": "from_hex",
                                "to_address": "to_hex",
                                "amount": 123,
                            }
                        },
                    },
                    {"type": "TriggerSmartContract", "parameter": {"value": {}}},
                ]
            }
        }

        transfers = _extract_native_transfers(tx)
        self.assertEqual(
            transfers,
            [{"from": "from_hex", "to": "to_hex", "amount_sun": 123}],
        )


    def test_extract_trc20_transfers(self):
        tx = {
            "raw_data": {
                "contract": [
                    {
                        "type": "TriggerSmartContract",
                        "parameter": {
                            "value": {
                                "owner_address": "41" + "11" * 20,
                                "contract_address": "a614f803b6fd780986a42c78ec9c7f77e6ded13c",
                                "data": (
                                    "a9059cbb"
                                    + "0" * 24
                                    + "22" * 20
                                    + hex(1_000_000)[2:].rjust(64, "0")
                                ),
                            }
                        },
                    }
                ]
            }
        }

        transfers = _extract_trc20_transfers(tx)
        self.assertEqual(len(transfers), 1)
        self.assertEqual(transfers[0]["symbol"], "USDT")
        self.assertEqual(transfers[0]["amount"], 1.0)

    def test_fetch_emits_major_transfer(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = os.path.join(td, "tron_state.json")
                os.environ["TRON_STATE_PATH"] = state_path
                os.environ["TRON_LOOKBACK_BLOCKS_ON_BOOT"] = "1"
                os.environ["TRON_MIN_TRANSFER_USD"] = "200000000"
                os.environ["TRON_TRX_USD_FALLBACK"] = "0.2"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = TronSource()

                source._get_now_block_number = lambda client: 100
                source._get_trx_usd_price = lambda client: 0.2

                def fake_get_block(client, block_num):
                    if block_num != 100:
                        return {"transactions": []}
                    return {
                        "block_header": {"raw_data": {"timestamp": 1735689600000}},
                        "transactions": [
                            {
                                "txID": "abc123",
                                "raw_data": {
                                    "contract": [
                                        {
                                            "type": "TransferContract",
                                            "parameter": {
                                                "value": {
                                                    "owner_address": "from_addr",
                                                    "to_address": "to_addr",
                                                    "amount": 1_100_000_000_000_000,
                                                }
                                            },
                                        }
                                    ]
                                },
                            }
                        ],
                    }

                source._get_block = fake_get_block

                items = source.fetch()
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0].source, "tron")
                self.assertEqual(items[0].data["blockNumber"], 100)
                self.assertGreaterEqual(items[0].data["amountUsd"], 200000000)
        finally:
            os.environ.clear()
            os.environ.update(old_env)



    def test_get_trx_usd_price_uses_cache_within_ttl(self):
        old_env = dict(os.environ)
        try:
            os.environ["TRON_PRICE_CACHE_TTL_SECONDS"] = "300"
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            source = TronSource()

            calls = {"count": 0}

            class _Response:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {"tron": {"usd": 0.35}}

            class _Client:
                def get(self, *args, **kwargs):
                    calls["count"] += 1
                    return _Response()

            client = _Client()
            first = source._get_trx_usd_price(client)
            second = source._get_trx_usd_price(client)

            self.assertEqual(first, 0.35)
            self.assertEqual(second, 0.35)
            self.assertEqual(calls["count"], 1)
        finally:
            os.environ.clear()
            os.environ.update(old_env)


    def test_get_trx_usd_price_rate_limit_backoff_uses_cached_memory(self):
        old_env = dict(os.environ)
        try:
            os.environ["TRON_PRICE_CACHE_TTL_SECONDS"] = "0"
            os.environ["TRON_PRICE_RATE_LIMIT_BACKOFF_SECONDS"] = "120"
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            source = TronSource()
            source._cached_trx_usd = 0.41
            source._cached_trx_usd_monotonic = 0.0

            calls = {"count": 0}

            class _Client:
                def get(self, *args, **kwargs):
                    calls["count"] += 1
                    request = httpx.Request("GET", "https://api.coingecko.com/api/v3/simple/price")
                    response = httpx.Response(429, request=request)
                    raise httpx.HTTPStatusError("rate limited", request=request, response=response)

            client = _Client()
            first = source._get_trx_usd_price(client)
            second = source._get_trx_usd_price(client)

            self.assertEqual(first, 0.41)
            self.assertEqual(second, 0.41)
            self.assertEqual(calls["count"], 1)
        finally:
            os.environ.clear()
            os.environ.update(old_env)



    def test_get_tracked_token_prices_queries_dexscreener_once(self):
        old_env = dict(os.environ)
        try:
            os.environ["TRON_PRICE_CACHE_TTL_SECONDS"] = "300"
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            source = TronSource()
            requested_contracts = []

            class _Response:
                def raise_for_status(self):
                    return None

                def json(self):
                    return [
                        {
                            "priceUsd": "0.031",
                            "liquidity": {"usd": 1200000},
                            "baseToken": {"address": "50327c6c5a14dcade707abad2e27eb517df87ab5"},
                            "quoteToken": {"address": "t9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"},
                        }
                    ]

            class _Client:
                def get(self, *args, **kwargs):
                    requested_contracts.append(args[0])
                    return _Response()

            prices = source._get_tracked_token_prices_usd(_Client())

            self.assertEqual(len(requested_contracts), 1)
            self.assertIn("api.dexscreener.com/tokens/v1/tron/", requested_contracts[0])
            self.assertEqual(set(prices.keys()), set(TRACKED_TRC20_TOKENS.keys()))
            self.assertAlmostEqual(prices["50327c6c5a14dcade707abad2e27eb517df87ab5"], 0.031)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_fetch_retries_on_rate_limit(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = os.path.join(td, "tron_state.json")
                os.environ["TRON_STATE_PATH"] = state_path
                os.environ["TRON_LOOKBACK_BLOCKS_ON_BOOT"] = "1"
                os.environ["TRON_BLOCK_REQUEST_DELAY_SECONDS"] = "0"
                os.environ["TRON_BLOCK_FETCH_MAX_RETRIES"] = "2"
                os.environ["TRON_BLOCK_RETRY_BACKOFF_SECONDS"] = "0"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = TronSource()
                source._sleep = lambda seconds: None
                source._get_now_block_number = lambda client: 42
                source._get_trx_usd_price = lambda client: 1.0

                attempts = {"count": 0}

                def fake_get_block(client, block_num):
                    attempts["count"] += 1
                    if attempts["count"] < 3:
                        request = httpx.Request("POST", "https://api.trongrid.io/wallet/getblockbynum")
                        response = httpx.Response(429, request=request)
                        raise httpx.HTTPStatusError("rate limited", request=request, response=response)
                    return {"block_header": {"raw_data": {"timestamp": 1735689600000}}, "transactions": []}

                source._get_block = fake_get_block

                items = source.fetch()
                self.assertEqual(items, [])
                self.assertEqual(attempts["count"], 3)
        finally:
            os.environ.clear()
            os.environ.update(old_env)


    def test_fetch_emits_watchlist_transfer_below_global_threshold(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = os.path.join(td, "tron_state.json")
                watchlist_path = os.path.join(td, "watchlist.json")
                with open(watchlist_path, "w", encoding="utf-8") as f:
                    f.write('[{"address": "watch_to", "label": "Known Wallet", "category": "exchange"}]')

                os.environ["TRON_STATE_PATH"] = state_path
                os.environ["TRON_WATCHLIST_PATH"] = watchlist_path
                os.environ["TRON_LOOKBACK_BLOCKS_ON_BOOT"] = "1"
                os.environ["TRON_MIN_TRANSFER_USD"] = "200000000"
                os.environ["TRON_WATCHLIST_MIN_TRANSFER_USD"] = "1000000"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = TronSource()
                source._get_now_block_number = lambda client: 77
                source._get_trx_usd_price = lambda client: 1.0

                source._get_block = lambda client, block_num: {
                    "block_header": {"raw_data": {"timestamp": 1735689600000}},
                    "transactions": [
                        {
                            "txID": "watch123",
                            "raw_data": {
                                "contract": [
                                    {
                                        "type": "TransferContract",
                                        "parameter": {
                                            "value": {
                                                "owner_address": "random_from",
                                                "to_address": "watch_to",
                                                "amount": 2_000_000_000_000,
                                            }
                                        },
                                    }
                                ]
                            },
                        }
                    ],
                }

                items = source.fetch()
                self.assertEqual(len(items), 1)
                self.assertTrue(items[0].data["isWatchlist"])
                self.assertEqual(items[0].data["thresholdUsd"], 1000000)
                self.assertIn("watchlist", items[0].tags)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_fetch_emits_major_trc20_transfer(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = os.path.join(td, "tron_state.json")
                os.environ["TRON_STATE_PATH"] = state_path
                os.environ["TRON_LOOKBACK_BLOCKS_ON_BOOT"] = "1"
                os.environ["TRON_MIN_TRANSFER_USD"] = "200000000"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = TronSource()
                source._get_now_block_number = lambda client: 100
                source._get_trx_usd_price = lambda client: 0.25
                source._get_tracked_token_prices_usd = lambda client: {
                    "a614f803b6fd780986a42c78ec9c7f77e6ded13c": 1.0
                }
                source._get_block = lambda client, block_num: {
                    "block_header": {"raw_data": {"timestamp": 1735689600000}},
                    "transactions": [
                        {
                            "txID": "trc20tx",
                            "raw_data": {
                                "contract": [
                                    {
                                        "type": "TriggerSmartContract",
                                        "parameter": {
                                            "value": {
                                                "owner_address": "41" + "11" * 20,
                                                "contract_address": "a614f803b6fd780986a42c78ec9c7f77e6ded13c",
                                                "data": (
                                                    "a9059cbb"
                                                    + "0" * 24
                                                    + "22" * 20
                                                    + hex(250_000_000_000_000)[2:].rjust(64, "0")
                                                ),
                                            }
                                        },
                                    }
                                ]
                            },
                        }
                    ],
                }

                items = source.fetch()
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0].item_type, "onchain_token_transfer")
                self.assertEqual(items[0].data["tokenSymbol"], "USDT")
                self.assertGreaterEqual(items[0].data["amountUsd"], 200000000)
        finally:
            os.environ.clear()
            os.environ.update(old_env)



    def test_starts_at_chain_head_on_startup_even_with_stale_state(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = os.path.join(td, "tron_state.json")
                with open(state_path, "w", encoding="utf-8") as f:
                    f.write('{"last_scanned_block": 1}')
                os.environ["TRON_STATE_PATH"] = state_path
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = TronSource()
                source._get_now_block_number = lambda client: 10
                source._get_trx_usd_price = lambda client: 1.0
                visited = []

                def fake_get_block(client, block_num):
                    visited.append(block_num)
                    return {"block_header": {"raw_data": {"timestamp": 1735689600000}}, "transactions": []}

                source._get_block = fake_get_block
                source.fetch()
                self.assertEqual(visited, [10])
        finally:
            os.environ.clear()
            os.environ.update(old_env)

if __name__ == "__main__":
    unittest.main()
