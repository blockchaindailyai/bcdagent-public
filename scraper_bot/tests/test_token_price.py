import tempfile
import time
import unittest
from pathlib import Path

from scraper_bot.sources import token_price
from scraper_bot.sources.token_price import fetch_token_prices_from_dexscreener


class TokenPriceTest(unittest.TestCase):
    def setUp(self):
        token_price._RATE_LIMIT_BACKOFF_UNTIL.clear()

    def tearDown(self):
        token_price._RATE_LIMIT_BACKOFF_UNTIL.clear()

    def test_uses_persisted_cache_when_rate_limited(self):
        with tempfile.TemporaryDirectory() as td:
            cache_path = Path(td) / "token_price_cache.json"
            cache_path.write_text(
                '{"tron": {"ta": 1.23}}',
                encoding="utf-8",
            )

            class _Response:
                status_code = 429

                def raise_for_status(self):
                    raise AssertionError("raise_for_status should not be called for 429")

            class _Client:
                def get(self, *args, **kwargs):
                    return _Response()

            prices = fetch_token_prices_from_dexscreener(
                client=_Client(),
                chain="tron",
                contract_addresses=["TA"],
                fallback_prices={"ta": 1.0},
                cache_path=cache_path,
                rate_limit_backoff_seconds=180,
            )

            self.assertEqual(prices["ta"], 1.23)

    def test_backoff_skips_follow_up_network_calls(self):
        with tempfile.TemporaryDirectory() as td:
            cache_path = Path(td) / "token_price_cache.json"
            token_price._RATE_LIMIT_BACKOFF_UNTIL.clear()
            calls = {"count": 0}

            class _Response:
                status_code = 429

                def raise_for_status(self):
                    raise AssertionError("raise_for_status should not be called for 429")

            class _Client:
                def get(self, *args, **kwargs):
                    calls["count"] += 1
                    return _Response()

            client = _Client()
            fetch_token_prices_from_dexscreener(
                client=client,
                chain="ethereum",
                contract_addresses=["0xabc"],
                fallback_prices={"0xabc": 1.0},
                cache_path=cache_path,
                rate_limit_backoff_seconds=60,
            )
            before = time.time()
            fetch_token_prices_from_dexscreener(
                client=client,
                chain="ethereum",
                contract_addresses=["0xabc"],
                fallback_prices={"0xabc": 1.0},
                cache_path=cache_path,
                rate_limit_backoff_seconds=60,
            )

            self.assertEqual(calls["count"], 1)
            self.assertGreater(token_price._RATE_LIMIT_BACKOFF_UNTIL["ethereum"], before)


if __name__ == "__main__":
    unittest.main()
