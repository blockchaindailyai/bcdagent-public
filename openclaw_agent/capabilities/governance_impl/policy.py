from __future__ import annotations

import math

from .models import GovernanceAction, GovernanceDecision

# Public-release example thresholds; not deployed policy settings.
AUTO_MAX_ACTION_NAV_PCT = 1.5
DUAL_MIN_ACTION_NAV_PCT = 6.0


def route_governance_lane(action: GovernanceAction) -> GovernanceDecision:
    reasons: list[str] = []

    if not math.isfinite(action.requested_size_nav_pct) or action.requested_size_nav_pct < 0:
        return GovernanceDecision(False, "blocked", 0, ["invalid_requested_size_nav_pct"])

    if not action.data_quality_healthy:
        reasons.append("degraded_data_manual_required")
        return GovernanceDecision(True, "manual", 1, reasons)

    if action.requires_config_change:
        reasons.append("config_change_manual_required")
        return GovernanceDecision(True, "manual", 1, reasons)

    if action.requested_size_nav_pct >= DUAL_MIN_ACTION_NAV_PCT:
        reasons.append("large_action_dual_required")
        return GovernanceDecision(True, "dual", 2, reasons)

    if action.requested_size_nav_pct <= AUTO_MAX_ACTION_NAV_PCT:
        return GovernanceDecision(True, "auto", 0, reasons)

    reasons.append("above_auto_threshold_manual_required")
    return GovernanceDecision(True, "manual", 1, reasons)
