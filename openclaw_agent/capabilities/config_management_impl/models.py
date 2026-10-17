from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class PolicyConfigV1:
    version: str
    allowed_assets: List[str]
    reserve_floor_nav_pct: float
    max_daily_turnover_nav_pct: float
    max_weekly_turnover_nav_pct: float
    auto_max_action_nav_pct: float
    auto_max_daily_nav_pct: float
    dual_min_action_nav_pct: float
    soft_drift_nav_pct: float
    hard_drift_nav_pct: float
    soft_breach_limit_24h: int
    allowed_borrow_pairs: Dict[str, List[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class ConfigValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ConfigDiff:
    from_version: str
    to_version: str
    changed_fields: List[str]
