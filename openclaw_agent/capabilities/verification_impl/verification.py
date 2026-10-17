from __future__ import annotations

from typing import Iterable, List

from .models import REQUIRED_EVIDENCE_FIELDS, ScenarioResult, VerificationReport, VerificationScenario


def scenario_to_evidence(scenario: VerificationScenario) -> dict:
    return {
        "scenario_id": scenario.scenario_id,
        "input_artifacts": scenario.input_artifacts,
        "execution_artifacts": scenario.execution_artifacts,
        "reconciliation_artifacts": scenario.reconciliation_artifacts,
        "approval_artifacts": scenario.approval_artifacts,
        "result": "fail" if scenario.defects else "pass",
        "defects": scenario.defects,
        "reviewer_signoff": scenario.reviewer_signoff,
    }


def evaluate_scenario(scenario: VerificationScenario) -> ScenarioResult:
    evidence = scenario_to_evidence(scenario)
    errors: List[str] = []

    for field_name in sorted(REQUIRED_EVIDENCE_FIELDS):
        value = evidence.get(field_name)
        if value in (None, ""):
            errors.append(f"missing_evidence_field:{field_name}")

    if evidence["result"] not in {"pass", "fail"}:
        errors.append("invalid_result")

    if evidence["result"] == "pass" and evidence["defects"]:
        errors.append("pass_with_defects")

    return ScenarioResult(
        scenario_id=scenario.scenario_id,
        passed=not errors and evidence["result"] == "pass",
        errors=errors,
    )


def make_verification_report(
    scenarios: Iterable[VerificationScenario],
    *,
    unresolved_critical_defects: int = 0,
    unaudited_transaction_paths: int = 0,
    deterministic_replay_parity: bool = True,
) -> VerificationReport:
    results = [evaluate_scenario(s) for s in scenarios]
    failed_scenarios = [r.scenario_id for r in results if not r.passed]

    acceptance_errors: List[str] = []
    if failed_scenarios:
        acceptance_errors.append("failed_scenarios_present")
    if unresolved_critical_defects > 0:
        acceptance_errors.append("unresolved_critical_defects")
    if unaudited_transaction_paths > 0:
        acceptance_errors.append("unaudited_transaction_paths")
    if not deterministic_replay_parity:
        acceptance_errors.append("deterministic_replay_parity_failed")

    passed_count = len(results) - len(failed_scenarios)
    return VerificationReport(
        total=len(results),
        passed=passed_count,
        failed=len(failed_scenarios),
        accepted=not acceptance_errors,
        failed_scenarios=failed_scenarios,
        acceptance_errors=acceptance_errors,
    )
