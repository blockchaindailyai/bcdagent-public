from __future__ import annotations

from dataclasses import asdict
from typing import Dict, List

from openclaw_agent.capabilities.policy_controls import DecisionIntent, PolicyDecision

from .models import ExecutionPlanV2, ExecutionStep, PolicyDecisionV2


def to_policy_decision_v2(intent: DecisionIntent, decision: PolicyDecision, *, policy_version: str = "v1") -> PolicyDecisionV2:
    limits_applied: List[str] = [
        "allowlist",
        "borrow_pair_directionality",
        "approval_thresholds",
        "reconciliation_thresholds",
    ]
    return PolicyDecisionV2(
        intent_id=intent.intent_id,
        allowed=decision.allowed,
        approval_tier=decision.tier,
        denial_reasons=list(decision.reasons),
        limits_applied=limits_applied,
        policy_version=policy_version,
    )


def make_execution_plan(
    intent: DecisionIntent,
    decision_v2: PolicyDecisionV2,
    *,
    now_epoch_s: int,
    ttl_s: int = 300,
    max_slippage_bps: int = 50,
) -> ExecutionPlanV2:
    if not decision_v2.allowed:
        raise ValueError("cannot_build_plan_for_disallowed_intent")

    step_params: Dict[str, str] = {
        "asset": intent.asset,
        "action_type": intent.action_type,
        "collateral_asset": intent.collateral_asset or "",
        "borrow_asset": intent.borrow_asset or "",
    }

    step = ExecutionStep(name="justlend_strategy_call", params=step_params)

    required_approvals = {
        "auto": 0,
        "manual": 1,
        "dual": 2,
        "blocked": 99,
    }[decision_v2.approval_tier.value]

    return ExecutionPlanV2(
        intent_id=intent.intent_id,
        plan_id=f"planv2_{intent.intent_id}",
        ordered_steps=[step],
        max_slippage_bps=max_slippage_bps,
        deadline_epoch_s=now_epoch_s + ttl_s,
        required_approvals=required_approvals,
    )


def plan_to_dict(plan: ExecutionPlanV2) -> Dict[str, object]:
    payload = asdict(plan)
    payload["schema"] = "execution_plan_v2"
    return payload
