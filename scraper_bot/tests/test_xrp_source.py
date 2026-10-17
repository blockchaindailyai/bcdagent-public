import os
import tempfile
import unittest
from unittest.mock import patch

import httpx

from scraper_bot.sources.price_backoff import reset_price_lookup_backoff_for_tests
from scraper_bot.sources.xrp_source import XrpSource, _extract_successful_payments, _ripple_time_to_utc


class XrpSourceTest(unittest.TestCase):
    def test_extract_successful_payments_uses_delivered_amount(self):
        ledger = {
            "transactions": [
                {
                    "tx": {
                        "TransactionType": "Payment",
                        "hash": "ABC",
                        "Account": "rFrom",
                        "Destination": "rTo",
                        "Amount": "9999999",
                    },
                    "meta": {
                        "TransactionResult": "tesSUCCESS",
                        "delivered_amount": "1000000",
                    },
                }
            ]
        }
        payments = _extract_successful_payments(ledger)
        self.assertEqual(payments[0]["amount"], "1000000")


    def test_extract_successful_payments_supports_expanded_tx_shape(self):
        ledger = {
            "transactions": [
                {
                    "TransactionType": "Payment",
                    "hash": "XYZ",
                    "Account": "rFrom",
                    "Destination": "rTo",
                    "Amount": "2000000",
                    "metaData": {
                        "TransactionResult": "tesSUCCESS",
                        "delivered_amount": "1500000",
                    },
                }
            ]
        }
        payments = _extract_successful_payments(ledger)
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments[0]["txid"], "XYZ")
        self.assertEqual(payments[0]["amount"], "1500000")

    def test_ripple_time_conversion(self):
        dt = _ripple_time_to_utc(0)
        self.assertEqual(dt.isoformat(), "2000-01-01T00:00:00+00:00")

    def test_fetch_emits_xrp_and_stablecoin_transfer(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["XRP_STATE_PATH"] = os.path.join(td, "xrp_state.json")
                os.environ["XRP_LOOKBACK_LEDGERS_ON_BOOT"] = "1"
                os.environ["XRP_MIN_TRANSFER_USD"] = "1000"
                os.environ["XRP_USD_FALLBACK"] = "0.5"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = XrpSource()
                source._get_now_ledger_index = lambda client: 100
                source._get_xrp_usd_price = lambda client: 0.5
                source._get_ledger_with_retries = lambda client, idx: {
                    "ledger_index": idx,
                    "close_time": 0,
                    "transactions": [
                        {
                            "tx": {
                                "TransactionType": "Payment",
                                "hash": "XRP_TX",
                                "Account": "rA",
                                "Destination": "rB",
                                "DestinationTag": 123,
                                "Amount": "3000000000",
                            },
                            "meta": {
                                "TransactionResult": "tesSUCCESS",
                                "delivered_amount": "3000000000",
                            },
                        },
                        {
                            "tx": {
                                "TransactionType": "Payment",
                                "hash": "USD_TX",
                                "Account": "rC",
                                "Destination": "rD",
                                "Amount": {"currency": "USD", "value": "2500", "issuer": "rIssuer"},
                            },
                            "meta": {
                                "TransactionResult": "tesSUCCESS",
                                "delivered_amount": {"currency": "USD", "value": "2500", "issuer": "rIssuer"},
                            },
                        },
                    ],
                }

                items = source.fetch()
                self.assertEqual(len(items), 2)
                self.assertEqual(items[0].source, "xrp")
                self.assertEqual(items[0].data["destinationTag"], 123)
                self.assertEqual(items[1].classification["tickers"], ["USD"])
        finally:
            os.environ.clear()
            os.environ.update(old_env)



    def test_fetch_clamps_scan_window_when_state_is_far_behind(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["XRP_STATE_PATH"] = os.path.join(td, "xrp_state.json")
                os.environ["XRP_LOOKBACK_LEDGERS_ON_BOOT"] = "1"
                os.environ["XRP_MAX_LEDGERS_PER_CYCLE"] = "3"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = XrpSource()
                source._is_first_fetch_cycle = False
                source.state_path.write_text('{"last_scanned_ledger": 5}', encoding="utf-8")
                source._get_now_ledger_index = lambda client: 10
                source._get_xrp_usd_price = lambda client: 0.5
                scanned_ledgers = []

                def fake_get_ledger(client, idx):
                    scanned_ledgers.append(idx)
                    return {"ledger_index": idx, "close_time": 0, "transactions": []}

                source._get_ledger_with_retries = fake_get_ledger
                source.state_path.write_text('{"last_scanned_ledger": 1}', encoding='utf-8')

                with patch("scraper_bot.sources.xrp_source.log.warning") as warning_mock:
                    items = source.fetch()

                self.assertEqual(items, [])
                self.assertEqual(scanned_ledgers, [8, 9, 10])
                self.assertTrue(any(call.args and call.args[0] == "xrp.scan_window_clamped start_ledger=%s clamped_start_ledger=%s now_ledger=%s max_ledgers_per_cycle=%s effective_max_ledgers_per_cycle=%s" for call in warning_mock.call_args_list))
        finally:
            os.environ.clear()
            os.environ.update(old_env)




    def test_effective_max_ledgers_per_cycle_respects_budget_in_serial_mode(self):
        old_env = dict(os.environ)
        try:
            os.environ["XRP_CYCLE_TIME_BUDGET_SECONDS"] = "45"
            os.environ["XRP_LEDGER_REQUEST_DELAY_SECONDS"] = "0.35"
            os.environ["XRP_MAX_LEDGERS_PER_CYCLE"] = "256"
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            source = XrpSource()
            source.parallel_ledger_fetch_workers = 1
            self.assertEqual(source._effective_max_ledgers_per_cycle(), 128)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_fetch_cold_start_begins_at_tip_not_lookback_window(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["XRP_STATE_PATH"] = os.path.join(td, "xrp_state.json")
                os.environ["XRP_LOOKBACK_LEDGERS_ON_BOOT"] = "500"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = XrpSource()
                source._get_now_ledger_index = lambda client: 100
                source._get_xrp_usd_price = lambda client: 1.0

                seen_ledgers = []

                def _ledger(client, idx):
                    seen_ledgers.append(idx)
                    return {"ledger_index": idx, "close_time": 0, "transactions": []}

                source._get_ledger_with_retries = _ledger

                items = source.fetch()
                self.assertEqual(items, [])
                self.assertEqual(seen_ledgers, [100])
        finally:
            os.environ.clear()
            os.environ.update(old_env)


    def test_fetch_ignores_stored_backlog_on_first_cycle_after_restart(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["XRP_STATE_PATH"] = os.path.join(td, "xrp_state.json")
                os.environ["XRP_MAX_LEDGERS_PER_CYCLE"] = "256"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = XrpSource()
                source.state_path.write_text('{"last_scanned_ledger": 5}', encoding='utf-8')
                source._get_now_ledger_index = lambda client: 10
                source._get_xrp_usd_price = lambda client: 1.0

                seen_ledgers = []

                def _ledger(client, idx):
                    seen_ledgers.append(idx)
                    return {"ledger_index": idx, "close_time": 0, "transactions": []}

                source._get_ledger_with_retries = _ledger

                items = source.fetch()
                self.assertEqual(items, [])
                self.assertEqual(seen_ledgers, [10])
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_fetch_respects_cycle_time_budget_and_persists_progress(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["XRP_STATE_PATH"] = os.path.join(td, "xrp_state.json")
                os.environ["XRP_LOOKBACK_LEDGERS_ON_BOOT"] = "5"
                os.environ["XRP_CYCLE_TIME_BUDGET_SECONDS"] = "1"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = XrpSource()
                source.state_path.write_text('{"last_scanned_ledger": 5}', encoding="utf-8")
                source._get_now_ledger_index = lambda client: 10
                source._get_xrp_usd_price = lambda client: 1.0
                source._get_ledger_with_retries = lambda client, idx: {"ledger_index": idx, "close_time": 0, "transactions": []}

                monotonic_values = iter([0.0, 0.0, 0.3, 1.2, 1.2])
                with patch("scraper_bot.sources.xrp_source.time.monotonic", side_effect=lambda: next(monotonic_values)):
                    items = source.fetch()

                self.assertEqual(items, [])
                state = source.state_path.read_text(encoding="utf-8")
                self.assertIn('"last_scanned_ledger": 10', state)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_get_xrp_usd_price_rate_limit_backoff_uses_cached_memory(self):
        old_env = dict(os.environ)
        try:
            os.environ["XRP_PRICE_CACHE_TTL_SECONDS"] = "0"
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            reset_price_lookup_backoff_for_tests()
            source = XrpSource()
            source._cached_xrp_usd = 0.61
            source._cached_xrp_usd_monotonic = 0.0

            calls = {"count": 0}

            class _Client:
                def get(self, *args, **kwargs):
                    calls["count"] += 1
                    request = httpx.Request("GET", "https://api.coingecko.com/api/v3/simple/price")
                    response = httpx.Response(429, request=request)
                    raise httpx.HTTPStatusError("rate limited", request=request, response=response)

            client = _Client()
            first = source._get_xrp_usd_price(client)
            second = source._get_xrp_usd_price(client)

            self.assertEqual(first, 0.61)
            self.assertEqual(second, 0.61)
            self.assertEqual(calls["count"], 1)
        finally:
            reset_price_lookup_backoff_for_tests()
            os.environ.clear()
            os.environ.update(old_env)

    def test_request_error_activates_global_backoff_for_other_chain_price_lookup(self):
        old_env = dict(os.environ)
        try:
            os.environ["XRP_PRICE_CACHE_TTL_SECONDS"] = "0"
            os.environ["ETH_PRICE_CACHE_TTL_SECONDS"] = "0"
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            from scraper_bot.sources.eth_source import EthSource

            reset_price_lookup_backoff_for_tests()
            xrp = XrpSource()
            eth = EthSource()
            xrp._cached_xrp_usd = 0.7
            xrp._cached_xrp_usd_monotonic = 0.0
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
                    raise AssertionError("eth lookup should have been skipped due to global backoff")

            self.assertEqual(xrp._get_xrp_usd_price(_TimeoutClient()), 0.7)
            self.assertEqual(eth._get_eth_usd_price(_EthClient()), 2100.0)
            self.assertEqual(eth_calls["count"], 0)
        finally:
            reset_price_lookup_backoff_for_tests()
            os.environ.clear()
            os.environ.update(old_env)



    def test_get_ledger_with_retries_handles_rpc_rate_limit_runtime_error(self):
        old_env = dict(os.environ)
        try:
            os.environ["XRP_LEDGER_FETCH_MAX_RETRIES"] = "1"
            os.environ["XRP_LEDGER_RETRY_BACKOFF_SECONDS"] = "0"
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            source = XrpSource()
            call_count = 0

            def flaky_get_ledger(client, ledger_index):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("xrp.rpc_error method=ledger result={'error': 'tooBusy'}")
                return {"ledger_index": ledger_index, "transactions": []}

            source._get_ledger = flaky_get_ledger

            ledger = source._get_ledger_with_retries(client=None, ledger_index=123)
            self.assertEqual(ledger["ledger_index"], 123)
            self.assertEqual(call_count, 2)
        finally:
            os.environ.clear()
            os.environ.update(old_env)


    def test_cycle_budget_warning_is_rate_limited_unless_backlog_grows(self):
        old_env = dict(os.environ)
        try:
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            source = XrpSource()
            source.cycle_time_budget_seconds = 45

            with patch("scraper_bot.sources.xrp_source.log.warning") as warning_mock:
                monotonic_values = iter([100.0, 101.0, 350.0, 360.0])
                with patch("scraper_bot.sources.xrp_source.time.monotonic", side_effect=lambda: next(monotonic_values)):
                    source._maybe_log_cycle_time_budget_reached(now_ledger=200, last_completed_ledger=150)
                    source._maybe_log_cycle_time_budget_reached(now_ledger=205, last_completed_ledger=155)
                    source._maybe_log_cycle_time_budget_reached(now_ledger=215, last_completed_ledger=155)
                    source._maybe_log_cycle_time_budget_reached(now_ledger=220, last_completed_ledger=160)

            self.assertEqual(warning_mock.call_count, 2)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

if __name__ == "__main__":
    unittest.main()
