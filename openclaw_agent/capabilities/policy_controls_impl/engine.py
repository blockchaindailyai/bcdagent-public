from __future__ import annotations

from .audit import InMemoryAuditStore
from .executor import ExecutionContext, execute_plan
from .models import DecisionIntent, ExecutionPlan, PolicyDecision
from .policy import evaluate_intent
from .reconciliation import build_reconciliation_record


class Phase1PolicyEngine:
    def __init__(self, audit_store: InMemoryAuditStore | None = None) -> None:
        self.audit_store = audit_store or InMemoryAuditStore()

    def evaluate(self, intent: DecisionIntent, *, degraded_data: bool = False, emergency: bool = False) -> PolicyDecision:
        return evaluate_intent(intent, degraded_data=degraded_data, emergency=emergency)

    def run(
        self,
        intent: DecisionIntent,
        *,
        approved_by: list[str] | None = None,
        degraded_data: bool = False,
        emergency: bool = False,
        drift_nav_pct: float = 0.0,
        soft_breaches_last_24h: int = 0,
    ):
        decision = self.evaluate(intent, degraded_data=degraded_data, emergency=emergency)
        plan = ExecutionPlan(
            plan_id=f"plan_{intent.intent_id}",
            intent_id=intent.intent_id,
            approval_tier=decision.tier,
        )
        receipt = execute_plan(plan, ExecutionContext(approved_by=approved_by or []))
        reconciliation = build_reconciliation_record(
            plan.plan_id,
            drift_nav_pct=drift_nav_pct,
            soft_breaches_last_24h=soft_breaches_last_24h,
        )
        audit_record = self.audit_store.write(intent, decision, plan, receipt, reconciliation)
        return {
            "decision": decision,
            "plan": plan,
            "receipt": receipt,
            "reconciliation": reconciliation,
            "audit": audit_record,
        }
