from __future__ import annotations

from dataclasses import dataclass
import binascii
import logging
from typing import Callable, Dict, List

from openclaw_agent.capabilities.policy_controls import ApprovalTier, DecisionIntent, evaluate_intent
from openclaw_agent.capabilities.execution_adapter import ExecutionPlanV2, ExecutionReceiptV2, ExecutionStep, PolicyDecisionV2

from .config import TurnkeyConfig
from .onchain import TronBroadcastError, TronTxBroadcaster


logger = logging.getLogger(__name__)


JTRX_ACTION_SELECTORS: Dict[str, str] = {
    "mint": "0x1249c58b",
    "redeem_underlying": "0x852a12e3",
    "borrow": "0xc5ebeaec",
    "repay_borrow": "0x0e752702",
    "redeem": "0xdb006a75",
}
TOKEN_MINT_SELECTOR = "0xa0712d68"

_ACTIONS_WITH_AMOUNT_ARG = frozenset({"redeem_underlying", "borrow", "redeem"})

_ALLOWED_ACTIONS = frozenset(JTRX_ACTION_SELECTORS.keys())
_ACTION_ALIASES = {
    "redeemunderlying": "redeem_underlying",
    "repayborrow": "repay_borrow",
}
_ALLOWED_SELECTORS = frozenset(set(JTRX_ACTION_SELECTORS.values()) | {TOKEN_MINT_SELECTOR})


def normalize_jtrx_action(action: str) -> str:
    normalized = action.strip().lower()
    return _ACTION_ALIASES.get(normalized, normalized)


@dataclass(frozen=True)
class JTRXDecisionIntent:
    intent_id: str
    action: str
    amount_sun: int
    requested_size_nav_pct: float
    daily_cumulative_nav_pct: float
    strategy_amount_sun: int | None = None
    market: str | None = None
    underlying_asset: str | None = None


@dataclass(frozen=True)
class JTRXExecutionPlan:
    policy_decision: PolicyDecisionV2
    execution_plan: ExecutionPlanV2


class JTRXDecisionEngine:
    def evaluate(self, intent: JTRXDecisionIntent, *, degraded_data: bool = False, emergency: bool = False) -> PolicyDecisionV2:
        action = normalize_jtrx_action(intent.action)
        if action not in _ALLOWED_ACTIONS:
            return PolicyDecisionV2(
                intent_id=intent.intent_id,
                allowed=False,
                approval_tier=ApprovalTier.BLOCKED,
                denial_reasons=[f"unsupported_jtrx_action:{intent.action}"],
                limits_applied=["jtrx_action_allowlist"],
                policy_version="jtrx-v1",
            )

        action_type = "borrow" if action == "borrow" else "deposit"
        collateral_asset, borrow_asset, policy_asset = _policy_assets_for_intent(action, intent.underlying_asset)
        policy_intent = DecisionIntent(
            intent_id=intent.intent_id,
            asset=policy_asset,
            action_type=action_type,
            requested_size_nav_pct=intent.requested_size_nav_pct,
            daily_cumulative_nav_pct=intent.daily_cumulative_nav_pct,
            collateral_asset=collateral_asset if action == "borrow" else None,
            borrow_asset=borrow_asset if action == "borrow" else None,
        )
        decision = evaluate_intent(policy_intent, degraded_data=degraded_data, emergency=emergency)

        return PolicyDecisionV2(
            intent_id=intent.intent_id,
            allowed=decision.allowed,
            approval_tier=decision.tier,
            denial_reasons=list(decision.reasons),
            limits_applied=[
                "jtrx_action_allowlist",
                "phase1_allowlist",
                "phase1_approval_thresholds",
            ],
            policy_version="jtrx-v1",
        )

    def build_plan(self, intent: JTRXDecisionIntent, policy_decision: PolicyDecisionV2, cfg: TurnkeyConfig, *, now_epoch_s: int, ttl_s: int = 300) -> JTRXExecutionPlan:
        if not policy_decision.allowed:
            raise ValueError("cannot_build_plan_for_disallowed_intent")

        action = normalize_jtrx_action(intent.action)
        use_token_market = bool(intent.underlying_asset and intent.underlying_asset.strip().upper() != "TRX")
        selector = _selector_for_action(action, use_token_market=use_token_market)
        call_data = _encode_calldata(selector, intent.amount_sun, with_amount=action in _ACTIONS_WITH_AMOUNT_ARG or (action == "mint" and use_token_market))
        strategy_method = _strategy_method_for_action(action, use_token_market=use_token_market)
        strategy_amount_sun = _strategy_amount_for_action(action, intent.amount_sun, intent.strategy_amount_sun)
        call_value_sun = _call_value_for_action(action, intent.amount_sun)
        target_market = intent.market.strip() if intent.market else cfg.addresses.jtrx_market

        policy_collateral_asset, policy_borrow_asset, _ = _policy_assets_for_intent(action, intent.underlying_asset)

        params = {
            # Borrow policy semantics remain symbol-level for strategy/pair guardrails.
            # Address translation for borrow-capability checks happens in onchain broadcasting.
            "collateral_asset": policy_collateral_asset if action == "borrow" else "",
            "borrow_asset": policy_borrow_asset if action == "borrow" else "",
            "strategy_method": strategy_method,
            # Ops module owns borrow account state; use ops for stateful debt/collateral
            # actions and reserve strategy module for strategy-wrapped calls (e.g. redeem).
            "strategy_module": cfg.addresses.justlend_ops_module if action in {"mint", "borrow", "repay_borrow"} else cfg.addresses.strategy_module,
            "module_kind": "justlend_ops" if action in {"mint", "borrow", "repay_borrow"} else "strategy",
            "target": target_market,
            "selector": selector,
            "asset": target_market if action in {"redeem", "redeem_underlying"} else (intent.underlying_asset or "TRX"),
            "amount_sun": str(intent.amount_sun),
            "strategy_amount_sun": str(strategy_amount_sun),
            "call_value_sun": str(call_value_sun),
            "call_data": call_data,
        }
        step = ExecutionStep(name="justlend_strategy_call", params=params)

        required_approvals = {
            ApprovalTier.AUTO: 0,
            ApprovalTier.MANUAL: 1,
            ApprovalTier.DUAL: 2,
            ApprovalTier.BLOCKED: 99,
        }[policy_decision.approval_tier]

        return JTRXExecutionPlan(
            policy_decision=policy_decision,
            execution_plan=ExecutionPlanV2(
                intent_id=intent.intent_id,
                plan_id=f"jtrx_{intent.intent_id}",
                ordered_steps=[step],
                max_slippage_bps=0,
                deadline_epoch_s=now_epoch_s + ttl_s,
                required_approvals=required_approvals,
            ),
        )


