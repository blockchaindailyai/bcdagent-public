const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

if (typeof global.atob !== 'function') {
  global.atob = (input) => Buffer.from(input, 'base64').toString('binary');
}

function toBase64Url(input) {
  return Buffer.from(input)
    .toString('base64')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/g, '');
}

function signHs256Jwt(payload, secret) {
  const header = { alg: 'HS256', typ: 'JWT' };
  const headerB64 = toBase64Url(JSON.stringify(header));
  const payloadB64 = toBase64Url(JSON.stringify(payload));
  const data = `${headerB64}.${payloadB64}`;

  const sig = crypto
    .createHmac('sha256', secret)
    .update(data)
    .digest('base64')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/g, '');

  return `${data}.${sig}`;
}

async function loadWorker() {
  const workerPath = path.join(__dirname, '..', 'worker.js');
  const source = readFileSync(workerPath, 'utf8');
  const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
  return import(moduleUrl);
}

function makeEnv(feedItems) {
  return {
    JWT_SECRET: 'test-secret-32-bytes-minimum-value',
    INTEL_KV: {
      async get(key) {
        if (key !== 'feed') return null;
        return JSON.stringify(feedItems);
      },
    },
  };
}

test('GET /api/intel rejects requests without bearer token', async () => {
  const worker = (await loadWorker()).default;
  const req = new Request('https://example.com/api/intel', { method: 'GET' });

  const res = await worker.fetch(req, makeEnv([{ id: 'a' }]));
  assert.equal(res.status, 401);

  const json = await res.json();
  assert.equal(json.error, 'Missing token');
});

test('GET /api/intel returns feed for valid JWT', async () => {
  const worker = (await loadWorker()).default;
  const nowSec = Math.floor(Date.now() / 1000);
  const token = signHs256Jwt(
    {
      addr: 'TTestWalletAddr11111111111111111',
      untilMs: Date.now() + 60_000,
      exp: nowSec + 60,
    },
    'test-secret-32-bytes-minimum-value'
  );

  const req = new Request('https://example.com/api/intel', {
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
  });

  const expectedItems = [{ id: 'one', title: 'Intel Item' }];
  const res = await worker.fetch(req, makeEnv(expectedItems));

  assert.equal(res.status, 200);
  const body = await res.json();
  assert.deepEqual(body, {
    items: [
      {
        id: 'one',
        title: 'Intel Item',
        headline: 'Intel Item',
        body: '',
      },
    ],
  });
});

test('GET /api/intel/source/:source returns only matching source items', async () => {
  const worker = (await loadWorker()).default;
  const nowSec = Math.floor(Date.now() / 1000);
  const token = signHs256Jwt(
    {
      addr: 'TTestWalletAddr11111111111111111',
      untilMs: Date.now() + 60_000,
      exp: nowSec + 60,
    },
    'test-secret-32-bytes-minimum-value'
  );

  const req = new Request('https://example.com/api/intel/source/polymarket', {
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
  });

  const feed = [
    { id: 'a', source: 'polymarket', title: 'PM 1' },
    { id: 'b', source: 'hyperliquid', title: 'HL 1' },
    { id: 'c', source: 'POLYMARKET', title: 'PM 2' },
  ];

  const res = await worker.fetch(req, makeEnv(feed));
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.deepEqual(body, {
    items: [
      { id: 'a', source: 'polymarket', title: 'PM 1', headline: 'PM 1', body: '' },
      { id: 'c', source: 'POLYMARKET', title: 'PM 2', headline: 'PM 2', body: '' },
    ],
  });
});

test('GET /api/intel/source/:source supports comma-separated source filters', async () => {
  const worker = (await loadWorker()).default;
  const nowSec = Math.floor(Date.now() / 1000);
  const token = signHs256Jwt(
    {
      addr: 'TTestWalletAddr11111111111111111',
      untilMs: Date.now() + 60_000,
      exp: nowSec + 60,
    },
    'test-secret-32-bytes-minimum-value'
  );

  const req = new Request('https://example.com/api/intel/source/polymarket,hyperliquid', {
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
  });

  const feed = [
    { id: 'a', source: 'polymarket', title: 'PM 1' },
    { id: 'b', source: 'hyperliquid', title: 'HL 1' },
    { id: 'c', source: 'rss', title: 'RSS 1' },
  ];

  const res = await worker.fetch(req, makeEnv(feed));
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.deepEqual(body, {
    items: [
      { id: 'a', source: 'polymarket', title: 'PM 1', headline: 'PM 1', body: '' },
      { id: 'b', source: 'hyperliquid', title: 'HL 1', headline: 'HL 1', body: '' },
    ],
  });
});

test('GET /api/intel rejects JWTs that omit exp', async () => {
  const worker = (await loadWorker()).default;
  const token = signHs256Jwt(
    {
      addr: 'TTestWalletAddr11111111111111111',
      untilMs: Date.now() + 60_000,
    },
    'test-secret-32-bytes-minimum-value'
  );

  const req = new Request('https://example.com/api/intel', {
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
  });

  const res = await worker.fetch(req, makeEnv([{ id: 'a' }]));
  assert.equal(res.status, 401);

  const json = await res.json();
  assert.equal(json.error, 'Invalid/expired token');
});


test('OPTIONS preflight is rejected for disallowed origin', async () => {
  const worker = (await loadWorker()).default;
  const req = new Request('https://example.com/api/intel', {
    method: 'OPTIONS',
    headers: { Origin: 'https://evil.example' },
  });

  const res = await worker.fetch(req, makeEnv([{ id: 'a' }]));
  assert.equal(res.status, 403);
});

test('GET /api/intel is rejected for disallowed origin even with valid JWT', async () => {
  const worker = (await loadWorker()).default;
  const nowSec = Math.floor(Date.now() / 1000);
  const token = signHs256Jwt(
    {
      addr: 'TTestWalletAddr11111111111111111',
      untilMs: Date.now() + 60_000,
      exp: nowSec + 60,
    },
    'test-secret-32-bytes-minimum-value'
  );

  const req = new Request('https://example.com/api/intel', {
    method: 'GET',
    headers: {
      Origin: 'https://evil.example',
      Authorization: `Bearer ${token}`,
    },
  });

  const res = await worker.fetch(req, makeEnv([{ id: 'a' }]));
  assert.equal(res.status, 403);
  const body = await res.json();
  assert.equal(body.error, 'CORS blocked: origin not allowed');
});
