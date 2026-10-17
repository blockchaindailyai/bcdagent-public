from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .models import ApprovalTier, ExecutionPlan, ExecutionReceipt


@dataclass
class ExecutionContext:
    approved_by: List[str]


class ApprovalError(ValueError):
    pass


def execute_plan(plan: ExecutionPlan, ctx: ExecutionContext) -> ExecutionReceipt:
    approvers = set(ctx.approved_by)

    if plan.approval_tier == ApprovalTier.MANUAL and len(approvers) < 1:
        raise ApprovalError("manual_approval_missing")
    if plan.approval_tier == ApprovalTier.DUAL and len(approvers) < 2:
        raise ApprovalError("dual_approval_missing")
    if plan.approval_tier == ApprovalTier.BLOCKED:
        return ExecutionReceipt(plan_id=plan.plan_id, status="blocked", tx_hashes=[])

    tx_hash = f"tx_{plan.plan_id}"
    return ExecutionReceipt(plan_id=plan.plan_id, status="submitted", tx_hashes=[tx_hash])
