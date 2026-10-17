const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { createApp } = require('../src/server');

function startJsonServer(routes) {
  const server = http.createServer(async (req, res) => {
    let body = '';
    for await (const chunk of req) body += chunk;
    const key = `${req.method} ${req.url}`;
    const handler = routes[key];
    if (!handler) {
      res.writeHead(404, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ error: 'not-found' }));
      return;
    }
    const payload = body ? JSON.parse(body) : {};
    const result = await handler(payload);
    res.writeHead(result.status || 200, { 'content-type': 'application/json' });
    res.end(JSON.stringify(result.body || {}));
  });

  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({ server, port });
    });
  });
}

function requestJson(port, pathName, method = 'GET', body = undefined) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        host: '127.0.0.1',
        port,
        path: pathName,
        method,
        headers: body ? { 'content-type': 'application/json' } : undefined,
      },
      (res) => {
        let data = '';
        res.on('data', (chunk) => {
          data += chunk;
        });
        res.on('end', () => {
          resolve({ status: res.statusCode, body: data ? JSON.parse(data) : {} });
        });
      }
    );

    req.on('error', reject);
    if (body) req.write(JSON.stringify(body));
    req.end();
  });
}

function startApiWithUpstream(upstreamPort) {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mcp-tron-test-'));
  const app = createApp({
    targetRpcUrl: `http://127.0.0.1:${upstreamPort}`,
    port: 0,
    timeoutMs: 5000,
    retries: 0,
    allowedOrigin: '*',
    maxBodySize: '1mb',
    tronProApiKey: '',
    indexer: {
      dataDir: tempDir,
      eventsFile: path.join(tempDir, 'events.jsonl'),
      maxEventBytes: 65536,
      maxReadEvents: 200,
    },
  });

  const server = app.listen(0, '127.0.0.1');
  return { server, tempDir };
}

test('health returns healthy=true when upstream getnowblock is valid', async (t) => {
  const upstream = await startJsonServer({
    'POST /wallet/getnowblock': async () => ({ body: { block_header: { raw_data: {} } } }),
  });
  t.after(() => upstream.server.close());

  const { server, tempDir } = startApiWithUpstream(upstream.port);
  t.after(() => {
    server.close();
    fs.rmSync(tempDir, { recursive: true, force: true });
  });
  await new Promise((resolve) => server.once('listening', resolve));

  const apiPort = server.address().port;
  const response = await requestJson(apiPort, '/health');
  assert.equal(response.status, 200);
  assert.equal(response.body.healthy, true);
  assert.ok(response.body.observability);
});

test('wallet path proxies request payload', async (t) => {
  const upstream = await startJsonServer({
    'POST /wallet/triggerconstantcontract': async (payload) => ({ body: { echoed: payload } }),
  });
  t.after(() => upstream.server.close());

  const { server, tempDir } = startApiWithUpstream(upstream.port);
  t.after(() => {
    server.close();
    fs.rmSync(tempDir, { recursive: true, force: true });
  });
  await new Promise((resolve) => server.once('listening', resolve));

  const apiPort = server.address().port;
  const reqBody = { owner_address: 'T123', function_selector: 'balanceOf(address)' };
  const response = await requestJson(apiPort, '/wallet/triggerconstantcontract', 'POST', reqBody);
  assert.equal(response.status, 200);
  assert.deepEqual(response.body.echoed, reqBody);
});

test('ops indexer ingests and returns recent events', async (t) => {
  const upstream = await startJsonServer({
    'POST /wallet/getnowblock': async () => ({ body: { block_header: { raw_data: {} } } }),
  });
  t.after(() => upstream.server.close());

  const { server, tempDir } = startApiWithUpstream(upstream.port);
  t.after(() => {
    server.close();
    fs.rmSync(tempDir, { recursive: true, force: true });
  });
  await new Promise((resolve) => server.once('listening', resolve));

  const apiPort = server.address().port;
  const ingest = await requestJson(apiPort, '/ops/indexer/events', 'POST', {
    event: 'jtrx_execution_submitted',
    plan_id: 'plan-1',
  });

  assert.equal(ingest.status, 202);
  assert.equal(ingest.body.ok, true);
  assert.equal(ingest.body.event.event, 'jtrx_execution_submitted');

  const events = await requestJson(apiPort, '/ops/indexer/events?limit=5');
  assert.equal(events.status, 200);
  assert.equal(events.body.ok, true);
  assert.equal(events.body.events.length, 1);
  assert.equal(events.body.events[0].plan_id, 'plan-1');
});

