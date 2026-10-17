from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
import hashlib
from dataclasses import dataclass
from pathlib import Path

from .config import TurnkeyConfig
from .onchain import TronTxBroadcaster
from .runtime import _abi_encode_address, _b58check_decode


@dataclass(frozen=True)
class SmartRouterSwapRequest:
    full_host: str
    smart_router: str
    token_in: str
    token_out: str
    amount_in: int
    amount_out_min: int
    to: str
    deadline: int
    pool_version: list[str]
    version_len: list[int]
    fees: list[int]
    fee_limit: int = 500_000_000
    auto_approve: bool = False
    wait_for_receipt: bool = False
    receipt_timeout_s: int = 90
    auto_amount_out_min: bool = False
    quote_slippage_bps: int = 100


@dataclass(frozen=True)
class SmartRouterStrategySwapRequest:
    config: TurnkeyConfig
    full_host: str
    strategy_module: str
    smart_router: str
    token_in: str
    token_out: str
    amount_in: int
    amount_out_min: int
    to: str
    deadline: int
    pool_version: list[str]
    version_len: list[int]
    fees: list[int]
    owner_address: str
    intent_id: str
    plan_id: str
    fee_limit: int = 500_000_000
    wait_for_receipt: bool = False
    receipt_timeout_s: int = 90
    auto_amount_out_min: bool = False
    quote_slippage_bps: int = 100


