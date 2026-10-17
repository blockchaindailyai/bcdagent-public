from __future__ import annotations

import math

from .models import ApprovalTier, DecisionIntent, PolicyDecision

ALLOWED_ASSETS = {"TRX", "STRX", "SUSDD", "JSTRX", "JTRX", "JUSDT", "JUSDD", "JSUSDD", "USDD", "USDT"}

# collateral -> allowed borrow assets
ALLOWED_BORROW_PAIRS = {
    "JSTRX": {"TRX", "STRX"},
    "JTRX": {"TRX"},
    "JUSDT": {"USDT", "USDD"},
    "JUSDD": {"USDT", "USDD", "SUSDD"},
    "JSUSDD": {"USDT", "USDD", "SUSDD"},
}

# Public-release example thresholds; not deployed policy settings.
AUTO_MAX_ACTION_NAV_PCT = 1.5
AUTO_MAX_DAILY_NAV_PCT = 4.0
DUAL_MIN_ACTION_NAV_PCT = 6.0

SOFT_DRIFT_NAV_PCT = 0.10
HARD_DRIFT_NAV_PCT = 0.30
SOFT_BREACH_LIMIT_24H = 3


def _norm(asset: str | None) -> str | None:
    if asset is None:
        return None
    return asset.strip().upper()


def is_asset_allowed(asset: str) -> bool:
    return _norm(asset) in ALLOWED_ASSETS


def is_borrow_pair_allowed(collateral_asset: str, borrow_asset: str) -> bool:
    collateral = _norm(collateral_asset)
    borrow = _norm(borrow_asset)
    if collateral not in ALLOWED_BORROW_PAIRS:
        return False
    return borrow in ALLOWED_BORROW_PAIRS[collateral]


def approval_tier(
    requested_size_nav_pct: float,
    daily_cumulative_nav_pct: float,
    degraded_data: bool = False,
    emergency: bool = False,
) -> ApprovalTier:
    if any(not math.isfinite(value) or value < 0 for value in
           (requested_size_nav_pct, daily_cumulative_nav_pct)):
        return ApprovalTier.BLOCKED
    if emergency or requested_size_nav_pct >= DUAL_MIN_ACTION_NAV_PCT:
        return ApprovalTier.DUAL
    if degraded_data:
        return ApprovalTier.MANUAL
    if (
        requested_size_nav_pct <= AUTO_MAX_ACTION_NAV_PCT
        and daily_cumulative_nav_pct <= AUTO_MAX_DAILY_NAV_PCT
    ):
        return ApprovalTier.AUTO
    return ApprovalTier.MANUAL


def reconciliation_status(drift_nav_pct: float, soft_breaches_last_24h: int) -> str:
    if not math.isfinite(drift_nav_pct) or drift_nav_pct < 0 or soft_breaches_last_24h < 0:
        return "hard_halt"
    if drift_nav_pct > HARD_DRIFT_NAV_PCT or soft_breaches_last_24h >= SOFT_BREACH_LIMIT_24H:
        return "hard_halt"
    if drift_nav_pct > SOFT_DRIFT_NAV_PCT:
        return "soft_breach"
    return "ok"


def evaluate_intent(
    intent: DecisionIntent,
    *,
    degraded_data: bool = False,
    emergency: bool = False,
) -> PolicyDecision:
    reasons = []
    asset = _norm(intent.asset)

    if asset not in ALLOWED_ASSETS:
        reasons.append(f"asset_not_allowlisted:{asset}")

    if intent.action_type == "borrow":
        collateral = _norm(intent.collateral_asset)
        borrow = _norm(intent.borrow_asset)
        if not collateral or not borrow:
            reasons.append("borrow_missing_assets")
        elif not is_borrow_pair_allowed(collateral, borrow):
            reasons.append(f"borrow_pair_denied:{collateral}->{borrow}")

    tier = approval_tier(
        intent.requested_size_nav_pct,
        intent.daily_cumulative_nav_pct,
        degraded_data=degraded_data,
        emergency=emergency,
    )

    if tier == ApprovalTier.BLOCKED:
        reasons.append("invalid_numeric_policy_input")
    if reasons:
        return PolicyDecision(allowed=False, tier=ApprovalTier.BLOCKED, reasons=reasons)

    return PolicyDecision(allowed=True, tier=tier, reasons=[])
