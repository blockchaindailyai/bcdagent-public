import httpx
import os
import tempfile
import unittest

from scraper_bot.sources.solana_source import (
    SolanaSource,
    _extract_native_transfers,
    _extract_spl_token_transfers,
)


def _b58encode(data: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    if not data:
        return ""
    n = int.from_bytes(data, "big")
    out = []
    while n > 0:
        n, rem = divmod(n, 58)
        out.append(alphabet[rem])
    prefix = "1" * (len(data) - len(data.lstrip(b"\x00")))
    return prefix + ("".join(reversed(out)) if out else "")


class SolanaSourceTest(unittest.TestCase):

    def test_block_unavailable_error_detection(self):
        self.assertTrue(
            SolanaSource._is_block_unavailable_error(
                RuntimeError("rpc_error method=getBlock error={'code': -32004, 'message': 'Block not available'}")
            )
        )
        self.assertFalse(SolanaSource._is_block_unavailable_error(RuntimeError("rpc_error method=getBlock error=boom")))

    def test_uses_configured_rpc_url(self):
        old_env = dict(os.environ)
        try:
            os.environ["SOLANA_RPC_URL"] = "https://example.solana.local/rpc"
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            source = SolanaSource()
            self.assertEqual(source.rpc_url, "https://example.solana.local/rpc")
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_fetch_cold_start_begins_at_tip_not_lookback_window(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["SOLANA_STATE_PATH"] = os.path.join(td, "solana_state.json")
                os.environ["SOLANA_LOOKBACK_SLOTS_ON_BOOT"] = "500"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = SolanaSource()
                source._get_now_slot = lambda client: 100
                source._get_sol_usd_price = lambda client: 200.0

                visited = []

                def fake_get_block(client, slot):
                    visited.append(slot)
                    return {"blockTime": 1735689600, "transactions": []}

                source._get_block = fake_get_block

                items = source.fetch()
                self.assertEqual(items, [])
                self.assertEqual(visited, [100])
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_allows_slot_request_delay_below_half_second(self):
        old_env = dict(os.environ)
        try:
            os.environ["SOLANA_SLOT_REQUEST_DELAY_SECONDS"] = "0.1"
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            source = SolanaSource()
            self.assertEqual(source.slot_request_delay_seconds, 0.1)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_extract_native_transfers_only(self):
        tx = {
            "transaction": {
                "message": {
                    "accountKeys": [
                        "from_addr",
                        "to_addr",
                        "11111111111111111111111111111111",
                        "SomeOtherProgram111111111111111111111111",
                    ],
                    "instructions": [
                        {
                            "programIdIndex": 2,
                            "parsed": {
                                "type": "transfer",
                                "info": {
                                    "source": "from_addr",
                                    "destination": "to_addr",
                                    "lamports": 123,
                                },
                            },
                        },
                        {"programIdIndex": 3, "parsed": {"type": "transfer", "info": {"lamports": 50}}},
                    ],
                }
            }
        }

        transfers = _extract_native_transfers(tx)
        self.assertEqual(transfers, [{"from": "from_addr", "to": "to_addr", "amount_lamports": 123}])



    def test_extract_native_transfers_supports_object_account_keys(self):
        tx = {
            "transaction": {
                "message": {
                    "accountKeys": [
                        {"pubkey": "from_addr", "signer": True, "writable": True},
                        {"pubkey": "to_addr", "signer": False, "writable": True},
                        {"pubkey": "11111111111111111111111111111111", "signer": False, "writable": False},
                    ],
                    "instructions": [
                        {
                            "programIdIndex": 2,
                            "parsed": {
                                "type": "transfer",
                                "info": {
                                    "source": "from_addr",
                                    "destination": "to_addr",
                                    "lamports": 321,
                                },
                            },
                        }
                    ],
                }
            }
        }

        transfers = _extract_native_transfers(tx)
        self.assertEqual(transfers, [{"from": "from_addr", "to": "to_addr", "amount_lamports": 321}])

    def test_extract_native_transfers_supports_program_id_field(self):
        tx = {
            "transaction": {
                "message": {
                    "accountKeys": ["from_addr", "to_addr"],
                    "instructions": [
                        {
                            "programId": "11111111111111111111111111111111",
                            "parsed": {
                                "type": "transfer",
                                "info": {
                                    "source": "from_addr",
                                    "destination": "to_addr",
                                    "lamports": 999,
                                },
                            },
                        }
                    ],
                }
            }
        }

        transfers = _extract_native_transfers(tx)
        self.assertEqual(transfers, [{"from": "from_addr", "to": "to_addr", "amount_lamports": 999}])

    def test_extract_native_transfers_from_raw_instruction_data(self):
        tx = {
            "transaction": {
                "message": {
                    "accountKeys": [
                        "from_addr",
                        "to_addr",
                        "11111111111111111111111111111111",
                    ],
                    "instructions": [
                        {
                            "programIdIndex": 2,
                            "accounts": [0, 1],
                            "data": _b58encode((2).to_bytes(4, "little") + (500).to_bytes(8, "little")),
                        }
                    ],
                }
            }
        }

        transfers = _extract_native_transfers(tx)
        self.assertEqual(transfers, [{"from": "from_addr", "to": "to_addr", "amount_lamports": 500}])

    def test_extract_spl_token_transfers_supports_inner_instructions(self):
        tx = {
            "transaction": {
                "message": {
                    "accountKeys": [
                        "source_token_account",
                        "dest_token_account",
                        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    ],
                    "instructions": [],
                }
            },
            "meta": {
                "preTokenBalances": [
                    {
                        "accountIndex": 0,
                        "mint": "Mint111111111111111111111111111111111111111",
                        "uiTokenAmount": {"decimals": 6},
                    },
                    {
                        "accountIndex": 1,
                        "mint": "Mint111111111111111111111111111111111111111",
                        "uiTokenAmount": {"decimals": 6},
                    },
                ],
                "innerInstructions": [
                    {
                        "index": 0,
                        "instructions": [
                            {
                                "programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                                "parsed": {
                                    "type": "transfer",
                                    "info": {
                                        "source": "source_token_account",
                                        "destination": "dest_token_account",
                                        "amount": "1000000",
                                    },
                                },
                            }
                        ],
                    }
                ],
            },
        }

        transfers = _extract_spl_token_transfers(tx)
        self.assertEqual(
            transfers,
            [
                {
                    "from": "source_token_account",
                    "to": "dest_token_account",
                    "mint": "Mint111111111111111111111111111111111111111",
                    "amount_raw": 1000000,
                    "decimals": 6,
                }
            ],
        )


    def test_extract_spl_token_transfers_from_raw_instruction_data(self):
        tx = {
            "transaction": {
                "message": {
                    "accountKeys": [
                        "source_token_account",
                        "dest_token_account",
                        "owner",
                        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    ],
                    "instructions": [
                        {
                            "programIdIndex": 3,
                            "accounts": [0, 1, 2],
                            "data": _b58encode(bytes([3]) + (1000000).to_bytes(8, "little")),
                        }
                    ],
                }
            },
            "meta": {
                "preTokenBalances": [
                    {
                        "accountIndex": 0,
                        "mint": "Mint111111111111111111111111111111111111111",
                        "uiTokenAmount": {"decimals": 6},
                    },
                    {
                        "accountIndex": 1,
                        "mint": "Mint111111111111111111111111111111111111111",
                        "uiTokenAmount": {"decimals": 6},
                    },
                ]
            },
        }

        transfers = _extract_spl_token_transfers(tx)
        self.assertEqual(
            transfers,
            [
                {
                    "from": "source_token_account",
                    "to": "dest_token_account",
                    "mint": "Mint111111111111111111111111111111111111111",
                    "amount_raw": 1000000,
                    "decimals": 6,
                }
            ],
        )

    def test_fetch_emits_major_transfer(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = os.path.join(td, "solana_state.json")
                os.environ["SOLANA_STATE_PATH"] = state_path
                os.environ["SOLANA_LOOKBACK_SLOTS_ON_BOOT"] = "1"
                os.environ["SOLANA_MIN_TRANSFER_USD"] = "200000000"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = SolanaSource()
                source._get_now_slot = lambda client: 100
                source._get_sol_usd_price = lambda client: 200.0
                source._get_block = lambda client, slot: {
                    "blockTime": 1735689600,
                    "transactions": [
                        {
                            "transaction": {
                                "signatures": ["sig123"],
                                "message": {
                                    "accountKeys": [
                                        "from_addr",
                                        "to_addr",
                                        "11111111111111111111111111111111",
                                    ],
                                    "instructions": [
                                        {
                                            "programIdIndex": 2,
                                            "parsed": {
                                                "type": "transfer",
                                                "info": {
                                                    "source": "from_addr",
                                                    "destination": "to_addr",
                                                    "lamports": 1_500_000_000_000_000,
                                                },
                                            },
                                        }
                                    ],
                                },
                            }
                        }
                    ],
                }

                items = source.fetch()
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0].source, "solana")
                self.assertEqual(items[0].data["slot"], 100)
                self.assertGreaterEqual(items[0].data["amountUsd"], 200000000)
        finally:
            os.environ.clear()
            os.environ.update(old_env)


    def test_bootstrap_starts_from_tip_even_with_state_and_lookback(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = os.path.join(td, "solana_state.json")
                with open(state_path, "w", encoding="utf-8") as f:
                    f.write('{"last_scanned_slot": 1}')
                os.environ["SOLANA_STATE_PATH"] = state_path
                os.environ["SOLANA_LOOKBACK_SLOTS_ON_BOOT"] = "3"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = SolanaSource()
                source._get_now_slot = lambda client: 10
                source._get_sol_usd_price = lambda client: 200.0
                visited = []

                def fake_get_block(client, slot):
                    visited.append(slot)
                    return {"blockTime": 1735689600, "transactions": []}

                source._get_block = fake_get_block
                source.fetch()
                self.assertEqual(visited, [10])
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_get_block_uses_confirmed_commitment(self):
        old_env = dict(os.environ)
        try:
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")

            source = SolanaSource()
            captured: dict[str, object] = {}

            def fake_rpc(client, method, params):
                captured["method"] = method
                captured["params"] = params
                return {}

            source._rpc = fake_rpc  # type: ignore[method-assign]
            source._get_block(client=None, slot=123)

            self.assertEqual(captured["method"], "getBlock")
            params = captured["params"]
            self.assertEqual(params[0], 123)
            self.assertEqual(params[1]["commitment"], "confirmed")
            self.assertEqual(params[1]["encoding"], "json")
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_fetch_retries_on_rate_limit(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = os.path.join(td, "solana_state.json")
                os.environ["SOLANA_STATE_PATH"] = state_path
                os.environ["SOLANA_LOOKBACK_SLOTS_ON_BOOT"] = "1"
                os.environ["SOLANA_SLOT_REQUEST_DELAY_SECONDS"] = "0"
                os.environ["SOLANA_SLOT_FETCH_MAX_RETRIES"] = "2"
                os.environ["SOLANA_SLOT_RETRY_BACKOFF_SECONDS"] = "0"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = SolanaSource()
                source._sleep = lambda seconds: None
                source._get_now_slot = lambda client: 42
                source._get_sol_usd_price = lambda client: 200.0

                attempts = {"count": 0}

                def fake_get_block(client, slot):
                    attempts["count"] += 1
                    if attempts["count"] < 3:
                        request = httpx.Request("POST", "https://beta.helius-rpc.com/?api-key=9f0e2e8b-d910-4ef5-b972-7d46c7551481")
                        response = httpx.Response(429, request=request)
                        raise httpx.HTTPStatusError("rate limited", request=request, response=response)
                    return {"blockTime": 1735689600, "transactions": []}

                source._get_block = fake_get_block

                items = source.fetch()
                self.assertEqual(items, [])
                self.assertEqual(attempts["count"], 3)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_fetch_honors_retry_after_header(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = os.path.join(td, "solana_state.json")
                os.environ["SOLANA_STATE_PATH"] = state_path
                os.environ["SOLANA_LOOKBACK_SLOTS_ON_BOOT"] = "1"
                os.environ["SOLANA_SLOT_REQUEST_DELAY_SECONDS"] = "0"
                os.environ["SOLANA_SLOT_FETCH_MAX_RETRIES"] = "1"
                os.environ["SOLANA_SLOT_RETRY_BACKOFF_SECONDS"] = "0.1"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = SolanaSource()
                sleeps: list[float] = []
                source._sleep = lambda seconds: sleeps.append(seconds)
                source._get_now_slot = lambda client: 42
                source._get_sol_usd_price = lambda client: 200.0

                attempts = {"count": 0}

                def fake_get_block(client, slot):
                    attempts["count"] += 1
                    if attempts["count"] == 1:
                        request = httpx.Request("POST", "https://beta.helius-rpc.com/?api-key=9f0e2e8b-d910-4ef5-b972-7d46c7551481")
                        response = httpx.Response(429, request=request, headers={"retry-after": "2"})
                        raise httpx.HTTPStatusError("rate limited", request=request, response=response)
                    return {"blockTime": 1735689600, "transactions": []}

                source._get_block = fake_get_block

                items = source.fetch()
                self.assertEqual(items, [])
                self.assertEqual(attempts["count"], 2)
                self.assertEqual(sleeps, [2.0])
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_clamps_scan_window_to_max_slots_per_cycle(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = os.path.join(td, "solana_state.json")
                with open(state_path, "w", encoding="utf-8") as f:
                    f.write('{"last_scanned_slot": 1}')
                os.environ["SOLANA_STATE_PATH"] = state_path
                os.environ["SOLANA_MAX_SLOTS_PER_CYCLE"] = "2"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = SolanaSource()
                source._bootstrapped = True
                source._get_now_slot = lambda client: 10
                source._get_sol_usd_price = lambda client: 200.0
                visited = []

                def fake_get_block(client, slot):
                    visited.append(slot)
                    return {"blockTime": 1735689600, "transactions": []}

                source._get_block = fake_get_block
                source.fetch()
                self.assertEqual(visited, [9, 10])
        finally:
            os.environ.clear()
            os.environ.update(old_env)


    def test_parallel_fetch_workers_processes_all_slots(self):
        old_env = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as td:
                state_path = os.path.join(td, "solana_state.json")
                with open(state_path, "w", encoding="utf-8") as f:
                    f.write('{"last_scanned_slot": 1}')
                os.environ["SOLANA_STATE_PATH"] = state_path
                os.environ["SOLANA_PARALLEL_BLOCK_FETCH_WORKERS"] = "4"
                os.environ.setdefault("INTEL_API_BASE", "http://localhost")
                os.environ.setdefault("INGEST_TOKEN", "x")

                source = SolanaSource()
                source._bootstrapped = True
                source._get_now_slot = lambda client: 5
                source._get_sol_usd_price = lambda client: 200.0
                visited = []

                def fake_get_block(client, slot):
                    visited.append(slot)
                    return {"blockTime": 1735689600, "transactions": []}

                source._get_block = fake_get_block
                source.fetch()
                self.assertEqual(sorted(visited), [2, 3, 4, 5])
        finally:
            os.environ.clear()
            os.environ.update(old_env)

if __name__ == "__main__":
    unittest.main()