test('ops observability reports request counters', async (t) => {
  const upstream = await startJsonServer({
    'POST /wallet/getnowblock': async () => ({ body: { block_header: { raw_data: {} } } }),
  });
  t.after(() => upstream.server.close());

  const { server, tempDir } = startApiWithUpstream(upstream.port);
  t.after(() => {
    server.close();
    fs.rmSync(tempDir, { recursive: true, force: true });
  });
  await new Promise((resolve) => server.once('listening', resolve));

  const apiPort = server.address().port;
  await requestJson(apiPort, '/ops/indexer/events', 'POST', { event: 'x' });
  const response = await requestJson(apiPort, '/ops/observability');
  assert.equal(response.status, 200);
  assert.equal(response.body.service, 'mcp-server-tron');
  assert.ok(response.body.observability.counters.total_requests >= 2);
  assert.ok(response.body.observability.counters.indexer_ingest_requests >= 1);
});

test('triggersmartcontract forwards permission_id and retries when upstream omits applied Permission_id', async (t) => {
  const seenPayloads = [];
  let callCount = 0;
  const upstream = await startJsonServer({
    'POST /wallet/triggersmartcontract': async (payload) => {
      callCount += 1;
      seenPayloads.push(payload);
      if (callCount === 1) {
        return {
          body: {
            transaction: {
              raw_data: {
                contract: [{}],
              },
            },
          },
        };
      }
      return {
        body: {
          transaction: {
            raw_data: {
              contract: [{ Permission_id: payload.permission_id }],
            },
          },
        },
      };
    },
  });
  t.after(() => upstream.server.close());

  const { server, tempDir } = startApiWithUpstream(upstream.port);
  t.after(() => {
    server.close();
    fs.rmSync(tempDir, { recursive: true, force: true });
  });
  await new Promise((resolve) => server.once('listening', resolve));

  const apiPort = server.address().port;
  const response = await requestJson(apiPort, '/wallet/triggersmartcontract', 'POST', {
    owner_address: 'T123',
    permission_id: '3',
  });

  assert.equal(response.status, 200);
  assert.equal(callCount, 2);
  assert.equal(seenPayloads[0].permission_id, 3);
  assert.equal(seenPayloads[0].Permission_id, 3);
  assert.equal(response.body.transaction.raw_data.contract[0].Permission_id, 3);
});

test('triggersmartcontract returns 502 when permission_id still not applied after retry', async (t) => {
  const upstream = await startJsonServer({
    'POST /wallet/triggersmartcontract': async () => ({
      body: {
        transaction: {
          raw_data: {
            contract: [{}],
          },
        },
      },
    }),
  });
  t.after(() => upstream.server.close());

  const { server, tempDir } = startApiWithUpstream(upstream.port);
  t.after(() => {
    server.close();
    fs.rmSync(tempDir, { recursive: true, force: true });
  });
  await new Promise((resolve) => server.once('listening', resolve));

  const apiPort = server.address().port;
  const response = await requestJson(apiPort, '/wallet/triggersmartcontract', 'POST', {
    owner_address: 'T123',
    permission_id: 3,
  });

  assert.equal(response.status, 502);
  assert.equal(response.body.error.message, 'Permission id not applied by upstream trigger');
  assert.equal(response.body.error.requested_permission_id, 3);
});

test('ops search reviewer/operator returns filtered decision artifacts', async (t) => {
  const upstream = await startJsonServer({
    'POST /wallet/getnowblock': async () => ({ body: { block_header: { raw_data: {} } } }),
  });
  t.after(() => upstream.server.close());

  const { server, tempDir } = startApiWithUpstream(upstream.port);
  t.after(() => {
    server.close();
    fs.rmSync(tempDir, { recursive: true, force: true });
  });
  await new Promise((resolve) => server.once('listening', resolve));

  const apiPort = server.address().port;
  await requestJson(apiPort, '/ops/indexer/events', 'POST', {
    event: 'jtrx_audit_bundle',
    owner: 'T-operator',
    plan_id: 'plan-review',
    intent_id: 'intent-1',
    approval_artifacts: {
      approvers: ['alice', 'bob'],
      evidence: [
        { approver_id: 'alice', role: 'reviewer' },
        { approver_id: 'bob', role: 'operator' },
      ],
    },
  });

  const response = await requestJson(apiPort, '/ops/search/reviewer-operator?role=reviewer&q=alice&limit=5');
  assert.equal(response.status, 200);
  assert.equal(response.body.ok, true);
  assert.equal(response.body.total_hits, 1);
  assert.equal(response.body.hits[0].plan_id, 'plan-review');
  assert.deepEqual(response.body.hits[0].reviewer_ids, ['alice']);
});
