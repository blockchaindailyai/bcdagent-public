from dataclasses import dataclass, field
from enum import IntEnum
from typing import List


class RolloutStage(IntEnum):
    STAGE_0_SIMULATION = 0
    STAGE_1_TESTNET_PILOT = 1
    STAGE_2_MAINNET_SHADOW = 2
    STAGE_3_LIMITED_AUTONOMY = 3
    STAGE_4_SCALED_AUTONOMY = 4


@dataclass(frozen=True)
class StageGuardrail:
    max_action_nav_pct: float
    max_daily_nav_pct: float
    autonomous_execution_enabled: bool


@dataclass(frozen=True)
class RolloutStatus:
    stage: RolloutStage
    unresolved_critical_findings: int
    unresolved_high_incidents: int
    policy_bypass_incidents: int
    reconciliation_hard_breaches: int
    repeated_soft_breach_escalations: int
    missing_manual_dual_approval_evidence: int
    deterministic_deny_failures: int


@dataclass(frozen=True)
class PromotionDecision:
    outcome: str
    reasons: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RollbackDecision:
    rollback_required: bool
    reasons: List[str] = field(default_factory=list)
