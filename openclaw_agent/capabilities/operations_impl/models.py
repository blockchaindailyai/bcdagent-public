from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List


class Cadence(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


@dataclass(frozen=True)
class KPIRecord:
    name: str
    value: float
    target_min: float | None = None
    target_max: float | None = None


@dataclass(frozen=True)
class KPIEvaluation:
    name: str
    compliant: bool
    reason: str = ""


@dataclass(frozen=True)
class AttestationCycle:
    cycle_id: str
    cadence: Cadence
    evidence_bundle_ref: str
    findings_delta_ref: str
    remediation_status_ref: str
    signoffs: List[str] = field(default_factory=list)
    autonomy_scope_statement: str = ""


@dataclass(frozen=True)
class AttestationDecision:
    passed: bool
    reasons: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class IncidentRecord:
    incident_id: str
    severity: str
    has_root_cause: bool
    has_control_mapping: bool
    has_owner_and_deadline: bool
    has_regression_update: bool
    findings_linked: bool


@dataclass(frozen=True)
class IncidentLearningResult:
    incident_id: str
    complete: bool
    missing: List[str] = field(default_factory=list)


DEFAULT_OPERATIONAL_CHECKLIST: Dict[Cadence, List[str]] = {
    Cadence.DAILY: [
        "review_failed_or_aborted_actions",
        "validate_reconciliation_exceptions",
        "check_policy_deny_spikes",
        "confirm_no_unauthorized_config_drift",
    ],
    Cadence.WEEKLY: [
        "exposure_and_utilization_report",
        "yield_attribution_variance",
        "approval_lane_utilization_metrics",
        "open_findings_sla_burndown",
    ],
    Cadence.MONTHLY: [
        "control_attestation_review",
        "key_secret_rotation_checks",
        "policy_parameter_drift_audit",
        "residual_risk_refresh",
    ],
    Cadence.QUARTERLY: [
        "red_team_incident_replay",
        "policy_recalibration_workshop",
        "rollback_killswitch_tabletop",
        "strategic_risk_posture_signoff",
    ],
}
