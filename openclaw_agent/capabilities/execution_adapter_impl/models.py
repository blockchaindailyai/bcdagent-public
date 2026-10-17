from dataclasses import dataclass, field
from typing import Any, Dict, List

from openclaw_agent.capabilities.policy_controls import ApprovalTier


@dataclass(frozen=True)
class PolicyDecisionV2:
    intent_id: str
    allowed: bool
    approval_tier: ApprovalTier
    denial_reasons: List[str] = field(default_factory=list)
    limits_applied: List[str] = field(default_factory=list)
    policy_version: str = "v1"


@dataclass(frozen=True)
class ExecutionStep:
    name: str
    params: Dict[str, Any]


@dataclass(frozen=True)
class ExecutionPlanV2:
    intent_id: str
    plan_id: str
    ordered_steps: List[ExecutionStep]
    max_slippage_bps: int
    deadline_epoch_s: int
    required_approvals: int


@dataclass(frozen=True)
class ExecutionReceiptV2:
    plan_id: str
    tx_hashes: List[str]
    status: str
    resource_usage: Dict[str, int] = field(default_factory=dict)
    failure_reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
