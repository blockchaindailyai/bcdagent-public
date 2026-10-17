import httpx
import os
import unittest

from scraper_bot.sources.eth_source import EthSource
from scraper_bot.sources.token_price import fetch_token_prices_from_dexscreener, reset_token_price_backoff_for_tests
from scraper_bot.sources.price_backoff import reset_price_lookup_backoff_for_tests
from scraper_bot.sources.tron_source import TronSource


class PriceLookupGlobalBackoffTest(unittest.TestCase):
    def test_429_on_tron_blocks_immediate_eth_price_retry(self):
        old_env = dict(os.environ)
        try:
            os.environ["TRON_PRICE_CACHE_TTL_SECONDS"] = "0"
            os.environ["ETH_PRICE_CACHE_TTL_SECONDS"] = "0"
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            reset_price_lookup_backoff_for_tests()
            reset_token_price_backoff_for_tests()
            tron = TronSource()
            eth = EthSource()
            tron._cached_trx_usd = 0.25
            tron._cached_trx_usd_monotonic = 0.0
            eth._cached_eth_usd = 2100.0
            eth._cached_eth_usd_monotonic = 0.0

            class _429Client:
                def get(self, *args, **kwargs):
                    request = httpx.Request("GET", "https://api.coingecko.com/api/v3/simple/price")
                    response = httpx.Response(429, request=request)
                    raise httpx.HTTPStatusError("rate limited", request=request, response=response)

            eth_calls = {"count": 0}

            class _EthClient:
                def get(self, *args, **kwargs):
                    eth_calls["count"] += 1
                    class _Response:
                        def raise_for_status(self):
                            return None

                        def json(self):
                            return {"ethereum": {"usd": 3000}}

                    return _Response()

            self.assertEqual(tron._get_trx_usd_price(_429Client()), 0.25)
            self.assertEqual(eth._get_eth_usd_price(_EthClient()), 2100.0)
            self.assertEqual(eth_calls["count"], 0)
        finally:
            reset_price_lookup_backoff_for_tests()
            reset_token_price_backoff_for_tests()
            os.environ.clear()
            os.environ.update(old_env)


    def test_request_timeout_on_tron_blocks_immediate_eth_price_retry(self):
        old_env = dict(os.environ)
        try:
            os.environ["TRON_PRICE_CACHE_TTL_SECONDS"] = "0"
            os.environ["ETH_PRICE_CACHE_TTL_SECONDS"] = "0"
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            reset_price_lookup_backoff_for_tests()
            reset_token_price_backoff_for_tests()
            tron = TronSource()
            eth = EthSource()
            tron._cached_trx_usd = 0.25
            tron._cached_trx_usd_monotonic = 0.0
            eth._cached_eth_usd = 2100.0
            eth._cached_eth_usd_monotonic = 0.0

            class _TimeoutClient:
                def get(self, *args, **kwargs):
                    request = httpx.Request("GET", "https://api.coingecko.com/api/v3/simple/price")
                    raise httpx.ReadTimeout("timeout", request=request)

            eth_calls = {"count": 0}

            class _EthClient:
                def get(self, *args, **kwargs):
                    eth_calls["count"] += 1
                    class _Response:
                        def raise_for_status(self):
                            return None

                        def json(self):
                            return {"ethereum": {"usd": 3000}}

                    return _Response()

            self.assertEqual(tron._get_trx_usd_price(_TimeoutClient()), 0.25)
            self.assertEqual(eth._get_eth_usd_price(_EthClient()), 2100.0)
            self.assertEqual(eth_calls["count"], 0)
        finally:
            reset_price_lookup_backoff_for_tests()
            reset_token_price_backoff_for_tests()
            os.environ.clear()
            os.environ.update(old_env)

    def test_dexscreener_request_error_uses_cache_and_activates_global_backoff(self):
        old_env = dict(os.environ)
        try:
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            reset_price_lookup_backoff_for_tests()
            reset_token_price_backoff_for_tests()
            calls = {"count": 0}

            class _TimeoutClient:
                def get(self, *args, **kwargs):
                    calls["count"] += 1
                    request = httpx.Request("GET", "https://api.dexscreener.com/tokens/v1/tron/test")
                    raise httpx.ReadTimeout("timeout", request=request)

            fallback = {"tokena": 1.0}
            first = fetch_token_prices_from_dexscreener(
                client=_TimeoutClient(),
                chain="tron",
                contract_addresses=["tokena"],
                fallback_prices=fallback,
                cache_path=None,
                rate_limit_backoff_seconds=180,
            )

            class _NoCallClient:
                def get(self, *args, **kwargs):
                    raise AssertionError("price lookup should have been skipped due to global backoff")

            second = fetch_token_prices_from_dexscreener(
                client=_NoCallClient(),
                chain="ethereum",
                contract_addresses=["tokena"],
                fallback_prices=fallback,
                cache_path=None,
                rate_limit_backoff_seconds=180,
            )

            self.assertEqual(calls["count"], 1)
            self.assertEqual(first["tokena"], 1.0)
            self.assertEqual(second["tokena"], 1.0)
        finally:
            reset_price_lookup_backoff_for_tests()
            reset_token_price_backoff_for_tests()
            os.environ.clear()
            os.environ.update(old_env)
