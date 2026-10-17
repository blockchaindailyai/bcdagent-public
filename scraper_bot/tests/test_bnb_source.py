import os
import tempfile
import unittest
from unittest.mock import patch

from scraper_bot.sources.bnb_source import BnbSource, _decode_bep20_transfer_log, _from_hex_int


class BnbSourceTest(unittest.TestCase):
    def test_from_hex_int(self):
        self.assertEqual(_from_hex_int("0x10"), 16)
        self.assertIsNone(_from_hex_int(None))
        self.assertIsNone(_from_hex_int("abc"))

    def test_decode_bep20_transfer_log(self):
        log_entry = {
            "address": "0x55d398326f99059fF775485246999027B3197955",
            "topics": [
                "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                "0x0000000000000000000000001111111111111111111111111111111111111111",
                "0x0000000000000000000000002222222222222222222222222222222222222222",
            ],
            "data": hex(250 * 10**18),
        }
        decoded = _decode_bep20_transfer_log(log_entry)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["symbol"], "USDT")
        self.assertEqual(decoded["amount"], 250.0)

    def test_fetch_emits_major_transfer(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = os.path.join(td, "bnb_state.json")
                os.environ["BNB_STATE_PATH"] = state_path
                os.environ["BNB_MIN_TRANSFER_USD"] = "200000000"
                os.environ["BNB_CONFIRMATION_DEPTH"] = "0"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = BnbSource()
                source._get_now_block_number = lambda client: 100
                source._get_bnb_usd_price = lambda client: 500.0
                source._get_tracked_token_prices_usd = lambda client: {}
                source._get_bep20_transfer_logs = lambda client, block_num: []
                source._get_block = lambda client, block_num: {
                    "timestamp": hex(1735689600),
                    "transactions": [
                        {
                            "hash": "0xabc",
                            "from": "0xfrom",
                            "to": "0xto",
                            "value": hex(500_000 * 10**18),
                        }
                    ],
                }

                items = source.fetch()
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0].source, "bnb")
                self.assertGreaterEqual(items[0].data["amountUsd"], 200000000)
        finally:
            os.environ.clear()
            os.environ.update(old_env)


    def test_fetch_clamps_scan_window_when_state_is_far_behind(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = os.path.join(td, "bnb_state.json")
                os.environ["BNB_STATE_PATH"] = state_path
                os.environ["BNB_CONFIRMATION_DEPTH"] = "0"
                os.environ["BNB_MAX_BLOCKS_PER_CYCLE"] = "3"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = BnbSource()
                source._bootstrapped = True
                source._get_now_block_number = lambda client: 10
                source._get_bnb_usd_price = lambda client: 500.0
                source._get_tracked_token_prices_usd = lambda client: {}
                source._get_bep20_transfer_logs = lambda client, block_num: []
                scanned_blocks = []

                def fake_get_block(client, block_num):
                    scanned_blocks.append(block_num)
                    return {"timestamp": hex(1735689600), "transactions": []}

                source._get_block = fake_get_block
                source.state_path.write_text('{"last_scanned_block": 1}', encoding='utf-8')

                with patch("scraper_bot.sources.bnb_source.log.warning") as warning_mock:
                    items = source.fetch()

                self.assertEqual(items, [])
                self.assertEqual(scanned_blocks, [8, 9, 10])
                self.assertTrue(any(call.args and call.args[0] == "bnb.scan_window_clamped start_block=%s clamped_start_block=%s now_block=%s max_blocks_per_cycle=%s" for call in warning_mock.call_args_list))
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_is_get_logs_limit_error(self):
        err = RuntimeError("rpc_error method=eth_getLogs error={'code': -32005, 'message': 'limit exceeded'}")
        self.assertTrue(BnbSource._is_get_logs_limit_error(err))

    def test_fetch_backs_off_after_get_logs_limit_error(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = os.path.join(td, "bnb_state.json")
                os.environ["BNB_STATE_PATH"] = state_path
                os.environ["BNB_CONFIRMATION_DEPTH"] = "0"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = BnbSource()
                source._get_now_block_number = lambda client: 2
                source._get_bnb_usd_price = lambda client: 500.0
                source._get_tracked_token_prices_usd = lambda client: {}
                source._get_block = lambda client, block_num: {
                    "timestamp": hex(1735689600),
                    "transactions": [],
                }

                call_count = 0

                def raise_limit_error(client, block_num):
                    nonlocal call_count
                    call_count += 1
                    raise RuntimeError(
                        "rpc_error method=eth_getLogs error={'code': -32005, 'message': 'limit exceeded'}"
                    )

                source._get_bep20_transfer_logs = raise_limit_error

                with patch("scraper_bot.sources.bnb_source.log.warning") as warning_mock:
                    items = source.fetch()

                self.assertEqual(items, [])
                self.assertEqual(call_count, 1)
                self.assertEqual(warning_mock.call_count, 1)
                warning_message = warning_mock.call_args.args[0]
                self.assertEqual(warning_message, "bnb.transfer_logs_rate_limited block=%s backoff_seconds=%.2f err=%s")
        finally:
            os.environ.clear()
            os.environ.update(old_env)



    def test_get_block_with_retries_handles_rpc_rate_limit_runtime_error(self):
        old_env = dict(os.environ)
        try:
            os.environ["BNB_BLOCK_FETCH_MAX_RETRIES"] = "1"
            os.environ["BNB_BLOCK_RETRY_BACKOFF_SECONDS"] = "0"
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            source = BnbSource()
            call_count = 0

            def flaky_get_block(client, block_num):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("rpc_error method=eth_getBlockByNumber error={'code': -32005, 'message': 'limit exceeded'}")
                return {"timestamp": hex(1735689600), "transactions": []}

            source._get_block = flaky_get_block

            block = source._get_block_with_retries(client=None, block_num=10)
            self.assertEqual(block["transactions"], [])
            self.assertEqual(call_count, 2)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

if __name__ == "__main__":
    unittest.main()
