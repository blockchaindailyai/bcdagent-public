const express = require('express');
const helmet = require('helmet');
const cors = require('cors');
const morgan = require('morgan');
const rateLimit = require('express-rate-limit');
const { createIndexerStore, resolveIndexerConfig } = require('./indexerStore');
const { createObservabilityState } = require('./observability');
const { searchReviewerOperatorEvents } = require('./searchService');

const DEFAULT_TARGET = 'https://nile.trongrid.io';
const DEFAULT_PORT = 3000;
const DEFAULT_TIMEOUT_MS = 12000;
const DEFAULT_RETRIES = 2;

function requireEnvNumber(name, fallback) {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`Invalid numeric env ${name}=${raw}`);
  }
  return parsed;
}

function buildConfig() {
  const targetRpcUrl = (process.env.MCP_TRON_TARGET_RPC || DEFAULT_TARGET).trim().replace(/\/$/, '');
  const port = requireEnvNumber('MCP_TRON_PORT', DEFAULT_PORT);
  const timeoutMs = requireEnvNumber('MCP_TRON_TIMEOUT_MS', DEFAULT_TIMEOUT_MS);
  const retries = requireEnvNumber('MCP_TRON_RETRIES', DEFAULT_RETRIES);
  const allowedOrigin = (process.env.MCP_TRON_ALLOWED_ORIGIN || '*').trim();
  const maxBodySize = (process.env.MCP_TRON_MAX_BODY_SIZE || '1mb').trim();
  const tronProApiKey = (process.env.TRON_PRO_API_KEY || '').trim();
  const indexer = resolveIndexerConfig();

  return {
    targetRpcUrl,
    port,
    timeoutMs,
    retries,
    allowedOrigin,
    maxBodySize,
    tronProApiKey,
    indexer,
  };
}

function buildError(message, details = {}) {
  return {
    error: {
      message,
      ...details,
    },
  };
}


function safeJsonBody(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return null;
  }
  return raw;
}

async function fetchWithRetry(url, options, retries) {
  let lastError;

  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      const response = await fetch(url, options);
      if (response.status >= 500 && attempt < retries) {
        await new Promise((resolve) => setTimeout(resolve, 150 * (attempt + 1)));
        continue;
      }
      return response;
    } catch (err) {
      lastError = err;
      if (attempt < retries) {
        await new Promise((resolve) => setTimeout(resolve, 150 * (attempt + 1)));
        continue;
      }
      throw lastError;
    }
  }

  throw lastError || new Error('Unknown upstream failure');
}


function parsePositivePermissionId(raw) {
  if (raw === undefined || raw === null || raw === '') return null;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return Math.trunc(parsed);
}

function extractRequestedPermissionId(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null;
  return parsePositivePermissionId(payload.permission_id ?? payload.Permission_id);
}

function withNormalizedPermissionPayload(payload, permissionId) {
  if (!permissionId) return payload;
  return {
    ...payload,
    permission_id: permissionId,
    Permission_id: permissionId,
  };
}

function extractPermissionIdFromTriggerResponse(responseBody) {
  if (!responseBody || typeof responseBody !== 'object') return null;
  const tx = responseBody.transaction;
  if (!tx || typeof tx !== 'object') return null;
  const rawData = tx.raw_data;
  if (!rawData || typeof rawData !== 'object') return null;
  const contracts = rawData.contract;
  if (!Array.isArray(contracts) || contracts.length < 1 || typeof contracts[0] !== 'object') return null;
  return parsePositivePermissionId(contracts[0].Permission_id ?? contracts[0].permission_id);
}

