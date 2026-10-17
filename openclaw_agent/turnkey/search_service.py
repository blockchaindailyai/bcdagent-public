from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .config import TurnkeyConfig


@dataclass(frozen=True)
class SearchHit:
    event_id: str
    observed_at: str
    event_type: str
    owner: str
    plan_id: str
    intent_id: str
    action: str
    status: str
    approval_tier: str
    approvers: List[str]
    reviewer_ids: List[str]
    operator_ids: List[str]
    raw: Dict[str, Any]



def _normalize_event_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(event.get("event_payload"), dict):
        return event["event_payload"]
    return event



def _to_search_hit(event: Dict[str, Any]) -> SearchHit:
    payload = _normalize_event_payload(event)
    approval_artifacts = payload.get("approval_artifacts") if isinstance(payload.get("approval_artifacts"), dict) else {}
    evidence = approval_artifacts.get("evidence") if isinstance(approval_artifacts.get("evidence"), list) else []

    reviewer_ids = sorted(
        {
            str(item.get("approver_id", "")).strip()
            for item in evidence
            if str(item.get("role", "")).strip().lower() == "reviewer" and str(item.get("approver_id", "")).strip()
        }
    )
    operator_ids = sorted(
        {
            str(item.get("approver_id", "")).strip()
            for item in evidence
            if str(item.get("role", "")).strip().lower() == "operator" and str(item.get("approver_id", "")).strip()
        }
    )

    approvers = approval_artifacts.get("approvers") if isinstance(approval_artifacts.get("approvers"), list) else []

    return SearchHit(
        event_id=str(event.get("event_id", "")),
        observed_at=str(event.get("observed_at", "")),
        event_type=str(payload.get("event", payload.get("event_type", ""))),
        owner=str(payload.get("owner", "")),
        plan_id=str(payload.get("plan_id", "")),
        intent_id=str(payload.get("intent_id", "")),
        action=str(payload.get("action", "")),
        status=str(payload.get("status", "")),
        approval_tier=str(payload.get("approval_tier", "")),
        approvers=[str(x) for x in approvers if str(x).strip()],
        reviewer_ids=reviewer_ids,
        operator_ids=operator_ids,
        raw=event,
    )



def _matches_query(hit: SearchHit, query: str, role_filter: str) -> bool:
    q = query.strip().lower()

    if role_filter == "reviewer" and not hit.reviewer_ids:
        return False
    if role_filter == "operator" and not hit.operator_ids:
        return False

    if not q:
        return True

    haystack = " ".join(
        [
            hit.event_id,
            hit.event_type,
            hit.owner,
            hit.plan_id,
            hit.intent_id,
            hit.action,
            hit.status,
            hit.approval_tier,
            *hit.approvers,
            *hit.reviewer_ids,
            *hit.operator_ids,
        ]
    ).lower()
    return q in haystack



def _dedupe_hits(hits: List[SearchHit]) -> List[SearchHit]:
    seen = set()
    out: List[SearchHit] = []
    for hit in hits:
        key = (hit.event_id, hit.plan_id, hit.intent_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out



def search_events_local(events_file: str, query: str = "", role_filter: str = "all", limit: int = 20) -> Dict[str, Any]:
    path = Path(events_file)
    if not path.exists():
        return {"ok": True, "hits": [], "source": "local", "events_file": str(path), "total_hits": 0}

    lines = path.read_text(encoding="utf-8").splitlines()
    hits: List[SearchHit] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        hit = _to_search_hit(event)
        if _matches_query(hit, query, role_filter):
            hits.append(hit)

    hits = _dedupe_hits(hits)
    hits.sort(key=lambda x: x.observed_at, reverse=True)
    selected = hits[: max(limit, 1)]
    return {
        "ok": True,
        "source": "local",
        "events_file": str(path),
        "query": query,
        "role_filter": role_filter,
        "total_hits": len(hits),
        "hits": [
            {
                "event_id": h.event_id,
                "observed_at": h.observed_at,
                "event_type": h.event_type,
                "owner": h.owner,
                "plan_id": h.plan_id,
                "intent_id": h.intent_id,
                "action": h.action,
                "status": h.status,
                "approval_tier": h.approval_tier,
                "approvers": h.approvers,
                "reviewer_ids": h.reviewer_ids,
                "operator_ids": h.operator_ids,
            }
            for h in selected
        ],
    }



def search_events_remote(base_url: str, query: str = "", role_filter: str = "all", limit: int = 20) -> Dict[str, Any]:
    params = urllib.parse.urlencode({
        "q": query,
        "role": role_filter,
        "limit": max(limit, 1),
    })
    url = f"{base_url.rstrip('/')}/ops/search/reviewer-operator?{params}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))



def search_reviewer_operator_events(cfg: TurnkeyConfig, query: str = "", role_filter: str = "all", limit: int = 20) -> Dict[str, Any]:
    if cfg.use_mcp_server_tron and cfg.mcp_server_tron_url.strip():
        try:
            payload = search_events_remote(cfg.mcp_server_tron_url, query=query, role_filter=role_filter, limit=limit)
            payload.setdefault("source", "mcp-server-tron")
            return payload
        except Exception:
            pass
    return search_events_local(cfg.subgraph_indexer_local_path, query=query, role_filter=role_filter, limit=limit)
