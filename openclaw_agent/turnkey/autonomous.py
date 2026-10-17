from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True)
class AutonomousCyclePlan:
    cycle_index: int
    action: str
    intent_id: str


@dataclass(frozen=True)
class AutonomousRunSummary:
    total_cycles: int
    succeeded_cycles: int
    failed_cycles: int
    interrupted: bool


def parse_action_sequence(raw: str) -> list[str]:
    actions = [item.strip() for item in (raw or "").split(",") if item.strip()]
    if not actions:
        raise ValueError("autonomous_action_sequence_empty")
    return actions


def decide_action_from_metrics(
    *,
    metrics: Mapping[str, float | str],
    repay_threshold: float,
    borrow_threshold: float,
    repay_collateral_ratio_below: float | None = None,
    borrow_collateral_ratio_above: float | None = None,
) -> str:
    """
    Basic adaptive policy for testnet burn-in:
    - repay_borrow when risk is high
    - borrow when risk is low and collateral ratio is healthy
    - mint otherwise

    Borrow/collateral thresholds can be expressed either directly from borrow ratio
    (`repay_threshold`, `borrow_threshold`) or via collateral-ratio wrappers
    (`repay_collateral_ratio_below`, `borrow_collateral_ratio_above`).
    """
    borrow_ratio = float(metrics.get("borrow_ratio", 0.0))
    collateral_ratio = float(metrics.get("collateral_ratio", 0.0))

    effective_repay_threshold, effective_borrow_threshold = resolve_effective_thresholds(
        repay_threshold=repay_threshold,
        borrow_threshold=borrow_threshold,
        repay_collateral_ratio_below=repay_collateral_ratio_below,
        borrow_collateral_ratio_above=borrow_collateral_ratio_above,
    )

    if borrow_ratio >= effective_repay_threshold:
        return "repayBorrow"
    # Require positive collateral exposure before repeatedly selecting borrow.
    # This prevents degenerate loops when telemetry returns all-zero balances.
    if borrow_ratio <= effective_borrow_threshold and collateral_ratio >= effective_borrow_threshold and collateral_ratio > 0:
        return "borrow"
    return "mint"


def resolve_effective_thresholds(
    *,
    repay_threshold: float,
    borrow_threshold: float,
    repay_collateral_ratio_below: float | None = None,
    borrow_collateral_ratio_above: float | None = None,
) -> tuple[float, float]:
    effective_repay_threshold = float(repay_threshold)
    if repay_collateral_ratio_below is not None:
        effective_repay_threshold = 1.0 - float(repay_collateral_ratio_below)

    effective_borrow_threshold = float(borrow_threshold)
    if borrow_collateral_ratio_above is not None:
        effective_borrow_threshold = 1.0 - float(borrow_collateral_ratio_above)

    return effective_repay_threshold, effective_borrow_threshold


def build_cycle_plan(*, cycle_index: int, actions: Sequence[str], intent_prefix: str, epoch_s: int | None = None) -> AutonomousCyclePlan:
    if not actions:
        raise ValueError("autonomous_action_sequence_empty")

    current_epoch = int(epoch_s if epoch_s is not None else time.time())
    action = actions[cycle_index % len(actions)]
    intent_id = f"{intent_prefix}-{current_epoch}-{cycle_index:04d}-{action}"

    return AutonomousCyclePlan(cycle_index=cycle_index, action=action, intent_id=intent_id)


def override_cycle_action(plan: AutonomousCyclePlan, selected_action: str) -> AutonomousCyclePlan:
    if not selected_action:
        raise ValueError("autonomous_selected_action_empty")

    original_action = plan.action
    if not original_action:
        raise ValueError("autonomous_original_action_empty")

    suffix = f"-{original_action}"
    if plan.intent_id.endswith(suffix):
        intent_id = f"{plan.intent_id[: -len(suffix)]}-{selected_action}"
    else:
        intent_id = f"{plan.intent_id}-{selected_action}"

    return AutonomousCyclePlan(cycle_index=plan.cycle_index, action=selected_action, intent_id=intent_id)


def run_autonomous_cycles(
    *,
    actions: Sequence[str],
    intent_prefix: str,
    max_cycles: int,
    interval_s: float,
    stop_on_failure: bool,
    cycle_runner: Callable[[AutonomousCyclePlan], int],
    action_selector: Callable[[int], str] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> AutonomousRunSummary:
    if max_cycles < 1:
        raise ValueError("autonomous_max_cycles_must_be_positive")

    sleeper = sleep_fn or time.sleep

    succeeded = 0
    failed = 0
    interrupted = False

    for cycle_index in range(max_cycles):
        plan = build_cycle_plan(cycle_index=cycle_index, actions=actions, intent_prefix=intent_prefix)
        if action_selector:
            selected_action = action_selector(cycle_index)
            plan = override_cycle_action(plan, selected_action)
        rc = cycle_runner(plan)

        if rc == 0:
            succeeded += 1
        else:
            failed += 1
            if stop_on_failure:
                break

        if cycle_index < (max_cycles - 1) and interval_s > 0:
            sleeper(interval_s)

    return AutonomousRunSummary(
        total_cycles=succeeded + failed,
        succeeded_cycles=succeeded,
        failed_cycles=failed,
        interrupted=interrupted,
    )
