from .models import ReconciliationRecord
from .policy import reconciliation_status


def build_reconciliation_record(plan_id: str, drift_nav_pct: float, soft_breaches_last_24h: int = 0) -> ReconciliationRecord:
    status = reconciliation_status(drift_nav_pct, soft_breaches_last_24h)
    return ReconciliationRecord(plan_id=plan_id, drift_nav_pct=drift_nav_pct, status=status)
