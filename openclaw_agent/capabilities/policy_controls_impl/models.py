from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class ApprovalTier(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"
    DUAL = "dual"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class DecisionIntent:
    intent_id: str
    asset: str
    action_type: str
    requested_size_nav_pct: float
    daily_cumulative_nav_pct: float
    collateral_asset: Optional[str] = None
    borrow_asset: Optional[str] = None


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    tier: ApprovalTier
    reasons: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExecutionPlan:
    plan_id: str
    intent_id: str
    approval_tier: ApprovalTier


@dataclass(frozen=True)
class ExecutionReceipt:
    plan_id: str
    status: str
    tx_hashes: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReconciliationRecord:
    plan_id: str
    drift_nav_pct: float
    status: str


@dataclass(frozen=True)
class AuditRecord:
    intent_id: str
    plan_id: str
    policy_allowed: bool
    approval_tier: ApprovalTier
    execution_status: str
    reconciliation_status: str