function createApp(config = buildConfig()) {
  const app = express();
  const indexerStore = createIndexerStore(config.indexer);
  const observability = createObservabilityState();

  app.disable('x-powered-by');
  app.use(helmet({ contentSecurityPolicy: false }));
  app.use(cors({ origin: config.allowedOrigin === '*' ? true : config.allowedOrigin }));
  app.use(express.json({ limit: config.maxBodySize }));
  app.use(morgan('combined'));
  app.use(observability.middleware);
  app.use(
    rateLimit({
      windowMs: 60 * 1000,
      max: 600,
      standardHeaders: true,
      legacyHeaders: false,
    })
  );

  app.get('/health', async (req, res) => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), config.timeoutMs);

    try {
      const response = await fetchWithRetry(
        `${config.targetRpcUrl}/wallet/getnowblock`,
        {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            ...(config.tronProApiKey ? { 'TRON-PRO-API-KEY': config.tronProApiKey } : {}),
          },
          body: '{}',
          signal: controller.signal,
        },
        config.retries
      );

      const payload = await response.json();
      const healthy = response.ok && Boolean(payload.block_header);
      res.status(healthy ? 200 : 503).json({
        service: 'mcp-server-tron',
        network: 'tron-nile',
        target_rpc_url: config.targetRpcUrl,
        healthy,
        observability: observability.snapshot({ indexer: indexerStore.stats() }),
      });
    } catch (error) {
      res.status(503).json({
        service: 'mcp-server-tron',
        network: 'tron-nile',
        target_rpc_url: config.targetRpcUrl,
        healthy: false,
        error: String(error.message || error),
      });
    } finally {
      clearTimeout(timeout);
    }
  });

  app.post('/wallet/*', async (req, res) => {
    observability.increment('wallet_proxy_requests');
    const path = req.path;
    const upstreamUrl = `${config.targetRpcUrl}${path}`;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), config.timeoutMs);

    try {
      const requestedPermissionId = path === '/wallet/triggersmartcontract'
        ? extractRequestedPermissionId(req.body || {})
        : null;

      const callUpstream = async (payload) => fetchWithRetry(
        upstreamUrl,
        {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            ...(config.tronProApiKey ? { 'TRON-PRO-API-KEY': config.tronProApiKey } : {}),
          },
          body: JSON.stringify(payload || {}),
          signal: controller.signal,
        },
        config.retries
      );

      let upstreamPayload = req.body || {};
      if (requestedPermissionId) {
        upstreamPayload = withNormalizedPermissionPayload(upstreamPayload, requestedPermissionId);
      }

      let response = await callUpstream(upstreamPayload);
      let text = await response.text();
      let parsed = null;
      try {
        parsed = text ? JSON.parse(text) : {};
      } catch (_e) {
        parsed = null;
      }

      if (requestedPermissionId && response.ok) {
        const appliedPermissionId = extractPermissionIdFromTriggerResponse(parsed);
        if (appliedPermissionId !== requestedPermissionId) {
          observability.increment('permissioned_trigger_retries');
          const retryPayload = withNormalizedPermissionPayload({ ...(req.body || {}) }, requestedPermissionId);
          response = await callUpstream(retryPayload);
          text = await response.text();
          try {
            parsed = text ? JSON.parse(text) : {};
          } catch (_e) {
            parsed = null;
          }
        }
      }

      if (requestedPermissionId && response.ok) {
        const appliedPermissionId = extractPermissionIdFromTriggerResponse(parsed);
        if (appliedPermissionId !== requestedPermissionId) {
          observability.increment('permissioned_trigger_mismatches');
          res.status(502).json(
            buildError('Permission id not applied by upstream trigger', {
              path,
              requested_permission_id: requestedPermissionId,
              applied_permission_id: appliedPermissionId,
              upstream_url: upstreamUrl,
              upstream_response: parsed || text,
            })
          );
          return;
        }
      }

      res.status(response.status);
      res.setHeader('content-type', response.headers.get('content-type') || 'application/json');
      res.send(text);
    } catch (error) {
      observability.increment('upstream_failures');
      res.status(502).json(
        buildError('Upstream TRON RPC request failed', {
          path,
          upstream_url: upstreamUrl,
          details: String(error.message || error),
        })
      );
    } finally {
      clearTimeout(timeout);
    }
  });


  app.post('/ops/indexer/events', (req, res) => {
    observability.increment('indexer_ingest_requests');
    try {
      const payload = safeJsonBody(req.body);
      if (!payload) {
        res.status(400).json(buildError('Invalid JSON event payload'));
        return;
      }
      const stored = indexerStore.appendEvent(payload);
      res.status(202).json({ ok: true, event: stored });
    } catch (error) {
      observability.increment('indexer_failures');
      res.status(400).json(buildError('Indexer event ingestion failed', { details: String(error.message || error) }));
    }
  });

  app.get('/ops/indexer/events', (req, res) => {
    observability.increment('indexer_read_requests');
    const limit = req.query.limit || 50;
    const events = indexerStore.readRecentEvents(limit);
    res.json({
      ok: true,
      events,
      indexer: indexerStore.stats(),
    });
  });

  app.get('/ops/search/reviewer-operator', (req, res) => {
    observability.increment('search_requests');
    const events = indexerStore.readRecentEvents(config.indexer.maxReadEvents);
    const payload = searchReviewerOperatorEvents(events, {
      query: req.query.q || '',
      role: req.query.role || 'all',
      limit: req.query.limit || 20,
    });
    res.json({
      ...payload,
      source: 'mcp-server-tron',
      indexer: indexerStore.stats(),
    });
  });

  app.get('/ops/observability', (req, res) => {
    res.json({
      service: 'mcp-server-tron',
      network: 'tron-nile',
      target_rpc_url: config.targetRpcUrl,
      observability: observability.snapshot({ indexer: indexerStore.stats() }),
    });
  });

  app.use((req, res) => {
    res.status(404).json(buildError('Route not found', { path: req.path }));
  });

  app.use((err, req, res, next) => {
    if (err && err.type === 'entity.too.large') {
      return res.status(413).json(buildError('Payload too large', { max: config.maxBodySize }));
    }
    return res.status(500).json(buildError('Internal server error', { details: String(err?.message || err) }));
  });

  return app;
}

function startServer() {
  const config = buildConfig();
  const app = createApp(config);
  const server = app.listen(config.port, () => {
    // eslint-disable-next-line no-console
    console.log(`[mcp-server-tron] listening on :${config.port} -> ${config.targetRpcUrl}`);
  });

  const shutdown = () => {
    server.close(() => process.exit(0));
  };

  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);

  return server;
}

if (require.main === module) {
  startServer();
}

module.exports = {
  buildConfig,
  buildError,
  createApp,
  fetchWithRetry,
  startServer,
};
