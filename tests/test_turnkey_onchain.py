import io
import unittest
from dataclasses import replace
import urllib.error
from unittest.mock import Mock, patch

from openclaw_agent.turnkey.onchain import (
    TronBroadcastError,
    TronTxBroadcaster,
    _encode_types,
    _resolve_asset_address,
    _resolve_borrow_asset_address,
    _to_hex_address,
)
from openclaw_agent.turnkey.config import load_turnkey_config


class TurnkeyOnchainEncodingTests(unittest.TestCase):
    def test_broadcaster_uses_mcp_transport_when_enabled(self):
        cfg = replace(load_turnkey_config(), use_mcp_server_tron=True, mcp_server_tron_url="https://mcp.tron.local")
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)
        self.assertEqual(broadcaster.rpc_url, "https://mcp.tron.local")
        self.assertEqual(broadcaster.signer_rpc_url, cfg.signer_rpc_url)
        self.assertEqual(broadcaster.transport, "mcp-server-tron")

    def test_encode_types_with_dynamic_bytes(self):
        payload = _encode_types(
            ["bytes32", "uint256", "address", "bytes"],
            [b"x" * 32, 42, "TQHyk9zmKKdqs9Q5NFj8ervEWo1paDre8L", b"\x12\x49\xc5\x8b"],
        )
        self.assertTrue(isinstance(payload, str))
        self.assertGreater(len(payload), 64 * 4)

    def test_resolve_asset_address_requires_contract_address(self):
        cfg = load_turnkey_config()
        with self.assertRaises(Exception):
            _resolve_asset_address(cfg, "TRX")

    def test_resolve_borrow_asset_address_maps_trx_to_target(self):
        cfg = load_turnkey_config()
        target = cfg.addresses.jtrx_market
        self.assertEqual(_resolve_borrow_asset_address(cfg, "TRX", target), target)

    def test_to_hex_address_converts_base58(self):
        self.assertEqual(
            _to_hex_address("TQHyk9zmKKdqs9Q5NFj8ervEWo1paDre8L"),
            "419d1e991025167104be2530051b18c01be47a1200",
        )

    def test_trigger_retries_with_hex_fallback_on_npe(self):
        cfg = load_turnkey_config()
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)

        first = {"result": {"code": "OTHER_ERROR", "message": "class java.lang.NullPointerException : null"}}
        second = {"transaction": {"txID": "abc"}}
        broadcaster._post = Mock(side_effect=[first, second])

        payload = {
            "owner_address": "TQHyk9zmKKdqs9Q5NFj8ervEWo1paDre8L",
            "contract_address": "TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY",
            "function_selector": "f()",
            "parameter": "",
            "fee_limit": 1,
            "call_value": 0,
            "visible": True,
        }
        result = broadcaster._trigger_smart_contract(payload)

        self.assertEqual(result, second)
        self.assertEqual(broadcaster._post.call_count, 2)
        _, fallback_call = broadcaster._post.call_args_list
        self.assertFalse(fallback_call.args[1]["visible"])
        self.assertEqual(fallback_call.args[1]["owner_address"], _to_hex_address(payload["owner_address"]))
        self.assertEqual(fallback_call.args[1]["contract_address"], _to_hex_address(payload["contract_address"]))

    def test_broadcast_fails_fast_for_invalid_strategy_module_address(self):
        cfg = load_turnkey_config()
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)

        plan = Mock()
        plan.execution_plan.intent_id = "i"
        plan.execution_plan.plan_id = "p"
        step = Mock()
        step.params = {
            "strategy_method": "executeCallWithTRX",
            "amount_sun": "1",
            "target": "TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
            "call_data": "0x1249c58b",
            "strategy_module": "TTYnQdGnwYsnucia2RF9pTPX67BuSJo7vkF",
        }
        plan.execution_plan.ordered_steps = [step]

        with self.assertRaises(TronBroadcastError) as exc:
            broadcaster.broadcast_jtrx_plan(plan, owner_address="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY")

        self.assertIn("invalid_tron_base58check_address:strategy_module", str(exc.exception))

    @patch("openclaw_agent.turnkey.onchain.urllib.request.urlopen")
    def test_post_gives_actionable_message_for_unsupported_sign_endpoint(self, mock_urlopen):
        cfg = load_turnkey_config()
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)

        req = urllib.error.HTTPError(
            url="https://nile.trongrid.io/wallet/gettransactionsign",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=io.BytesIO(b'{"Error":"not found"}'),
        )
        mock_urlopen.side_effect = req

        with self.assertRaises(TronBroadcastError) as exc:
            broadcaster._post("/wallet/gettransactionsign", {"tx": "x"}, base_url="https://nile.trongrid.io")

        self.assertIn("sign_endpoint_unsupported:/wallet/gettransactionsign", str(exc.exception))

    @patch.object(TronTxBroadcaster, "_trigger_smart_contract")
    @patch.object(TronTxBroadcaster, "_post")
    @patch.object(TronTxBroadcaster, "_sign_transaction_locally")
    def test_broadcast_always_uses_local_signing_and_only_posts_broadcast(self, mock_local_sign, mock_post, mock_trigger):
        cfg = load_turnkey_config()
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)
        broadcaster.signer_rpc_url = "https://any.signer.node"

        plan = Mock()
        plan.execution_plan.intent_id = "i"
        plan.execution_plan.plan_id = "p"
        step = Mock()
        step.params = {
            "strategy_method": "executeCallWithTRX",
            "amount_sun": "1",
            "target": "TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
            "call_data": "0x1249c58b",
            "strategy_module": "TRuDK4GVYxkzmCnM1ZJJ13btMF7aqMZAtY",
        }
        plan.execution_plan.ordered_steps = [step]

        tx = {"txID": "abc", "raw_data_hex": "00", "raw_data": {"contract": [{"Permission_id": 2}]}}
        mock_trigger.return_value = {"transaction": tx}
        mock_local_sign.return_value = {"txID": "abc", "signature": ["deadbeef"]}
        mock_post.return_value = {"result": True}

        out = broadcaster.broadcast_jtrx_plan(plan, owner_address="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY")

        self.assertEqual(out.txid, "abc")
        mock_local_sign.assert_called_once_with(tx)
        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(mock_post.call_args_list[0].args[0], "/wallet/broadcasttransaction")
        trigger_payload = mock_trigger.call_args.args[0]
        self.assertNotIn("permission_id", trigger_payload)

    @patch.object(TronTxBroadcaster, "_trigger_smart_contract")
    @patch.object(TronTxBroadcaster, "_post")
    def test_broadcast_requires_local_signing_enabled(self, mock_post, mock_trigger):
        cfg = replace(load_turnkey_config(), enable_local_signing_fallback=False)
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)

        plan = Mock()
        plan.execution_plan.intent_id = "i"
        plan.execution_plan.plan_id = "p"
        step = Mock()
        step.params = {
            "strategy_method": "executeCallWithTRX",
            "amount_sun": "1",
            "target": "TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
            "call_data": "0x1249c58b",
            "strategy_module": "TRuDK4GVYxkzmCnM1ZJJ13btMF7aqMZAtY",
        }
        plan.execution_plan.ordered_steps = [step]

        mock_trigger.return_value = {"transaction": {"txID": "abc", "raw_data_hex": "00"}}

        with self.assertRaises(TronBroadcastError) as exc:
            broadcaster.broadcast_jtrx_plan(plan, owner_address="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY")

        self.assertIn("local_signing_required", str(exc.exception))
        mock_post.assert_not_called()


    @patch.object(TronTxBroadcaster, "_trigger_smart_contract")
    @patch.object(TronTxBroadcaster, "_post")
    @patch.object(TronTxBroadcaster, "_sign_transaction_locally")
    def test_broadcast_uses_explicit_call_value_when_present(self, mock_local_sign, mock_post, mock_trigger):
        cfg = load_turnkey_config()
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)

        plan = Mock()
        plan.execution_plan.intent_id = "i"
        plan.execution_plan.plan_id = "p"
        step = Mock()
        step.params = {
            "strategy_method": "executeCallWithTRX",
            "amount_sun": "1000000",
            "strategy_amount_sun": "0",
            "call_value_sun": "0",
            "target": "TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
            "call_data": "0x852a12e300000000000000000000000000000000000000000000000000000000000f4240",
            "strategy_module": "TRuDK4GVYxkzmCnM1ZJJ13btMF7aqMZAtY",
        }
        plan.execution_plan.ordered_steps = [step]

        tx = {"txID": "abc", "raw_data_hex": "00", "raw_data": {"contract": [{"Permission_id": 2}]}}
        mock_trigger.return_value = {"transaction": tx}
        mock_local_sign.return_value = {"txID": "abc", "signature": ["deadbeef"]}
        mock_post.return_value = {"result": True}

        out = broadcaster.broadcast_jtrx_plan(plan, owner_address="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY")

        self.assertEqual(out.txid, "abc")
        payload = mock_trigger.call_args.args[0]
        self.assertEqual(payload["call_value"], 0)
        encoded_strategy_amount = int(payload["parameter"][128:192], 16)
        self.assertEqual(encoded_strategy_amount, 0)

    @patch.object(TronTxBroadcaster, "_trigger_smart_contract")
    @patch.object(TronTxBroadcaster, "_post")
    @patch.object(TronTxBroadcaster, "_sign_transaction_locally")
    def test_broadcast_includes_permission_id_when_positive(self, mock_local_sign, mock_post, mock_trigger):
        cfg = replace(load_turnkey_config(), permission_id=2)
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)

        plan = Mock()
        plan.execution_plan.intent_id = "i"
        plan.execution_plan.plan_id = "p"
        step = Mock()
        step.params = {
            "strategy_method": "executeCallWithTRX",
            "amount_sun": "1",
            "target": "TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
            "call_data": "0x1249c58b",
            "strategy_module": "TRuDK4GVYxkzmCnM1ZJJ13btMF7aqMZAtY",
        }
        plan.execution_plan.ordered_steps = [step]

        tx = {"txID": "abc", "raw_data_hex": "00", "raw_data": {"contract": [{"Permission_id": 2}]}}
        mock_trigger.return_value = {"transaction": tx}
        mock_local_sign.return_value = {"txID": "abc", "signature": ["deadbeef"]}
        mock_post.return_value = {"result": True}

        broadcaster.broadcast_jtrx_plan(plan, owner_address="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY")

        trigger_payload = mock_trigger.call_args.args[0]
        self.assertEqual(trigger_payload["permission_id"], 2)

    def test_permissioned_write_prefers_mcp_primary_path(self):
        cfg = replace(
            load_turnkey_config(),
            use_mcp_server_tron=True,
            mcp_server_tron_url="https://mcp.tron.local",
            signer_rpc_url="https://nile.trongrid.io",
            permission_id=3,
        )
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)
        self.assertEqual(broadcaster.rpc_url, "https://mcp.tron.local")
        self.assertEqual(broadcaster.signer_rpc_url, "https://nile.trongrid.io")

    @patch.object(TronTxBroadcaster, "_trigger_smart_contract")
    def test_trigger_retries_to_signer_when_permission_id_missing(self, mock_trigger):
        cfg = replace(
            load_turnkey_config(),
            use_mcp_server_tron=True,
            mcp_server_tron_url="https://mcp.tron.local",
            signer_rpc_url="https://nile.trongrid.io",
            permission_id=3,
        )
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)
        payload = {"permission_id": 3}
        mock_trigger.side_effect = [
            {"transaction": {"raw_data": {"contract": [{}]}}},
            {"transaction": {"raw_data": {"contract": [{"Permission_id": 3}]}}},
        ]

        _, used_base_url = broadcaster._trigger_with_permission_retry(payload)

        self.assertEqual(used_base_url, "https://nile.trongrid.io")
        self.assertEqual(mock_trigger.call_count, 2)
        self.assertEqual(mock_trigger.call_args_list[0].kwargs["base_url"], "https://mcp.tron.local")
        self.assertEqual(mock_trigger.call_args_list[1].kwargs["base_url"], "https://nile.trongrid.io")

    @patch.object(TronTxBroadcaster, "_trigger_with_permission_retry")
    @patch.object(TronTxBroadcaster, "_post")
    @patch.object(TronTxBroadcaster, "_sign_transaction_locally")
    def test_permissioned_broadcast_uses_trigger_base_url_first(self, mock_local_sign, mock_post, mock_trigger):
        cfg = replace(
            load_turnkey_config(),
            use_mcp_server_tron=True,
            mcp_server_tron_url="https://mcp.tron.local",
            signer_rpc_url="https://nile.trongrid.io",
            permission_id=3,
        )
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)

        plan = Mock()
        plan.execution_plan.intent_id = "i"
        plan.execution_plan.plan_id = "p"
        step = Mock()
        step.params = {
            "strategy_method": "executeCallWithTRX",
            "amount_sun": "1",
            "target": "TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
            "call_data": "0x1249c58b",
            "strategy_module": "TRuDK4GVYxkzmCnM1ZJJ13btMF7aqMZAtY",
        }
        plan.execution_plan.ordered_steps = [step]

        mock_trigger.return_value = ({"transaction": {"txID": "abc", "raw_data_hex": "00", "raw_data": {"contract": [{"Permission_id": 3}]}}}, "https://mcp.tron.local")
        mock_local_sign.return_value = {"txID": "abc", "signature": ["deadbeef"]}
        mock_post.return_value = {"result": True}

        broadcaster.broadcast_jtrx_plan(plan, owner_address="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY")

        self.assertEqual(mock_post.call_args.kwargs["base_url"], "https://mcp.tron.local")

    @patch.object(TronTxBroadcaster, "_post")
    def test_permissioned_broadcast_retries_on_signer_as_failsafe(self, mock_post):
        cfg = replace(
            load_turnkey_config(),
            use_mcp_server_tron=True,
            mcp_server_tron_url="https://mcp.tron.local",
            signer_rpc_url="https://nile.trongrid.io",
            permission_id=3,
        )
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)
        mock_post.side_effect = [
            {"code": "SIGERROR", "message": "signed by X but it is not contained of permission"},
            {"result": True},
        ]

        out = broadcaster._broadcast_transaction_with_failsafe({"txID": "abc"}, base_url="https://mcp.tron.local")

        self.assertTrue(out["result"])
        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(mock_post.call_args_list[0].kwargs["base_url"], "https://mcp.tron.local")
        self.assertEqual(mock_post.call_args_list[1].kwargs["base_url"], "https://nile.trongrid.io")

    @patch("openclaw_agent.turnkey.onchain.hashlib.sha256")
    def test_sign_transaction_locally_builds_signature_payload(self, mock_sha256):
        import sys

        cfg = load_turnkey_config()
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)

        digest = bytes.fromhex("22" * 32)
        mock_sha256.return_value.digest.return_value = digest

        class _FakePrivateKey:
            def __init__(self, _b):
                pass

            def sign_recoverable(self, d, hasher=None):
                return bytes.fromhex("33" * 65)

        class _FakeModule:
            PrivateKey = _FakePrivateKey

        prev = sys.modules.get("coincurve")
        sys.modules["coincurve"] = _FakeModule
        try:
            tx = {"raw_data_hex": "aa"}
            signed = broadcaster._sign_transaction_locally(tx)
        finally:
            if prev is None:
                del sys.modules["coincurve"]
            else:
                sys.modules["coincurve"] = prev

        self.assertEqual(signed["txID"], digest.hex())
        self.assertEqual(signed["signature"], ["33" * 65])


    @patch.object(TronTxBroadcaster, "_trigger_smart_contract")
    @patch.object(TronTxBroadcaster, "_post")
    @patch.object(TronTxBroadcaster, "_sign_transaction_locally")
    def test_broadcast_supply_trx_from_ops_uses_direct_ops_function(self, mock_local_sign, mock_post, mock_trigger):
        cfg = load_turnkey_config()
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)

        plan = Mock()
        plan.execution_plan.intent_id = "i"
        plan.execution_plan.plan_id = "p"
        step = Mock()
        step.params = {
            "strategy_method": "supplyTRXFromOps",
            "amount_sun": "1000000",
            "target": "TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
            "call_data": "0x1249c58b",
            "strategy_module": "TL7DisbHXzm8kLZHU3tKVMvqzYWErNa6bt",
        }
        plan.execution_plan.ordered_steps = [step]

        tx = {"txID": "abc", "raw_data_hex": "00", "raw_data": {"contract": [{"Permission_id": 2}]}}
        mock_trigger.return_value = {"transaction": tx}
        mock_local_sign.return_value = {"txID": "abc", "signature": ["deadbeef"]}
        mock_post.return_value = {"result": True}

        out = broadcaster.broadcast_jtrx_plan(plan, owner_address="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY")

        self.assertEqual(out.txid, "abc")
        payload = mock_trigger.call_args.args[0]
        self.assertEqual(payload["function_selector"], "supplyTRX(address,uint256)")
        self.assertEqual(payload["call_value"], 0)


    @patch.object(TronTxBroadcaster, "_trigger_smart_contract")
    @patch.object(TronTxBroadcaster, "_post")
    @patch.object(TronTxBroadcaster, "_sign_transaction_locally")
    def test_broadcast_supply_token_from_ops_uses_direct_ops_function(self, mock_local_sign, mock_post, mock_trigger):
        cfg = load_turnkey_config()
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)

        plan = Mock()
        plan.execution_plan.intent_id = "i"
        plan.execution_plan.plan_id = "p"
        step = Mock()
        step.params = {
            "strategy_method": "supplyTokenFromOps",
            "amount_sun": "1000000",
            "target": "TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
            "asset": "USDT",
            "call_data": "0x",
            "strategy_module": "TL7DisbHXzm8kLZHU3tKVMvqzYWErNa6bt",
        }
        plan.execution_plan.ordered_steps = [step]

        tx = {"txID": "abc", "raw_data_hex": "00", "raw_data": {"contract": [{"Permission_id": 2}]}}
        mock_trigger.return_value = {"transaction": tx}
        mock_local_sign.return_value = {"txID": "abc", "signature": ["deadbeef"]}
        mock_post.return_value = {"result": True}

        out = broadcaster.broadcast_jtrx_plan(plan, owner_address="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY")

        self.assertEqual(out.txid, "abc")
        payload = mock_trigger.call_args.args[0]
        self.assertEqual(payload["function_selector"], "supplyToken(address,address,uint256)")
        self.assertEqual(payload["call_value"], 0)
    @patch.object(TronTxBroadcaster, "_trigger_smart_contract")
    @patch.object(TronTxBroadcaster, "_post")
    @patch.object(TronTxBroadcaster, "_sign_transaction_locally")
    def test_broadcast_repay_borrow_from_ops_uses_direct_ops_function(self, mock_local_sign, mock_post, mock_trigger):
        cfg = load_turnkey_config()
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)

        plan = Mock()
        plan.execution_plan.intent_id = "i"
        plan.execution_plan.plan_id = "p"
        step = Mock()
        step.params = {
            "strategy_method": "repayBorrowFromOps",
            "amount_sun": "1000000",
            "target": "TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
            "call_data": "0x0e752702",
            "strategy_module": "TL7DisbHXzm8kLZHU3tKVMvqzYWErNa6bt",
        }
        plan.execution_plan.ordered_steps = [step]

        tx = {"txID": "abc", "raw_data_hex": "00", "raw_data": {"contract": [{"Permission_id": 2}]}}
        mock_trigger.return_value = {"transaction": tx}
        mock_local_sign.return_value = {"txID": "abc", "signature": ["deadbeef"]}
        mock_post.return_value = {"result": True}

        out = broadcaster.broadcast_jtrx_plan(plan, owner_address="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY")

        self.assertEqual(out.txid, "abc")
        payload = mock_trigger.call_args.args[0]
        self.assertEqual(payload["function_selector"], "repayBorrow(address,uint256)")
        self.assertEqual(payload["call_value"], 0)

    @patch.object(TronTxBroadcaster, "_trigger_smart_contract")
    @patch.object(TronTxBroadcaster, "_post")
    @patch.object(TronTxBroadcaster, "_sign_transaction_locally")
    def test_broadcast_repay_borrow_token_from_ops_uses_direct_ops_function(self, mock_local_sign, mock_post, mock_trigger):
        cfg = load_turnkey_config()
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)

        plan = Mock()
        plan.execution_plan.intent_id = "i"
        plan.execution_plan.plan_id = "p"
        step = Mock()
        step.params = {
            "strategy_method": "repayBorrowTokenFromOps",
            "amount_sun": "1000000",
            "target": "TPKHZjesmxmZEuXUzNEAR7wc2M6kAeAx8J",
            "asset": "USDT",
            "call_data": "0x0e752702",
            "strategy_module": "TL7DisbHXzm8kLZHU3tKVMvqzYWErNa6bt",
        }
        plan.execution_plan.ordered_steps = [step]

        tx = {"txID": "abc", "raw_data_hex": "00", "raw_data": {"contract": [{"Permission_id": 2}]}}
        mock_trigger.return_value = {"transaction": tx}
        mock_local_sign.return_value = {"txID": "abc", "signature": ["deadbeef"]}
        mock_post.return_value = {"result": True}

        out = broadcaster.broadcast_jtrx_plan(plan, owner_address="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY")

        self.assertEqual(out.txid, "abc")
        payload = mock_trigger.call_args.args[0]
        self.assertEqual(payload["function_selector"], "repayBorrowToken(address,address,uint256)")
        self.assertEqual(payload["call_value"], 0)

    @patch.object(TronTxBroadcaster, "_trigger_smart_contract")
    @patch.object(TronTxBroadcaster, "_post")
    @patch.object(TronTxBroadcaster, "_sign_transaction_locally")
    def test_broadcast_borrow_from_ops_uses_direct_ops_function(self, mock_local_sign, mock_post, mock_trigger):
        cfg = load_turnkey_config()
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)

        plan = Mock()
        plan.execution_plan.intent_id = "i"
        plan.execution_plan.plan_id = "p"
        step = Mock()
        step.params = {
            "strategy_method": "borrowFromOps",
            "amount_sun": "1000000",
            "target": "TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
            "call_data": "0xc5ebeaec00000000000000000000000000000000000000000000000000000000000f4240",
            "strategy_module": "TL7DisbHXzm8kLZHU3tKVMvqzYWErNa6bt",
        }
        plan.execution_plan.ordered_steps = [step]

        tx = {"txID": "abc", "raw_data_hex": "00", "raw_data": {"contract": [{"Permission_id": 2}]}}
        mock_trigger.return_value = {"transaction": tx}
        mock_local_sign.return_value = {"txID": "abc", "signature": ["deadbeef"]}
        mock_post.return_value = {"result": True}

        out = broadcaster.broadcast_jtrx_plan(plan, owner_address="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY")

        self.assertEqual(out.txid, "abc")
        payload = mock_trigger.call_args.args[0]
        self.assertEqual(payload["function_selector"], "borrow(address,uint256)")
        self.assertEqual(payload["call_value"], 0)

    def test_broadcast_borrow_fails_fast_when_calldata_amount_mismatches_semantic_amount(self):
        cfg = load_turnkey_config()
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)

        plan = Mock()
        plan.execution_plan.intent_id = "i"
        plan.execution_plan.plan_id = "p"
        step = Mock()
        step.params = {
            "strategy_method": "executeBorrow",
            "amount_sun": "1000000",
            "target": "TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
            "collateral_asset": "TAutUnFJhvocKb3egsma3vBEEEkznPbokk",
            "borrow_asset": "TRX",
            "call_data": "0xc5ebeaec00000000000000000000000000000000000000000000000000000000000003e8",
            "strategy_module": "TRuDK4GVYxkzmCnM1ZJJ13btMF7aqMZAtY",
        }
        plan.execution_plan.ordered_steps = [step]

        with self.assertRaises(TronBroadcastError) as exc:
            broadcaster.broadcast_jtrx_plan(plan, owner_address="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY")

        self.assertIn("call_data_amount_mismatch", str(exc.exception))


    @patch.object(TronTxBroadcaster, "_trigger_smart_contract")
    @patch.object(TronTxBroadcaster, "_post")
    @patch.object(TronTxBroadcaster, "_sign_transaction_locally")
    def test_broadcast_ops_sweep_trx_uses_local_signing(self, mock_local_sign, mock_post, mock_trigger):
        cfg = load_turnkey_config()
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)

        tx = {"txID": "abc", "raw_data_hex": "00", "raw_data": {"contract": [{"Permission_id": 2}]}}
        mock_trigger.return_value = {"transaction": tx}
        mock_local_sign.return_value = {"txID": "abc", "signature": ["deadbeef"]}
        mock_post.return_value = {"result": True}

        out = broadcaster.broadcast_ops_sweep_trx(
            owner_address="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY",
            ops_module="TL7DisbHXzm8kLZHU3tKVMvqzYWErNa6bt",
        )

        self.assertEqual(out.txid, "abc")
        mock_local_sign.assert_called_once_with(tx)

    @patch.object(TronTxBroadcaster, "_trigger_smart_contract")
    @patch.object(TronTxBroadcaster, "_post")
    @patch.object(TronTxBroadcaster, "_sign_transaction_locally")
    def test_broadcast_ops_sweep_token_uses_local_signing(self, mock_local_sign, mock_post, mock_trigger):
        cfg = load_turnkey_config()
        broadcaster = TronTxBroadcaster(cfg, private_key_hex="11" * 32)

        tx = {"txID": "abc", "raw_data_hex": "00", "raw_data": {"contract": [{"Permission_id": 2}]}}
        mock_trigger.return_value = {"transaction": tx}
        mock_local_sign.return_value = {"txID": "abc", "signature": ["deadbeef"]}
        mock_post.return_value = {"result": True}

        out = broadcaster.broadcast_ops_sweep_token(
            owner_address="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY",
            ops_module="TL7DisbHXzm8kLZHU3tKVMvqzYWErNa6bt",
            token_address="TPCkaTQ2gmq3Y7UfGQLwgd3KT68TMGq5f9",
        )

        self.assertEqual(out.txid, "abc")
        mock_local_sign.assert_called_once_with(tx)



if __name__ == "__main__":
    unittest.main()
