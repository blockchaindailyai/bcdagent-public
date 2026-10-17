function toInteger(raw, fallback) {
  const parsed = Number(raw);
  if (!Number.isInteger(parsed) || parsed <= 0) return fallback;
  return parsed;
}

function normalizeEventPayload(event) {
  if (event && typeof event.event_payload === 'object' && !Array.isArray(event.event_payload)) {
    return event.event_payload;
  }
  return event || {};
}

function toSearchHit(event) {
  const payload = normalizeEventPayload(event);
  const approvalArtifacts = payload.approval_artifacts && typeof payload.approval_artifacts === 'object'
    ? payload.approval_artifacts
    : {};
  const evidence = Array.isArray(approvalArtifacts.evidence) ? approvalArtifacts.evidence : [];

  const reviewerIds = [...new Set(
    evidence
      .filter((item) => String(item?.role || '').toLowerCase() === 'reviewer')
      .map((item) => String(item?.approver_id || '').trim())
      .filter(Boolean)
  )].sort();

  const operatorIds = [...new Set(
    evidence
      .filter((item) => String(item?.role || '').toLowerCase() === 'operator')
      .map((item) => String(item?.approver_id || '').trim())
      .filter(Boolean)
  )].sort();

  return {
    event_id: String(event?.event_id || ''),
    observed_at: String(event?.observed_at || ''),
    event_type: String(payload.event || payload.event_type || ''),
    owner: String(payload.owner || ''),
    plan_id: String(payload.plan_id || ''),
    intent_id: String(payload.intent_id || ''),
    action: String(payload.action || ''),
    status: String(payload.status || ''),
    approval_tier: String(payload.approval_tier || ''),
    approvers: Array.isArray(approvalArtifacts.approvers) ? approvalArtifacts.approvers.map(String) : [],
    reviewer_ids: reviewerIds,
    operator_ids: operatorIds,
  };
}

function matchesQuery(hit, query, role) {
  if (role === 'reviewer' && hit.reviewer_ids.length === 0) return false;
  if (role === 'operator' && hit.operator_ids.length === 0) return false;

  const normalized = String(query || '').trim().toLowerCase();
  if (!normalized) return true;

  const haystack = [
    hit.event_id,
    hit.event_type,
    hit.owner,
    hit.plan_id,
    hit.intent_id,
    hit.action,
    hit.status,
    hit.approval_tier,
    ...hit.approvers,
    ...hit.reviewer_ids,
    ...hit.operator_ids,
  ].join(' ').toLowerCase();

  return haystack.includes(normalized);
}

function searchReviewerOperatorEvents(events, options = {}) {
  const query = String(options.query || '');
  const role = ['all', 'reviewer', 'operator'].includes(options.role) ? options.role : 'all';
  const limit = toInteger(options.limit, 20);

  const hits = events
    .map((event) => toSearchHit(event))
    .filter((hit) => matchesQuery(hit, query, role))
    .sort((a, b) => String(b.observed_at).localeCompare(String(a.observed_at)));

  return {
    ok: true,
    query,
    role_filter: role,
    total_hits: hits.length,
    hits: hits.slice(0, limit),
  };
}

module.exports = {
  searchReviewerOperatorEvents,
};