class JTRXExecutor:
    def __init__(
        self,
        broadcaster: TronTxBroadcaster | None = None,
        owner_address: str = "",
        signer_label: str = "unknown",
        signer_provenance: Dict[str, str] | None = None,
        execution_observer: Callable[[ExecutionReceiptV2], None] | None = None,
    ):
        self.broadcaster = broadcaster
        self.owner_address = owner_address
        self.signer_label = signer_label
        self.signer_provenance = signer_provenance or {}
        self.execution_observer = execution_observer

    def _receipt_metadata(self, transport: str) -> Dict[str, str]:
        return {
            "signer": self.signer_label,
            "transport": transport,
            "signer_role": self.signer_provenance.get("signer_role", "execution"),
            "signer_source": self.signer_provenance.get("signer_source", "unknown"),
            "signer_key_id": self.signer_provenance.get("signer_key_id", ""),
            "provenance_standard": "bankofai-agent-wallet-v1",
        }

    def _transport_label(self) -> str:
        if not self.broadcaster:
            return "tron-rpc"
        return getattr(self.broadcaster, "transport", "tron-rpc") or "tron-rpc"

    def execute(self, plan: JTRXExecutionPlan, *, approved_by: List[str], broadcast: bool = False, wait_for_receipt: bool = False) -> ExecutionReceiptV2:
        required = plan.execution_plan.required_approvals
        if len(set(approved_by)) < required:
            receipt = ExecutionReceiptV2(
                plan_id=plan.execution_plan.plan_id,
                tx_hashes=[],
                status="blocked",
                failure_reason="approval_evidence_missing",
                metadata=self._receipt_metadata("none"),
            )
            self._observe(receipt)
            return receipt

        step = plan.execution_plan.ordered_steps[0]
        selector = step.params["selector"]
        if selector not in _ALLOWED_SELECTORS:
            receipt = ExecutionReceiptV2(
                plan_id=plan.execution_plan.plan_id,
                tx_hashes=[],
                status="failed",
                failure_reason="selector_not_allowlisted",
                metadata=self._receipt_metadata("policy-gate"),
            )
            self._observe(receipt)
            return receipt

        if not broadcast:
            receipt = ExecutionReceiptV2(
                plan_id=plan.execution_plan.plan_id,
                tx_hashes=[f"sim_{plan.execution_plan.plan_id}"],
                status="simulated",
                resource_usage={"energy": 1200, "bandwidth": 400},
                metadata=self._receipt_metadata("simulation"),
            )
            self._observe(receipt)
            return receipt

        if not self.broadcaster or not self.owner_address:
            receipt = ExecutionReceiptV2(
                plan_id=plan.execution_plan.plan_id,
                tx_hashes=[],
                status="failed",
                failure_reason="broadcast_not_configured",
                metadata=self._receipt_metadata("none"),
            )
            self._observe(receipt)
            return receipt

        try:
            transport_label = self._transport_label()
            result = self.broadcaster.broadcast_jtrx_plan(plan, owner_address=self.owner_address)
            tx_hashes = [result.txid]
            if wait_for_receipt:
                receipt = self.broadcaster.wait_for_receipt(result.txid)
                receipt_result = receipt.get("receipt", {}).get("result")
                status = "confirmed" if receipt_result == "SUCCESS" else "failed"
                failure_reason = ""
                if status == "failed":
                    failure_reason = _extract_receipt_failure_reason(receipt)
                logger.info("Receipt", extra={"txid": result.txid, "receipt": receipt})
            else:
                status = "submitted"
                failure_reason = ""
            receipt_obj = ExecutionReceiptV2(
                plan_id=plan.execution_plan.plan_id,
                tx_hashes=tx_hashes,
                status=status,
                resource_usage={"energy": 1200, "bandwidth": 400},
                failure_reason=failure_reason,
                metadata=self._receipt_metadata(transport_label),
            )
            self._observe(receipt_obj)
            return receipt_obj
        except TronBroadcastError as exc:
            receipt = ExecutionReceiptV2(
                plan_id=plan.execution_plan.plan_id,
                tx_hashes=[],
                status="failed",
                failure_reason=str(exc),
                metadata=self._receipt_metadata(self._transport_label()),
            )
            self._observe(receipt)
            return receipt



    def _observe(self, receipt: ExecutionReceiptV2) -> None:
        if self.execution_observer is None:
            return
        try:
            self.execution_observer(receipt)
        except Exception as exc:  # pragma: no cover - non-critical observer path
            logger.warning("execution_observer_failed:%s", exc)


