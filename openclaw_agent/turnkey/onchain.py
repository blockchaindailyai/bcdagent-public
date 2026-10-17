from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, TYPE_CHECKING

from .config import TurnkeyConfig
from .transport import resolve_tron_transport_context

if TYPE_CHECKING:
    from .jtrx_engine import JTRXExecutionPlan


logger = logging.getLogger(__name__)

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


@dataclass(frozen=True)
class BroadcastResult:
    txid: str
    raw_response: Dict[str, object]


class TronBroadcastError(RuntimeError):
    pass


class TronTxBroadcaster:
    def __init__(self, config: TurnkeyConfig, private_key_hex: str, permission_id: int | None = None):
        self.config = config
        transport = resolve_tron_transport_context(config)
        self.rpc_url = transport.api_base_url
        self.signer_rpc_url = str(getattr(config, "signer_rpc_url", transport.signer_base_url)).rstrip("/")
        self.transport = transport.transport
        self.private_key_hex = private_key_hex.lower().replace("0x", "")
        self.permission_id = int(config.permission_id if permission_id is None else permission_id)

    def broadcast_jtrx_plan(self, plan: "JTRXExecutionPlan", owner_address: str) -> BroadcastResult:
        step = plan.execution_plan.ordered_steps[0]
        method = step.params["strategy_method"]

        intent_id = _to_bytes32(plan.execution_plan.intent_id)
        plan_id = _to_bytes32(plan.execution_plan.plan_id)
        semantic_amount = int(step.params["amount_sun"])
        strategy_amount = int(step.params.get("strategy_amount_sun", semantic_amount))
        strategy_call_value = int(step.params.get("call_value_sun", semantic_amount))
        target = step.params["target"]
        calldata = bytes.fromhex(step.params["call_data"].replace("0x", ""))

        if method == "executeBorrow":
            encoded_amount = _decode_single_uint256_call(calldata, expected_selector="0xc5ebeaec")
            if encoded_amount != semantic_amount:
                raise TronBroadcastError(
                    f"call_data_amount_mismatch:semantic={semantic_amount}:calldata={encoded_amount}"
                )

        if method == "executeCallWithTRX":
            fn = "executeCallWithTRX(bytes32,bytes32,uint256,address,bytes)"
            params_hex = _encode_types(
                ["bytes32", "bytes32", "uint256", "address", "bytes"],
                [intent_id, plan_id, strategy_amount, target, calldata],
            )
            call_value = 0
        elif method == "supplyTRXFromOps":
            fn = "supplyTRX(address,uint256)"
            params_hex = _encode_types(
                ["address", "uint256"],
                [target, semantic_amount],
            )
            call_value = 0
        elif method == "supplyTokenFromOps":
            fn = "supplyToken(address,address,uint256)"
            params_hex = _encode_types(
                ["address", "address", "uint256"],
                [target, _resolve_asset_address(self.config, step.params.get("asset", "")), semantic_amount],
            )
            call_value = 0
        elif method == "repayBorrowFromOps":
            fn = "repayBorrow(address,uint256)"
            params_hex = _encode_types(
                ["address", "uint256"],
                [target, semantic_amount],
            )
            call_value = 0
        elif method == "repayBorrowTokenFromOps":
            fn = "repayBorrowToken(address,address,uint256)"
            params_hex = _encode_types(
                ["address", "address", "uint256"],
                [target, _resolve_asset_address(self.config, step.params.get("asset", "")), semantic_amount],
            )
            call_value = 0
        elif method == "executeBorrow":
            fn = "executeBorrow(bytes32,bytes32,address,address,address,bytes)"
            params_hex = _encode_types(
                ["bytes32", "bytes32", "address", "address", "address", "bytes"],
                [
                    intent_id,
                    plan_id,
                    _resolve_asset_address(self.config, step.params.get("collateral_asset", "")),
                    _resolve_borrow_asset_address(self.config, step.params.get("borrow_asset", ""), target),
                    target,
                    calldata,
                ],
            )
            call_value = 0
        elif method == "executeCallWithToken":
            fn = "executeCallWithToken(bytes32,bytes32,address,uint256,address,bytes)"
            params_hex = _encode_types(
                ["bytes32", "bytes32", "address", "uint256", "address", "bytes"],
                [intent_id, plan_id, _resolve_asset_address(self.config, step.params.get("asset", "")), strategy_amount, target, calldata],
            )
            call_value = 0
        elif method == "borrowFromOps":
            fn = "borrow(address,uint256)"
            params_hex = _encode_types(
                ["address", "uint256"],
                [target, semantic_amount],
            )
            call_value = 0
        else:
            raise TronBroadcastError(f"unsupported_strategy_method:{method}")

        logger.info("Triggering strategy call", extra={"function": fn, "target": target})
        _validate_base58_address("owner_address", owner_address)
        _validate_base58_address("strategy_module", step.params["strategy_module"])
        trigger_payload = {
            "owner_address": owner_address,
            "contract_address": step.params["strategy_module"],
            "function_selector": fn,
            "parameter": params_hex,
            "fee_limit": 100_000_000,
            "call_value": call_value,
            "visible": True,
        }
        if self.permission_id > 0:
            trigger_payload["permission_id"] = self.permission_id
        trigger, trigger_base_url = self._trigger_with_permission_retry(trigger_payload)
        tx = trigger.get("transaction")
        if not tx:
            raise TronBroadcastError(f"trigger_failed:{trigger}")

        if not self.config.enable_local_signing_fallback:
            raise TronBroadcastError("local_signing_required: set enable_local_signing_fallback=true")

        logger.info("Using local signing path")
        signed: Dict[str, object] = self._sign_transaction_locally(tx)

        result = self._broadcast_transaction_with_failsafe(signed, base_url=trigger_base_url)

        txid = signed.get("txID") or tx.get("txID") or ""
        logger.info("Broadcast success", extra={"txid": txid})
        return BroadcastResult(txid=txid, raw_response=result)

    def broadcast_execute_call_with_trx(
        self,
        *,
        owner_address: str,
        strategy_module: str,
        intent_id: str,
        plan_id: str,
        amount_sun: int,
        target: str,
        call_data_hex: str,
    ) -> BroadcastResult:
        intent = _to_bytes32(intent_id)
        plan = _to_bytes32(plan_id)
        calldata = bytes.fromhex(call_data_hex.replace("0x", ""))

        fn = "executeCallWithTRX(bytes32,bytes32,uint256,address,bytes)"
        params_hex = _encode_types(
            ["bytes32", "bytes32", "uint256", "address", "bytes"],
            [intent, plan, int(amount_sun), target, calldata],
        )

        _validate_base58_address("owner_address", owner_address)
        _validate_base58_address("strategy_module", strategy_module)
        trigger_payload = {
            "owner_address": owner_address,
            "contract_address": strategy_module,
            "function_selector": fn,
            "parameter": params_hex,
            "fee_limit": 100_000_000,
            "call_value": 0,
            "visible": True,
        }
        if self.permission_id > 0:
            trigger_payload["permission_id"] = self.permission_id
        trigger, trigger_base_url = self._trigger_with_permission_retry(trigger_payload)
        tx = trigger.get("transaction")
        if not tx:
            raise TronBroadcastError(f"trigger_failed:{trigger}")

        if not self.config.enable_local_signing_fallback:
            raise TronBroadcastError("local_signing_required: set enable_local_signing_fallback=true")

        signed: Dict[str, object] = self._sign_transaction_locally(tx)
        result = self._broadcast_transaction_with_failsafe(signed, base_url=trigger_base_url)

        txid = signed.get("txID") or tx.get("txID") or ""
        return BroadcastResult(txid=txid, raw_response=result)

    def broadcast_execute_call_with_token(
        self,
        *,
        owner_address: str,
        strategy_module: str,
        intent_id: str,
        plan_id: str,
        asset: str,
        amount_sun: int,
        target: str,
        call_data_hex: str,
    ) -> BroadcastResult:
        intent = _to_bytes32(intent_id)
        plan = _to_bytes32(plan_id)
        calldata = bytes.fromhex(call_data_hex.replace("0x", ""))

        fn = "executeCallWithToken(bytes32,bytes32,address,uint256,address,bytes)"
        params_hex = _encode_types(
            ["bytes32", "bytes32", "address", "uint256", "address", "bytes"],
            [intent, plan, asset, int(amount_sun), target, calldata],
        )

        _validate_base58_address("owner_address", owner_address)
        _validate_base58_address("strategy_module", strategy_module)
        trigger_payload = {
            "owner_address": owner_address,
            "contract_address": strategy_module,
            "function_selector": fn,
            "parameter": params_hex,
            "fee_limit": 100_000_000,
            "call_value": 0,
            "visible": True,
        }
        if self.permission_id > 0:
            trigger_payload["permission_id"] = self.permission_id
        trigger, trigger_base_url = self._trigger_with_permission_retry(trigger_payload)
        tx = trigger.get("transaction")
        if not tx:
            raise TronBroadcastError(f"trigger_failed:{trigger}")

        if not self.config.enable_local_signing_fallback:
            raise TronBroadcastError("local_signing_required: set enable_local_signing_fallback=true")

        signed: Dict[str, object] = self._sign_transaction_locally(tx)
        result = self._broadcast_transaction_with_failsafe(signed, base_url=trigger_base_url)

        txid = signed.get("txID") or tx.get("txID") or ""
        return BroadcastResult(txid=txid, raw_response=result)

    def broadcast_ops_sweep_trx(self, *, owner_address: str, ops_module: str) -> BroadcastResult:
        _validate_base58_address("owner_address", owner_address)
        _validate_base58_address("ops_module", ops_module)
        trigger_payload = {
            "owner_address": owner_address,
            "contract_address": ops_module,
            "function_selector": "sweepTRXToTreasury()",
            "parameter": "",
            "fee_limit": 100_000_000,
            "call_value": 0,
            "visible": True,
        }
        if self.permission_id > 0:
            trigger_payload["permission_id"] = self.permission_id

        trigger, trigger_base_url = self._trigger_with_permission_retry(trigger_payload)
        tx = trigger.get("transaction")
        if not tx:
            raise TronBroadcastError(f"trigger_failed:{trigger}")

        if not self.config.enable_local_signing_fallback:
            raise TronBroadcastError("local_signing_required: set enable_local_signing_fallback=true")

        logger.info("Using local signing path")
        signed: Dict[str, object] = self._sign_transaction_locally(tx)
        result = self._broadcast_transaction_with_failsafe(signed, base_url=trigger_base_url)
        txid = signed.get("txID") or tx.get("txID") or ""
        return BroadcastResult(txid=txid, raw_response=result)

    def broadcast_ops_sweep_token(self, *, owner_address: str, ops_module: str, token_address: str) -> BroadcastResult:
        _validate_base58_address("owner_address", owner_address)
        _validate_base58_address("ops_module", ops_module)
        _validate_base58_address("token_address", token_address)
        trigger_payload = {
            "owner_address": owner_address,
            "contract_address": ops_module,
            "function_selector": "sweepTokenToTreasury(address)",
            "parameter": _encode_types(["address"], [token_address]),
            "fee_limit": 100_000_000,
            "call_value": 0,
            "visible": True,
        }
        if self.permission_id > 0:
            trigger_payload["permission_id"] = self.permission_id

        trigger, trigger_base_url = self._trigger_with_permission_retry(trigger_payload)
        tx = trigger.get("transaction")
        if not tx:
            raise TronBroadcastError(f"trigger_failed:{trigger}")

        if not self.config.enable_local_signing_fallback:
            raise TronBroadcastError("local_signing_required: set enable_local_signing_fallback=true")

        logger.info("Using local signing path")
        signed: Dict[str, object] = self._sign_transaction_locally(tx)
        result = self._broadcast_transaction_with_failsafe(signed, base_url=trigger_base_url)
        txid = signed.get("txID") or tx.get("txID") or ""
        return BroadcastResult(txid=txid, raw_response=result)

    def wait_for_receipt(self, txid: str, timeout_s: int = 60) -> Dict[str, object]:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            data = self._post("/wallet/gettransactioninfobyid", {"value": txid, "visible": True})
            if data:
                return data
            time.sleep(2)
        raise TronBroadcastError("receipt_timeout")

    def _post(self, path: str, payload: Dict[str, object], *, base_url: str | None = None) -> Dict[str, object]:
        root_url = (base_url or self.rpc_url).rstrip("/")
        req = urllib.request.Request(
            f"{root_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = ""
            if exc.fp is not None:
                body = exc.fp.read().decode("utf-8", errors="replace")
            if path == "/wallet/gettransactionsign" and exc.code == 404:
                raise TronBroadcastError(
                    "sign_endpoint_unsupported:/wallet/gettransactionsign;"
                    " configure OPENCLAW_SIGNER_RPC_URL to a java-tron FullNode that exposes Wallet API"
                ) from exc
            raise TronBroadcastError(f"rpc_http_error:{path}:{exc.code}:{body}") from exc
        except urllib.error.URLError as exc:
            raise TronBroadcastError(f"rpc_network_error:{path}:{exc.reason}") from exc

    def _sign_transaction_locally(self, tx: Dict[str, object]) -> Dict[str, object]:
        try:
            from coincurve import PrivateKey
        except Exception as exc:  # pragma: no cover - dependency error path
            raise TronBroadcastError(
                "local_signing_unavailable: install coincurve (pip install coincurve) or configure OPENCLAW_SIGNER_RPC_URL"
            ) from exc

        txid = str(tx.get("txID", "")).strip().lower()
        raw_data_hex = str(tx.get("raw_data_hex", "")).strip().lower()
        if not raw_data_hex:
            raise TronBroadcastError("local_signing_missing_raw_data_hex")

        digest = hashlib.sha256(bytes.fromhex(raw_data_hex)).digest()
        digest_hex = digest.hex()
        if txid and txid != digest_hex:
            raise TronBroadcastError("local_signing_txid_mismatch")

        try:
            private_key = PrivateKey(bytes.fromhex(self.private_key_hex))
        except ValueError as exc:
            raise TronBroadcastError("invalid_private_key_hex") from exc

        signature = private_key.sign_recoverable(digest, hasher=None).hex()

        signed = dict(tx)
        signed["txID"] = txid or digest_hex
        signed["signature"] = [signature]
        return signed

    def _broadcast_transaction_with_failsafe(self, signed_tx: Dict[str, object], *, base_url: str) -> Dict[str, object]:
        result = self._post("/wallet/broadcasttransaction", signed_tx, base_url=base_url)
        if result.get("result"):
            return result

        if self._should_retry_broadcast_on_signer(result, base_url=base_url):
            logger.warning("Retrying broadcast via signer RPC due to permission/signature mismatch on primary transport")
            retry = self._post("/wallet/broadcasttransaction", signed_tx, base_url=self.signer_rpc_url)
            if retry.get("result"):
                return retry
            raise TronBroadcastError(f"broadcast_failed:primary={result}:retry={retry}")

        raise TronBroadcastError(f"broadcast_failed:{result}")

    def _should_retry_broadcast_on_signer(self, result: Dict[str, object], *, base_url: str) -> bool:
        if self.permission_id <= 0:
            return False
        if not self.signer_rpc_url or self.signer_rpc_url == base_url:
            return False
        code = str(result.get("code", "")).upper()
        message = str(result.get("message", "")).lower()
        return code == "SIGERROR" and "permission" in message

    def _trigger_with_permission_retry(self, payload: Dict[str, object]) -> tuple[Dict[str, object], str]:
        trigger = self._trigger_smart_contract(payload, base_url=self.rpc_url)
        if self.permission_id <= 0:
            return trigger, self.rpc_url

        tx = trigger.get("transaction")
        if isinstance(tx, dict) and _extract_permission_id(tx) == self.permission_id:
            return trigger, self.rpc_url

        if self.signer_rpc_url and self.signer_rpc_url != self.rpc_url:
            logger.warning(
                "Permission id was not applied by primary trigger path; retrying via signer RPC",
                extra={"requested_permission_id": self.permission_id},
            )
            retry = self._trigger_smart_contract(payload, base_url=self.signer_rpc_url)
            retry_tx = retry.get("transaction")
            if isinstance(retry_tx, dict) and _extract_permission_id(retry_tx) == self.permission_id:
                return retry, self.signer_rpc_url
            tx = retry_tx if isinstance(retry_tx, dict) else tx

        raise TronBroadcastError(
            f"permission_id_not_applied:requested={self.permission_id}:found={_extract_permission_id(tx) if isinstance(tx, dict) else 'none'}"
        )

    def _trigger_smart_contract(self, payload: Dict[str, object], *, base_url: str | None = None) -> Dict[str, object]:
        trigger = self._post("/wallet/triggersmartcontract", payload, base_url=base_url)
        if trigger.get("transaction"):
            return trigger

        result = trigger.get("result")
        message = ""
        if isinstance(result, dict):
            message = str(result.get("message", ""))
        if "NullPointerException" not in message:
            return trigger

        logger.warning("Retrying trigger with hex-visible fallback due to RPC NullPointerException")
        fallback = dict(payload)
        try:
            fallback["owner_address"] = _to_hex_address(str(payload["owner_address"]))
            fallback["contract_address"] = _to_hex_address(str(payload["contract_address"]))
        except ValueError:
            logger.warning("Skipping hex-visible fallback because one or more addresses are not valid base58check")
            return trigger
        fallback["visible"] = False
        return self._post("/wallet/triggersmartcontract", fallback, base_url=base_url)


def _validate_base58_address(field: str, value: str) -> None:
    try:
        _b58check_decode(value)
    except (ValueError, IndexError) as exc:
        raise TronBroadcastError(f"invalid_tron_base58check_address:{field}:{value}") from exc


def _to_bytes32(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def _encode_types(types: list[str], values: list[object]) -> str:
    if len(types) != len(values):
        raise ValueError("types_values_mismatch")

    head_parts: list[bytes] = []
    tail = b""
    static_size = 32 * len(types)

    for t, v in zip(types, values):
        if t == "bytes":
            offset = static_size + len(tail)
            head_parts.append(_enc_uint(offset))
            payload = _enc_dynamic_bytes(v if isinstance(v, bytes) else bytes(v))
            tail += payload
        else:
            head_parts.append(_enc_static(t, v))

    return (b"".join(head_parts) + tail).hex()


def _enc_static(t: str, v: object) -> bytes:
    if t == "bytes32":
        b = v if isinstance(v, bytes) else bytes(v)
        if len(b) != 32:
            raise ValueError("invalid_bytes32")
        return b
    if t == "uint256":
        return _enc_uint(int(v))
    if t == "address":
        return _enc_address(str(v))
    raise ValueError(f"unsupported_type:{t}")


def _enc_uint(v: int) -> bytes:
    if v < 0:
        raise ValueError("negative_uint")
    return v.to_bytes(32, "big")


def _enc_dynamic_bytes(v: bytes) -> bytes:
    padded_len = ((len(v) + 31) // 32) * 32
    return _enc_uint(len(v)) + v.ljust(padded_len, b"\x00")


def _enc_address(base58_addr: str) -> bytes:
    raw = _b58check_decode(base58_addr)
    if len(raw) != 21 or raw[0] != 0x41:
        raise ValueError("invalid_tron_address")
    return raw[1:].rjust(32, b"\x00")


def _b58check_decode(s: str) -> bytes:
    num = 0
    for c in s:
        num = num * 58 + _B58_ALPHABET.index(c)
    full = num.to_bytes((num.bit_length() + 7) // 8, "big")
    pad = 0
    for c in s:
        if c == "1":
            pad += 1
        else:
            break
    full = b"\x00" * pad + full
    if len(full) < 5:
        raise ValueError("invalid_base58")
    data, checksum = full[:-4], full[-4:]
    digest = hashlib.sha256(hashlib.sha256(data).digest()).digest()[:4]
    if checksum != digest:
        raise ValueError("invalid_base58_checksum")
    return data


def _resolve_asset_address(config: TurnkeyConfig, symbol_or_address: str) -> str:
    candidate = config.allowed_assets.get(symbol_or_address, symbol_or_address)
    if not candidate or candidate.upper() == "TRX":
        raise TronBroadcastError("missing_contract_address_for_asset:TRX")
    return candidate


def _resolve_borrow_asset_address(config: TurnkeyConfig, symbol_or_address: str, target: str) -> str:
    candidate = config.allowed_assets.get(symbol_or_address, symbol_or_address)
    if candidate.upper() == "TRX":
        return target
    if not candidate:
        raise TronBroadcastError("missing_contract_address_for_asset:TRX")
    return candidate


def _to_hex_address(base58_addr: str) -> str:
    return _b58check_decode(base58_addr).hex()


def _decode_single_uint256_call(calldata: bytes, expected_selector: str) -> int:
    selector = bytes.fromhex(expected_selector.replace("0x", ""))
    if len(selector) != 4:
        raise TronBroadcastError("invalid_selector_length")
    if len(calldata) != 36:
        raise TronBroadcastError("invalid_call_data_length")
    if calldata[:4] != selector:
        raise TronBroadcastError("invalid_call_data_selector")
    return int.from_bytes(calldata[4:], "big")


def _extract_permission_id(tx: Dict[str, object]) -> int | None:
    raw_data = tx.get("raw_data")
    if not isinstance(raw_data, dict):
        return None
    contracts = raw_data.get("contract")
    if not isinstance(contracts, list) or not contracts:
        return None
    contract = contracts[0]
    if not isinstance(contract, dict):
        return None
    value = contract.get("Permission_id", contract.get("permission_id"))
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
