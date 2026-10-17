from __future__ import annotations

from .models import (
    PromotionDecision,
    RollbackDecision,
    RolloutStage,
    RolloutStatus,
    StageGuardrail,
)


STAGE_GUARDRAILS = {
    RolloutStage.STAGE_0_SIMULATION: StageGuardrail(0.0, 0.0, False),
    RolloutStage.STAGE_1_TESTNET_PILOT: StageGuardrail(0.5, 1.5, False),
    RolloutStage.STAGE_2_MAINNET_SHADOW: StageGuardrail(0.0, 0.0, False),
    RolloutStage.STAGE_3_LIMITED_AUTONOMY: StageGuardrail(1.0, 3.0, True),
    # stage 4 values remain bounded by governance change control in practice,
    # so baseline guardrail mirrors stage 3 until policy update is approved.
    RolloutStage.STAGE_4_SCALED_AUTONOMY: StageGuardrail(1.0, 3.0, True),
}


def get_stage_guardrail(stage: RolloutStage) -> StageGuardrail:
    return STAGE_GUARDRAILS[stage]


def evaluate_rollback(status: RolloutStatus) -> RollbackDecision:
    reasons: list[str] = []

    if status.policy_bypass_incidents > 0:
        reasons.append("policy_bypass_incident")
    if status.unresolved_critical_findings > 0:
        reasons.append("unresolved_critical_finding")
    if status.reconciliation_hard_breaches > 0:
        reasons.append("reconciliation_hard_breach")
    if status.repeated_soft_breach_escalations > 0:
        reasons.append("repeated_soft_breach_escalation")
    if status.missing_manual_dual_approval_evidence > 0:
        reasons.append("missing_manual_dual_approval_evidence")
    if status.deterministic_deny_failures > 0:
        reasons.append("deterministic_deny_failure")

    return RollbackDecision(rollback_required=bool(reasons), reasons=reasons)


def evaluate_stage_promotion(
    current_stage: RolloutStage,
    status: RolloutStatus,
    *,
    decision_execution_parity_ok: bool = True,
    approval_sla_ok: bool = True,
    reconciliation_profile_ok: bool = True,
    sustained_stability_ok: bool = True,
    residual_risk_reapproved: bool = True,
) -> PromotionDecision:
    rollback = evaluate_rollback(status)
    if rollback.rollback_required:
        return PromotionDecision("rollback", rollback.reasons)

    reasons: list[str] = []
    if current_stage == RolloutStage.STAGE_0_SIMULATION:
        if status.unresolved_critical_findings > 0:
            reasons.append("critical_findings_present")
    elif current_stage == RolloutStage.STAGE_1_TESTNET_PILOT:
        if status.unresolved_critical_findings > 0:
            reasons.append("critical_incidents_present")
    elif current_stage == RolloutStage.STAGE_2_MAINNET_SHADOW:
        if not decision_execution_parity_ok:
            reasons.append("decision_execution_parity_below_threshold")
        if not approval_sla_ok:
            reasons.append("approval_sla_outside_target")
    elif current_stage == RolloutStage.STAGE_3_LIMITED_AUTONOMY:
        if status.unresolved_high_incidents > 0:
            reasons.append("unresolved_high_incidents")
        if not reconciliation_profile_ok:
            reasons.append("reconciliation_profile_outside_expected_range")
    elif current_stage == RolloutStage.STAGE_4_SCALED_AUTONOMY:
        if not sustained_stability_ok:
            reasons.append("stability_window_not_met")
        if not residual_risk_reapproved:
            reasons.append("residual_risk_not_reapproved")

    if reasons:
        return PromotionDecision("hold_and_remediate", reasons)
    return PromotionDecision("promote", [])