def _policy_assets_for_intent(action: str, underlying_asset: str | None) -> tuple[str, str, str]:
    if action != "borrow":
        return "", "", "TRX"

    canonical = _canonicalize_underlying_symbol(underlying_asset)
    if canonical == "TRX":
        return "jTRX", "TRX", "TRX"

    return f"j{canonical}", canonical, canonical


def _canonicalize_underlying_symbol(underlying_asset: str | None) -> str:
    raw = (underlying_asset or "").strip()
    if not raw:
        return "TRX"

    lowered = raw.lower()
    if lowered == "trx":
        return "TRX"
    if lowered == "strx":
        return "sTRX"

    return raw.upper()

def _selector_for_action(action: str, *, use_token_market: bool) -> str:
    if action == "mint" and use_token_market:
        return TOKEN_MINT_SELECTOR
    return JTRX_ACTION_SELECTORS[action]


def _strategy_method_for_action(action: str, *, use_token_market: bool) -> str:
    if action == "mint":
        return "supplyTokenFromOps" if use_token_market else "supplyTRXFromOps"
    if action == "repay_borrow":
        return "repayBorrowTokenFromOps" if use_token_market else "repayBorrowFromOps"
    if action == "redeem":
        return "executeCallWithToken"
    if action == "redeem_underlying":
        return "executeCallWithToken"
    if action == "borrow":
        return "borrowFromOps"
    raise ValueError(f"unsupported_jtrx_action:{action}")


def _strategy_amount_for_action(action: str, amount_sun: int, strategy_amount_sun: int | None = None) -> int:
    if strategy_amount_sun is not None:
        return strategy_amount_sun
    if action in {"redeem", "redeem_underlying"}:
        return amount_sun
    return 0


def _call_value_for_action(action: str, amount_sun: int) -> int:
    return 0


def _encode_calldata(selector: str, amount_sun: int, *, with_amount: bool) -> str:
    if not with_amount:
        return selector
    if amount_sun < 0:
        raise ValueError("amount_must_be_non_negative")
    return selector + f"{amount_sun:064x}"


def _extract_receipt_failure_reason(receipt: Dict[str, object]) -> str:
    receipt_result = str(receipt.get("receipt", {}).get("result") or "").strip()
    contract_ret = str(receipt.get("result") or "").strip()
    raw_message = receipt.get("resMessage")
    contract_result = receipt.get("contractResult")

    decoded_message = ""
    if isinstance(raw_message, str) and raw_message:
        decoded_message = _decode_res_message(raw_message)

    decoded_contract_result = _decode_contract_result_revert(contract_result)

    for candidate in (decoded_message, decoded_contract_result, contract_ret, receipt_result):
        if candidate:
            return candidate
    return "transaction_reverted"


def _decode_contract_result_revert(contract_result: object) -> str:
    if not isinstance(contract_result, list) or not contract_result:
        return ""
    payload = contract_result[0]
    if not isinstance(payload, str) or not payload:
        return ""

    try:
        data = binascii.unhexlify(payload)
    except (ValueError, binascii.Error):
        return ""

    if len(data) < 4:
        return ""

    selector = data[:4].hex()
    if selector == "08c379a0":  # Error(string)
        if len(data) < 4 + 32 + 32:
            return ""
        strlen = int.from_bytes(data[4 + 32:4 + 64], "big")
        start = 4 + 64
        end = start + strlen
        if end > len(data):
            return ""
        return data[start:end].decode("utf-8", errors="replace").strip()

    if selector == "4e487b71":  # Panic(uint256)
        if len(data) < 4 + 32:
            return ""
        code = int.from_bytes(data[4:4 + 32], "big")
        return f"panic(0x{code:x})"

    return f"revert_selector_0x{selector}"


def _decode_res_message(raw_message: str) -> str:
    try:
        decoded = binascii.unhexlify(raw_message).decode("utf-8", errors="replace").strip()
        if decoded:
            return decoded
    except (ValueError, binascii.Error):
        pass
    return raw_message
