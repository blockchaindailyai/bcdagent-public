import httpx
import os
import tempfile
import unittest

from scraper_bot.sources.price_backoff import reset_price_lookup_backoff_for_tests
from scraper_bot.sources.token_price import reset_token_price_backoff_for_tests
from scraper_bot.sources.eth_source import (
    TRACKED_ERC20_TOKENS,
    EthSource,
    _decode_erc20_transfer_log,
    _from_hex_int,
)


class EthSourceTest(unittest.TestCase):
    def setUp(self):
        reset_price_lookup_backoff_for_tests()
        reset_token_price_backoff_for_tests()

    def tearDown(self):
        reset_price_lookup_backoff_for_tests()
        reset_token_price_backoff_for_tests()

    def test_from_hex_int(self):
        self.assertEqual(_from_hex_int("0x10"), 16)
        self.assertIsNone(_from_hex_int(None))
        self.assertIsNone(_from_hex_int("abc"))


    def test_decode_erc20_transfer_log(self):
        log_entry = {
            "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
            "topics": [
                "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                "0x0000000000000000000000001111111111111111111111111111111111111111",
                "0x0000000000000000000000002222222222222222222222222222222222222222",
            ],
            "data": hex(250_000_000),
        }
        decoded = _decode_erc20_transfer_log(log_entry)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["symbol"], "USDT")
        self.assertEqual(decoded["amount"], 250.0)

    def test_fetch_emits_major_transfer(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = os.path.join(td, "eth_state.json")
                os.environ["ETH_STATE_PATH"] = state_path
                os.environ["ETH_LOOKBACK_BLOCKS_ON_BOOT"] = "1"
                os.environ["ETH_MIN_TRANSFER_USD"] = "200000000"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = EthSource()
                source._get_now_block_number = lambda client: 100
                source._get_eth_usd_price = lambda client: 2000.0

                source._get_block = lambda client, block_num: {
                    "timestamp": hex(1735689600),
                    "transactions": [
                        {
                            "hash": "0xabc",
                            "from": "0xfrom",
                            "to": "0xto",
                            "value": hex(120_000 * 10**18),
                        }
                    ],
                }

                items = source.fetch()
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0].source, "ethereum")
                self.assertGreaterEqual(items[0].data["amountUsd"], 200000000)
        finally:
            os.environ.clear()
            os.environ.update(old_env)


    def test_get_eth_usd_price_uses_cache_within_ttl(self):
        old_env = dict(os.environ)
        try:
            os.environ["ETH_PRICE_CACHE_TTL_SECONDS"] = "300"
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            source = EthSource()

            calls = {"count": 0}

            class _Response:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {"ethereum": {"usd": 2500}}

            class _Client:
                def get(self, *args, **kwargs):
                    calls["count"] += 1
                    return _Response()

            client = _Client()
            first = source._get_eth_usd_price(client)
            second = source._get_eth_usd_price(client)

            self.assertEqual(first, 2500)
            self.assertEqual(second, 2500)
            self.assertEqual(calls["count"], 1)
        finally:
            os.environ.clear()
            os.environ.update(old_env)



    def test_get_eth_usd_price_global_backoff_after_429_uses_cache(self):
        old_env = dict(os.environ)
        try:
            os.environ["ETH_PRICE_CACHE_TTL_SECONDS"] = "0"
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            reset_price_lookup_backoff_for_tests()
            source = EthSource()
            source._cached_eth_usd = 2222.0
            source._cached_eth_usd_monotonic = 0.0

            calls = {"count": 0}

            class _Client:
                def get(self, *args, **kwargs):
                    calls["count"] += 1
                    request = httpx.Request("GET", "https://api.coingecko.com/api/v3/simple/price")
                    response = httpx.Response(429, request=request)
                    raise httpx.HTTPStatusError("rate limited", request=request, response=response)

            client = _Client()
            first = source._get_eth_usd_price(client)
            second = source._get_eth_usd_price(client)

            self.assertEqual(first, 2222.0)
            self.assertEqual(second, 2222.0)
            self.assertEqual(calls["count"], 1)
        finally:
            reset_price_lookup_backoff_for_tests()
            os.environ.clear()
            os.environ.update(old_env)

    def test_get_tracked_token_prices_queries_dexscreener_once(self):
        old_env = dict(os.environ)
        try:
            os.environ["ETH_PRICE_CACHE_TTL_SECONDS"] = "300"
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            source = EthSource()
            requested_contracts = []

            class _Response:
                def raise_for_status(self):
                    return None

                def json(self):
                    return [
                        {
                            "priceUsd": "1.002",
                            "liquidity": {"usd": 5000000},
                            "baseToken": {"address": "0xdac17f958d2ee523a2206206994597c13d831ec7"},
                            "quoteToken": {"address": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"},
                        }
                    ]

            class _Client:
                def get(self, *args, **kwargs):
                    requested_contracts.append(args[0])
                    return _Response()

            prices = source._get_tracked_token_prices_usd(_Client())

            self.assertEqual(len(requested_contracts), 1)
            self.assertIn("api.dexscreener.com/tokens/v1/ethereum/", requested_contracts[0])
            self.assertEqual(set(prices.keys()), set(TRACKED_ERC20_TOKENS.keys()))
            self.assertAlmostEqual(prices["0xdac17f958d2ee523a2206206994597c13d831ec7"], 1.002)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_fetch_retries_on_rate_limit(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = os.path.join(td, "eth_state.json")
                os.environ["ETH_STATE_PATH"] = state_path
                os.environ["ETH_LOOKBACK_BLOCKS_ON_BOOT"] = "1"
                os.environ["ETH_BLOCK_REQUEST_DELAY_SECONDS"] = "0"
                os.environ["ETH_BLOCK_FETCH_MAX_RETRIES"] = "2"
                os.environ["ETH_BLOCK_RETRY_BACKOFF_SECONDS"] = "0"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = EthSource()
                source._sleep = lambda seconds: None
                source._get_now_block_number = lambda client: 42
                source._get_eth_usd_price = lambda client: 2000.0

                attempts = {"count": 0}

                def fake_get_block(client, block_num):
                    attempts["count"] += 1
                    if attempts["count"] < 3:
                        request = httpx.Request("POST", "https://ethereum-rpc.publicnode.com")
                        response = httpx.Response(429, request=request)
                        raise httpx.HTTPStatusError("rate limited", request=request, response=response)
                    return {"timestamp": hex(1735689600), "transactions": []}

                source._get_block = fake_get_block

                items = source.fetch()
                self.assertEqual(items, [])
                self.assertEqual(attempts["count"], 3)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_fetch_emits_major_token_transfer(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = os.path.join(td, "eth_state.json")
                os.environ["ETH_STATE_PATH"] = state_path
                os.environ["ETH_LOOKBACK_BLOCKS_ON_BOOT"] = "1"
                os.environ["ETH_MIN_TRANSFER_USD"] = "200000000"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = EthSource()
                source._get_now_block_number = lambda client: 100
                source._get_eth_usd_price = lambda client: 2000.0
                source._get_tracked_token_prices_usd = lambda client: {
                    "0xdac17f958d2ee523a2206206994597c13d831ec7": 1.0
                }

                source._get_block = lambda client, block_num: {
                    "timestamp": hex(1735689600),
                    "transactions": [{"hash": "0xtoken", "from": "0xabc", "to": "0xdef", "value": "0x0"}],
                }

                source._get_erc20_transfer_logs = lambda client, block_num: [
                    {
                        "transactionHash": "0xtoken",
                        "address": "0xdac17f958d2ee523a2206206994597c13d831ec7",
                        "topics": [
                            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                            "0x0000000000000000000000001111111111111111111111111111111111111111",
                            "0x0000000000000000000000002222222222222222222222222222222222222222",
                        ],
                        "data": hex(250_000_000_000_000),
                    }
                ]

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
                state_path = os.path.join(td, "eth_state.json")
                with open(state_path, "w", encoding="utf-8") as f:
                    f.write('{"last_scanned_block": 1}')
                os.environ["ETH_STATE_PATH"] = state_path
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = EthSource()
                source._get_now_block_number = lambda client: 10
                source._get_eth_usd_price = lambda client: 2000.0
                visited = []

                def fake_get_block(client, block_num):
                    visited.append(block_num)
                    return {"timestamp": hex(1735689600), "transactions": []}

                source._get_block = fake_get_block
                source._get_erc20_transfer_logs = lambda client, block_num: []

                source.fetch()
                self.assertEqual(visited, [10])
        finally:
            os.environ.clear()
            os.environ.update(old_env)

if __name__ == "__main__":
    unittest.main()
