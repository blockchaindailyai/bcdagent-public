from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any, Dict, List

from .models import ConfigDiff, ConfigValidationResult, PolicyConfigV1

REQUIRED_DENIED_BORROW_DIRECTIONS = {
    ("TRX", "STRX"),
    ("USDD", "SUSDD"),
    ("USDT", "SUSDD"),
}


def normalize_asset(a: str) -> str:
    return a.strip().upper()


def load_policy_config(payload: Dict[str, Any]) -> PolicyConfigV1:
    return PolicyConfigV1(
        version=str(payload["version"]),
        allowed_assets=[normalize_asset(x) for x in payload["allowed_assets"]],
        reserve_floor_nav_pct=float(payload["reserve_floor_nav_pct"]),
        max_daily_turnover_nav_pct=float(payload["max_daily_turnover_nav_pct"]),
        max_weekly_turnover_nav_pct=float(payload["max_weekly_turnover_nav_pct"]),
        auto_max_action_nav_pct=float(payload["auto_max_action_nav_pct"]),
        auto_max_daily_nav_pct=float(payload["auto_max_daily_nav_pct"]),
        dual_min_action_nav_pct=float(payload["dual_min_action_nav_pct"]),
        soft_drift_nav_pct=float(payload["soft_drift_nav_pct"]),
        hard_drift_nav_pct=float(payload["hard_drift_nav_pct"]),
        soft_breach_limit_24h=int(payload["soft_breach_limit_24h"]),
        allowed_borrow_pairs={
            normalize_asset(k): [normalize_asset(v) for v in vals]
            for k, vals in payload.get("allowed_borrow_pairs", {}).items()
        },
    )


def validate_policy_config(cfg: PolicyConfigV1) -> ConfigValidationResult:
    errors: List[str] = []

    numeric_fields = (
        "reserve_floor_nav_pct", "max_daily_turnover_nav_pct", "max_weekly_turnover_nav_pct",
        "auto_max_action_nav_pct", "auto_max_daily_nav_pct", "dual_min_action_nav_pct",
        "soft_drift_nav_pct", "hard_drift_nav_pct", "soft_breach_limit_24h",
    )
    for name in numeric_fields:
        value = getattr(cfg, name)
        if not math.isfinite(value) or value < 0:
            errors.append(f"invalid_numeric:{name}")

    if not cfg.version:
        errors.append("missing_version")

    if cfg.reserve_floor_nav_pct < 0 or cfg.reserve_floor_nav_pct > 100:
        errors.append("invalid_reserve_floor_nav_pct")

    if cfg.auto_max_action_nav_pct < 0:
        errors.append("invalid_auto_max_action_nav_pct")
    if cfg.auto_max_daily_nav_pct < cfg.auto_max_action_nav_pct:
        errors.append("auto_daily_below_auto_action")
    if cfg.dual_min_action_nav_pct < cfg.auto_max_action_nav_pct:
        errors.append("dual_threshold_below_auto_threshold")

    if cfg.soft_drift_nav_pct < 0 or cfg.hard_drift_nav_pct <= cfg.soft_drift_nav_pct:
        errors.append("invalid_drift_thresholds")

    assets = set(cfg.allowed_assets)
    for collateral, borrows in cfg.allowed_borrow_pairs.items():
        if collateral not in assets:
            errors.append(f"collateral_not_allowlisted:{collateral}")
        for b in borrows:
            if b not in assets:
                errors.append(f"borrow_asset_not_allowlisted:{b}")

    # deterministically denied directions must not appear in allowlist map
    for c, b in REQUIRED_DENIED_BORROW_DIRECTIONS:
        if b in cfg.allowed_borrow_pairs.get(c, []):
            errors.append(f"forbidden_borrow_direction_present:{c}->{b}")

    return ConfigValidationResult(valid=not errors, errors=errors)


def diff_configs(old: PolicyConfigV1, new: PolicyConfigV1) -> ConfigDiff:
    old_d = asdict(old)
    new_d = asdict(new)
    changed = sorted([k for k in new_d if old_d.get(k) != new_d.get(k)])
    return ConfigDiff(from_version=old.version, to_version=new.version, changed_fields=changed)
