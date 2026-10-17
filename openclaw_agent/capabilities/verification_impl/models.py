from dataclasses import dataclass, field
from typing import Dict, List


REQUIRED_EVIDENCE_FIELDS = {
    "scenario_id",
    "input_artifacts",
    "execution_artifacts",
    "reconciliation_artifacts",
    "approval_artifacts",
    "result",
    "defects",
    "reviewer_signoff",
}


@dataclass(frozen=True)
class VerificationScenario:
    scenario_id: str
    category: str
    input_artifacts: Dict[str, str] = field(default_factory=dict)
    execution_artifacts: Dict[str, str] = field(default_factory=dict)
    reconciliation_artifacts: Dict[str, str] = field(default_factory=dict)
    approval_artifacts: Dict[str, str] = field(default_factory=dict)
    defects: List[str] = field(default_factory=list)
    reviewer_signoff: str = ""


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    passed: bool
    errors: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class VerificationReport:
    total: int
    passed: int
    failed: int
    accepted: bool
    failed_scenarios: List[str] = field(default_factory=list)
    acceptance_errors: List[str] = field(default_factory=list)
