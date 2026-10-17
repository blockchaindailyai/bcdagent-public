function createObservabilityState() {
  const startedAt = new Date().toISOString();
  const counters = {
    total_requests: 0,
    wallet_proxy_requests: 0,
    indexer_ingest_requests: 0,
    indexer_read_requests: 0,
    upstream_failures: 0,
    indexer_failures: 0,
  };

  const statusCodes = {};

  function increment(field) {
    counters[field] = (counters[field] || 0) + 1;
  }

  function trackResponseStatus(code) {
    const key = String(code);
    statusCodes[key] = (statusCodes[key] || 0) + 1;
  }

  function middleware(req, res, next) {
    increment('total_requests');
    res.on('finish', () => trackResponseStatus(res.statusCode));
    next();
  }

  function snapshot(extra = {}) {
    return {
      started_at: startedAt,
      counters: { ...counters },
      status_codes: { ...statusCodes },
      ...extra,
    };
  }

  return {
    counters,
    increment,
    middleware,
    snapshot,
  };
}

module.exports = {
  createObservabilityState,
};