class SmartRouterSwapError(RuntimeError):
    def __init__(self, message: str, diagnostics: dict[str, object] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _post_json(full_host: str, path: str, payload: dict[str, object]) -> dict[str, object]:
    req = urllib.request.Request(
        f"{full_host}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise SmartRouterSwapError("rpc_invalid_response_shape")
    return parsed


def _decode_uint256_word(data_hex: str) -> int:
    cleaned = data_hex.strip().replace("0x", "")
    if len(cleaned) < 64:
        raise SmartRouterSwapError("rpc_constant_result_too_short")
    return int(cleaned[:64], 16)


def _trigger_constant(full_host: str, *, contract_address: str, function_selector: str, parameter: str = "") -> str:
    body = _post_json(
        full_host,
        "/wallet/triggerconstantcontract",
        {
            "owner_address": contract_address,
            "contract_address": contract_address,
            "function_selector": function_selector,
            "parameter": parameter,
            "visible": True,
        },
    )
    const = body.get("constant_result", [])
    if not isinstance(const, list) or not const:
        raise SmartRouterSwapError(f"rpc_missing_constant_result:{function_selector}")
    return str(const[0])


def _get_trc20_balance(full_host: str, *, token: str, owner: str) -> int:
    encoded_owner = _abi_encode_address(owner)
    word = _trigger_constant(
        full_host,
        contract_address=token,
        function_selector="balanceOf(address)",
        parameter=encoded_owner,
    )
    return _decode_uint256_word(word)


def ensure_sufficient_trc20_balance(
    full_host: str,
    *,
    token: str,
    owner: str,
    required_amount: int,
) -> dict[str, str]:
    if required_amount <= 0:
        raise SmartRouterSwapError("required_amount_must_be_positive")

    current_balance = _get_trc20_balance(full_host, token=token, owner=owner)
    if current_balance < required_amount:
        raise SmartRouterSwapError(
            "insufficient_token_in_balance",
            diagnostics={
                "owner": owner,
                "token_in": token,
                "required_amount_in": str(required_amount),
                "token_in_balance": str(current_balance),
                "token_in_deficit": str(required_amount - current_balance),
            },
        )

    return {
        "owner": owner,
        "token_in": token,
        "required_amount_in": str(required_amount),
        "token_in_balance": str(current_balance),
    }


def _get_trx_balance(full_host: str, *, owner: str) -> int:
    body = _post_json(full_host, "/wallet/getaccount", {"address": owner, "visible": True})
    value = body.get("balance", 0)
    return int(value if isinstance(value, (int, float, str)) else 0)


def _base58check_encode(raw: bytes) -> str:
    checksum = hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
    full = raw + checksum
    num = int.from_bytes(full, "big")
    out = ""
    while num > 0:
        num, rem = divmod(num, 58)
        out = _B58_ALPHABET[rem] + out
    pad = 0
    for b in full:
        if b == 0:
            pad += 1
        else:
            break
    return ("1" * pad) + out


def _resolve_wtrx_address(req: SmartRouterStrategySwapRequest) -> str:
    cfg_wtrx = str(req.config.allowed_assets.get("WTRX", "")).strip()
    if cfg_wtrx.startswith("T"):
        return cfg_wtrx

    word = _trigger_constant(
        req.full_host,
        contract_address=req.smart_router,
        function_selector="WTRX()",
    )
    cleaned = word.strip().replace("0x", "").lower()
    if len(cleaned) < 40:
        raise SmartRouterSwapError("invalid_wtrx_constant_result")
    addr_20 = bytes.fromhex(cleaned[-40:])
    return _base58check_encode(bytes([0x41]) + addr_20)


def _to_hex20(base58_addr: str) -> str:
    raw = _b58check_decode(base58_addr)
    if len(raw) != 21 or raw[0] != 0x41:
        raise SmartRouterSwapError(f"invalid_tron_address:{base58_addr}")
    return raw[1:].hex().lower()


def _ensure_wtrx_liquidity_for_swap(
    req: SmartRouterStrategySwapRequest,
    *,
    broadcaster: TronTxBroadcaster,
) -> dict[str, object] | None:
    cfg_wtrx = str(req.config.allowed_assets.get("WTRX", "")).strip()
    if cfg_wtrx.startswith("T") and _to_hex20(req.token_in) != _to_hex20(cfg_wtrx):
        return None

    wtrx = cfg_wtrx if cfg_wtrx.startswith("T") else _resolve_wtrx_address(req)
    if _to_hex20(req.token_in) != _to_hex20(wtrx):
        return None

    treasury = req.config.addresses.treasury
    wtrx_balance = _get_trc20_balance(req.full_host, token=req.token_in, owner=treasury)
    if wtrx_balance >= req.amount_in:
        return {
            "wtrx_detected": True,
            "wtrx_address": wtrx,
            "treasury": treasury,
            "wtrx_balance": str(wtrx_balance),
            "required_amount_in": str(req.amount_in),
            "auto_wrap": "not_needed",
        }

    deficit = req.amount_in - wtrx_balance
    trx_balance = _get_trx_balance(req.full_host, owner=treasury)
    if trx_balance < deficit:
        raise SmartRouterSwapError(
            "insufficient_wtrx_and_trx_balance",
            diagnostics={
                "treasury": treasury,
                "wtrx_address": wtrx,
                "token_in": req.token_in,
                "required_amount_in": str(req.amount_in),
                "wtrx_balance": str(wtrx_balance),
                "wtrx_deficit": str(deficit),
                "trx_balance": str(trx_balance),
            },
        )

    wrap_intent_id = f"{req.intent_id}:auto-wrap-wtrx"
    wrap_plan_id = f"{req.plan_id}:auto-wrap-wtrx"
    wrap_result = broadcaster.broadcast_execute_call_with_trx(
        owner_address=req.owner_address,
        strategy_module=req.strategy_module,
        intent_id=wrap_intent_id,
        plan_id=wrap_plan_id,
        amount_sun=deficit,
        target=wtrx,
        call_data_hex="0xd0e30db0",
    )
    wrap_receipt = broadcaster.wait_for_receipt(wrap_result.txid, timeout_s=req.receipt_timeout_s)
    wrap_receipt_result = str(wrap_receipt.get("receipt", {}).get("result") or "").strip()
    if wrap_receipt_result != "SUCCESS":
        raise SmartRouterSwapError(
            "auto_wrap_wtrx_failed",
            diagnostics={
                "treasury": treasury,
                "wtrx_address": wtrx,
                "wtrx_balance": str(wtrx_balance),
                "wtrx_deficit": str(deficit),
                "trx_balance": str(trx_balance),
                "wrap_txid": wrap_result.txid,
                "wrap_receipt": wrap_receipt,
            },
        )

    wrapped_balance = _get_trc20_balance(req.full_host, token=req.token_in, owner=treasury)
    if wrapped_balance < req.amount_in:
        raise SmartRouterSwapError(
            "auto_wrap_wtrx_balance_still_insufficient",
            diagnostics={
                "treasury": treasury,
                "wtrx_address": wtrx,
                "required_amount_in": str(req.amount_in),
                "wtrx_balance_before": str(wtrx_balance),
                "wtrx_balance_after": str(wrapped_balance),
                "wrap_txid": wrap_result.txid,
            },
        )

    return {
        "wtrx_detected": True,
        "wtrx_address": wtrx,
        "treasury": treasury,
        "wtrx_balance": str(wtrx_balance),
        "required_amount_in": str(req.amount_in),
        "trx_balance": str(trx_balance),
        "wtrx_deficit": str(deficit),
        "auto_wrap": "performed",
        "auto_wrap_txid": wrap_result.txid,
        "auto_wrap_receipt": wrap_receipt,
        "wtrx_balance_after": str(wrapped_balance),
    }


def _extract_swap_error_detail(stderr: str, stdout: str) -> tuple[str, dict[str, object]]:
    for raw in (stderr, stdout):
        text = (raw or '').strip()
        if not text:
            continue

        parsed: dict[str, object] | None = None
        for candidate in (text, text.splitlines()[-1]):
            try:
                maybe = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(maybe, dict):
                parsed = maybe
                break

        if parsed is not None:
            err = str(parsed.get("error") or '').strip()
            diagnostics = {
                "status": parsed.get("status"),
                "context": parsed.get("context"),
                "stderr": stderr,
                "stdout": stdout,
            }
            if err:
                return err, diagnostics

        return text, {}
    return "smart_router_swap_failed", {}


def run_smart_router_swap(req: SmartRouterSwapRequest) -> dict[str, object]:
    if req.amount_in <= 0:
        raise SmartRouterSwapError("amount_in_must_be_positive")
    if len(req.pool_version) != len(req.version_len):
        raise SmartRouterSwapError("poolVersion_versionLen_length_mismatch")
    if sum(req.version_len) != 2:
        raise SmartRouterSwapError("versionLen_sum_must_equal_path_length")
    if not req.fees or req.fees[-1] != 0:
        raise SmartRouterSwapError("fees_last_must_be_zero")
    if req.quote_slippage_bps < 0 or req.quote_slippage_bps >= 10_000:
        raise SmartRouterSwapError("quote_slippage_bps_out_of_range")

    script = Path(__file__).resolve().parents[1] / "scripts" / "smart_router_swap_exact_input.js"
    cmd = [
        "node",
        str(script),
        f"--full-host={req.full_host}",
        f"--smart-router={req.smart_router}",
        f"--token-in={req.token_in}",
        f"--token-out={req.token_out}",
        f"--amount-in={req.amount_in}",
        f"--amount-out-min={req.amount_out_min}",
        f"--to={req.to}",
        f"--deadline={req.deadline}",
        f"--pool-version={','.join(req.pool_version)}",
        f"--version-len={','.join(str(x) for x in req.version_len)}",
        f"--fees={','.join(str(x) for x in req.fees)}",
        f"--path={req.token_in},{req.token_out}",
        f"--fee-limit={req.fee_limit}",
        f"--auto-approve={'true' if req.auto_approve else 'false'}",
        f"--wait-for-receipt={'true' if req.wait_for_receipt else 'false'}",
        f"--receipt-timeout-s={req.receipt_timeout_s}",
        f"--auto-amount-out-min={'true' if req.auto_amount_out_min else 'false'}",
        f"--quote-slippage-bps={req.quote_slippage_bps}",
    ]

    completed = subprocess.run(cmd, capture_output=True, text=True)
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()

    if completed.returncode != 0:
        detail, diagnostics = _extract_swap_error_detail(stderr, stdout)
        if not diagnostics:
            diagnostics = {"stderr": stderr, "stdout": stdout}
        raise SmartRouterSwapError(detail, diagnostics=diagnostics)

    if not stdout:
        raise SmartRouterSwapError("empty_swap_output")

    for candidate in (stdout, stdout.splitlines()[-1]):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise SmartRouterSwapError(f"invalid_swap_output:{stdout}")


def prepare_smart_router_strategy_swap(req: SmartRouterStrategySwapRequest) -> dict[str, object]:
    if req.amount_in <= 0:
        raise SmartRouterSwapError("amount_in_must_be_positive")
    if len(req.pool_version) != len(req.version_len):
        raise SmartRouterSwapError("poolVersion_versionLen_length_mismatch")
    if sum(req.version_len) != 2:
        raise SmartRouterSwapError("versionLen_sum_must_equal_path_length")
    if not req.fees or req.fees[-1] != 0:
        raise SmartRouterSwapError("fees_last_must_be_zero")
    if req.quote_slippage_bps < 0 or req.quote_slippage_bps >= 10_000:
        raise SmartRouterSwapError("quote_slippage_bps_out_of_range")

    script = Path(__file__).resolve().parents[1] / "scripts" / "smart_router_swap_exact_input.js"
    cmd = [
        "node",
        str(script),
        "--prepare-only=true",
        f"--full-host={req.full_host}",
        f"--owner-address={req.owner_address}",
        f"--smart-router={req.smart_router}",
        f"--token-in={req.token_in}",
        f"--token-out={req.token_out}",
        f"--amount-in={req.amount_in}",
        f"--amount-out-min={req.amount_out_min}",
        f"--to={req.to}",
        f"--deadline={req.deadline}",
        f"--pool-version={','.join(req.pool_version)}",
        f"--version-len={','.join(str(x) for x in req.version_len)}",
        f"--fees={','.join(str(x) for x in req.fees)}",
        f"--path={req.token_in},{req.token_out}",
        f"--fee-limit={req.fee_limit}",
        f"--wait-for-receipt={'true' if req.wait_for_receipt else 'false'}",
        f"--receipt-timeout-s={req.receipt_timeout_s}",
        f"--auto-amount-out-min={'true' if req.auto_amount_out_min else 'false'}",
        f"--quote-slippage-bps={req.quote_slippage_bps}",
    ]

    completed = subprocess.run(cmd, capture_output=True, text=True)
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if completed.returncode != 0:
        detail, diagnostics = _extract_swap_error_detail(stderr, stdout)
        if not diagnostics:
            diagnostics = {"stderr": stderr, "stdout": stdout}
        raise SmartRouterSwapError(detail, diagnostics=diagnostics)

    if not stdout:
        raise SmartRouterSwapError("empty_strategy_prepare_output")

    for candidate in (stdout, stdout.splitlines()[-1]):
        try:
            payload = json.loads(candidate)
            if not isinstance(payload, dict):
                continue
            return payload
        except json.JSONDecodeError:
            continue
    raise SmartRouterSwapError(f"invalid_strategy_prepare_output:{stdout}")


def run_smart_router_strategy_swap(
    req: SmartRouterStrategySwapRequest,
    *,
    broadcaster: TronTxBroadcaster,
    permission_id: int,
) -> dict[str, object]:
    preflight = _ensure_wtrx_liquidity_for_swap(req, broadcaster=broadcaster)
    treasury_balance_preflight = ensure_sufficient_trc20_balance(
        req.full_host,
        token=req.token_in,
        owner=req.config.addresses.treasury,
        required_amount=req.amount_in,
    )
    prepared = prepare_smart_router_strategy_swap(req)
    strategy_payload = prepared.get("strategyPayload") if isinstance(prepared, dict) else None
    if not isinstance(strategy_payload, dict):
        raise SmartRouterSwapError("missing_strategy_payload")

    call_data = str(strategy_payload.get("callData") or "").strip()
    if not call_data:
        raise SmartRouterSwapError("missing_strategy_call_data")

    amount_in = int(strategy_payload.get("amountIn") or req.amount_in)
    target = str(strategy_payload.get("target") or req.smart_router)

    result = broadcaster.broadcast_execute_call_with_token(
        owner_address=req.owner_address,
        strategy_module=req.strategy_module,
        intent_id=req.intent_id,
        plan_id=req.plan_id,
        asset=req.token_in,
        amount_sun=amount_in,
        target=target,
        call_data_hex=call_data,
    )

    output: dict[str, object] = {
        "status": "submitted",
        "permission_id": permission_id,
        "txid": result.txid,
        "strategy": {
            "strategy_module": req.strategy_module,
            "target": target,
            "asset": req.token_in,
            "amount_in": str(amount_in),
            "intent_id": req.intent_id,
            "plan_id": req.plan_id,
            "selector": strategy_payload.get("selector", "0xcef95229"),
            "call_data": call_data,
        },
        "prepared": prepared,
        "broadcast_response": result.raw_response,
    }
    if preflight is not None:
        output["preflight"] = preflight
    output["treasury_balance_preflight"] = treasury_balance_preflight

    if req.wait_for_receipt:
        receipt = broadcaster.wait_for_receipt(result.txid, timeout_s=req.receipt_timeout_s)
        receipt_result = str(receipt.get("receipt", {}).get("result") or "").strip()
        output["receipt"] = receipt
        output["status"] = "confirmed" if receipt_result == "SUCCESS" else "failed"

    return output


def request_to_dict(req: SmartRouterSwapRequest) -> dict[str, object]:
    return {
        "full_host": req.full_host,
        "smart_router": req.smart_router,
        "token_in": req.token_in,
        "token_out": req.token_out,
        "amount_in": req.amount_in,
        "amount_out_min": req.amount_out_min,
        "to": req.to,
        "deadline": req.deadline,
        "pool_version": req.pool_version,
        "version_len": req.version_len,
        "fees": req.fees,
        "fee_limit": req.fee_limit,
        "auto_approve": req.auto_approve,
        "wait_for_receipt": req.wait_for_receipt,
        "receipt_timeout_s": req.receipt_timeout_s,
        "auto_amount_out_min": req.auto_amount_out_min,
        "quote_slippage_bps": req.quote_slippage_bps,
        "runtime": {
            "python": sys.executable,
            "node_script": "openclaw_agent/scripts/smart_router_swap_exact_input.js",
        },
    }


def strategy_request_to_dict(req: SmartRouterStrategySwapRequest) -> dict[str, object]:
    return {
        "full_host": req.full_host,
        "strategy_module": req.strategy_module,
        "smart_router": req.smart_router,
        "token_in": req.token_in,
        "token_out": req.token_out,
        "amount_in": req.amount_in,
        "amount_out_min": req.amount_out_min,
        "to": req.to,
        "deadline": req.deadline,
        "pool_version": req.pool_version,
        "version_len": req.version_len,
        "fees": req.fees,
        "fee_limit": req.fee_limit,
        "wait_for_receipt": req.wait_for_receipt,
        "receipt_timeout_s": req.receipt_timeout_s,
        "auto_amount_out_min": req.auto_amount_out_min,
        "quote_slippage_bps": req.quote_slippage_bps,
        "owner_address": req.owner_address,
        "intent_id": req.intent_id,
        "plan_id": req.plan_id,
        "runtime": {
            "python": sys.executable,
            "node_script": "openclaw_agent/scripts/smart_router_swap_exact_input.js",
        },
    }
