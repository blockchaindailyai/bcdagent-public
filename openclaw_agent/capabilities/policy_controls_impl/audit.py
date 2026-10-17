from typing import Dict

from .models import AuditRecord, DecisionIntent, ExecutionPlan, ExecutionReceipt, PolicyDecision, ReconciliationRecord


class InMemoryAuditStore:
    def __init__(self) -> None:
        self.records: Dict[str, AuditRecord] = {}

    def write(
        self,
        intent: DecisionIntent,
        decision: PolicyDecision,
        plan: ExecutionPlan,
        receipt: ExecutionReceipt,
        reconciliation: ReconciliationRecord,
    ) -> AuditRecord:
        record = AuditRecord(
            intent_id=intent.intent_id,
            plan_id=plan.plan_id,
            policy_allowed=decision.allowed,
            approval_tier=decision.tier,
            execution_status=receipt.status,
            reconciliation_status=reconciliation.status,
        )
        self.records[intent.intent_id] = record
        return record

    def get(self, intent_id: str) -> AuditRecord:
        return self.records[intent_id]
