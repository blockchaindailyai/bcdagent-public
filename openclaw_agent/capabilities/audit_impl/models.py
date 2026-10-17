from dataclasses import dataclass, field
from enum import Enum
from typing import List


class FindingSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    MITIGATED = "mitigated"
    ACCEPTED = "accepted"


@dataclass(frozen=True)
class AuditFinding:
    finding_id: str
    track: str
    severity: FindingSeverity
    description: str
    evidence_refs: List[str]
    owner: str
    target_fix_date: str
    status: FindingStatus
    residual_risk_statement: str = ""


@dataclass(frozen=True)
class FindingsSummary:
    total: int
    critical_open: int
    high_open: int
    high_accepted: int
    open_by_severity: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ReleaseGateDecision:
    allowed: bool
    reasons: List[str] = field(default_factory=list)
