from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openclaw_agent.identity_rails import Trc8004IdentityClient


@dataclass(frozen=True)
class ApprovalIdentity:
    approver_id: str
    trc8004_subject: str
    role: str


def parse_approval_identities(raw: str) -> List[ApprovalIdentity]:
    """Parse comma-separated identity entries.

    Format per item: approver_id|trc8004_subject|role
    role is optional and defaults to "reviewer".
    """

    entries = [x.strip() for x in (raw or "").split(",") if x.strip()]
    out: List[ApprovalIdentity] = []
    for entry in entries:
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) < 2:
            raise ValueError(f"invalid_approval_identity_entry:{entry}")
        approver_id = parts[0]
        subject = parts[1]
        role = parts[2] if len(parts) >= 3 and parts[2] else "reviewer"
        if not approver_id or not subject:
            raise ValueError(f"invalid_approval_identity_entry:{entry}")
        if not Trc8004IdentityClient.is_valid_subject(subject):
            raise ValueError(f"invalid_trc8004_subject:{subject}")
        out.append(ApprovalIdentity(approver_id=approver_id, trc8004_subject=subject, role=role))
    return out


def build_approval_artifacts(
    approved_by: List[str],
    identities: List[ApprovalIdentity],
    identity_client: Optional[Trc8004IdentityClient] = None,
) -> Dict[str, Any]:
    approved = sorted(set(x.strip() for x in approved_by if x.strip()))
    idx = {i.approver_id: i for i in identities}

    evidence = []
    for approver in approved:
        item = idx.get(approver)
        onchain_record = identity_client.resolve(approver) if (item and identity_client) else None
        evidence.append(
            {
                "approver_id": approver,
                "trc8004_subject": item.trc8004_subject if item else "",
                "role": item.role if item else "",
                "identity_attested": bool(item),
                "subject_format_valid": Trc8004IdentityClient.is_valid_subject(item.trc8004_subject) if item else False,
                "onchain_active": onchain_record.is_active if onchain_record else False,
            }
        )

    missing_identity_for_approved = sorted([x["approver_id"] for x in evidence if not x["identity_attested"]])
    orphan_identity_entries = sorted([i.approver_id for i in identities if i.approver_id not in approved])

    return {
        "approvers": approved,
        "evidence": evidence,
        "missing_identity_for_approved": missing_identity_for_approved,
        "orphan_identity_entries": orphan_identity_entries,
    }


def build_audit_bundle(
    *,
    network: str,
    chain_id: str,
    owner: str,
    intent: Dict[str, Any],
    decision: Dict[str, Any],
    plan: Dict[str, Any],
    receipt: Dict[str, Any],
    approval_artifacts: Dict[str, Any],
) -> Dict[str, Any]:
    canonical = {
        "network": network,
        "chain_id": chain_id,
        "owner": owner,
        "intent": intent,
        "decision": decision,
        "plan": plan,
        "receipt": receipt,
        "approval_artifacts": approval_artifacts,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()

    return {
        "audit_bundle_id": f"audit_{digest[:24]}",
        "audit_bundle_hash": digest,
        "schema_version": "wave2-v1",
        "record": canonical,
    }
