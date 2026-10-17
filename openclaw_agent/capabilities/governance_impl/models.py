from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class GovernanceAction:
    action_id: str
    action_type: str
    requested_size_nav_pct: float
    data_quality_healthy: bool
    requires_config_change: bool = False


@dataclass(frozen=True)
class GovernanceDecision:
    allowed: bool
    lane: str
    required_approvals: int
    reasons: List[str] = field(default_factory=list)
