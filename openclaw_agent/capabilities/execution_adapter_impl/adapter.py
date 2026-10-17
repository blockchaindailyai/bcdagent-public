from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Set

from .models import ExecutionPlanV2, ExecutionReceiptV2


ALLOWED_JUSTLEND_METHODS: Set[str] = {
    "deposit",
    "withdraw",
    "borrow",
    "repay",
}


@dataclass
class AdapterResult:
    allowed: bool
    reason: str = ""


class JustLendAdapter:
    """A phase-2 adapter stub with deterministic allowlist checks.

    This class does not perform chain calls yet; it validates intent->plan call shape.
    """

    def __init__(self, allowed_methods: Set[str] | None = None):
        self.allowed_methods = allowed_methods or ALLOWED_JUSTLEND_METHODS

    def validate_method(self, method: str) -> AdapterResult:
        if method not in self.allowed_methods:
            return AdapterResult(False, f"method_not_allowlisted:{method}")
        return AdapterResult(True)

    def simulate(self, plan: ExecutionPlanV2) -> ExecutionReceiptV2:
        # Deterministic stub for early integration tests.
        return ExecutionReceiptV2(
            plan_id=plan.plan_id,
            tx_hashes=[f"sim_{plan.plan_id}"],
            status="simulated",
            resource_usage={"energy": 1000, "bandwidth": 300},
        )

    def execute(self, plan: ExecutionPlanV2) -> ExecutionReceiptV2:
        # Execution stub, to be replaced by on-chain integration in later phases.
        return ExecutionReceiptV2(
            plan_id=plan.plan_id,
            tx_hashes=[f"tx_{plan.plan_id}"],
            status="submitted",
            resource_usage={"energy": 1100, "bandwidth": 320},
        )


def validate_plan_shape(plan: ExecutionPlanV2) -> None:
    if not plan.ordered_steps:
        raise ValueError("empty_execution_plan")
    if plan.max_slippage_bps < 0:
        raise ValueError("invalid_slippage")
    if plan.required_approvals < 0:
        raise ValueError("invalid_required_approvals")
    for step in plan.ordered_steps:
        if step.name != "justlend_strategy_call":
            raise ValueError(f"invalid_step:{step.name}")
