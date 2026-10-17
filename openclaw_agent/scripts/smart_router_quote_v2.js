#!/usr/bin/env node
const { execFile } = require('child_process');
const tronwebModule = require('tronweb');
const TronWeb = tronwebModule.TronWeb || tronwebModule.default || tronwebModule;

function arg(name, def = '') {
  const prefix = `--${name}=`;
  const hit = process.argv.find((x) => x.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : def;
}

function parseCsv(v) {
  if (!v) return [];
  return v.split(',').map((x) => x.trim()).filter((x) => x.length > 0);
}

function ensureAddress(tronWeb, value, fieldName) {
  const candidate = String(value || '').trim();
  if (!candidate) throw new Error(`missing_${fieldName}`);
  if (!tronWeb.isAddress(candidate)) throw new Error(`invalid_${fieldName}:${candidate}`);
  return candidate;
}

function parseBigInt(value, fieldName) {
  const normalized = String(value || '').trim();
  if (!/^\d+$/.test(normalized)) throw new Error(`invalid_${fieldName}:${value}`);
  return BigInt(normalized);
}

function computeVersionLen(tokens, poolVersionsPerHop) {
  if (!Array.isArray(tokens) || tokens.length < 2) throw new Error('route_tokens_invalid');
  if (!Array.isArray(poolVersionsPerHop) || poolVersionsPerHop.length !== tokens.length - 1) {
    throw new Error('route_poolVersions_length_mismatch');
  }

  const groupedVersions = [];
  const runLengths = [];
  let current = '';
  let currentHopCount = 0;

  for (const version of poolVersionsPerHop) {
    const normalized = String(version || '').trim();
    if (!normalized) throw new Error('route_poolVersion_empty');
    if (!current) {
      current = normalized;
      currentHopCount = 1;
      continue;
    }
    if (normalized === current) {
      currentHopCount += 1;
      continue;
    }
    groupedVersions.push(current);
    runLengths.push(currentHopCount);
    current = normalized;
    currentHopCount = 1;
  }

  groupedVersions.push(current);
  runLengths.push(currentHopCount);

  const versionLen = runLengths.map((hopCount, idx) => (idx === 0 ? hopCount + 1 : hopCount));
  const sum = versionLen.reduce((a, b) => a + b, 0);
  if (sum !== tokens.length) throw new Error(`versionLen_sum_must_equal_path_length:${sum}:${tokens.length}`);

  return { poolVersion: groupedVersions, versionLen };
}

function httpGetJson(url) {
  return new Promise((resolve, reject) => {
    execFile('curl', ['-sS', '--fail', url], { maxBuffer: 5 * 1024 * 1024 }, (err, stdout, stderr) => {
      if (err) {
        reject(new Error(`calc_service_curl_failed:${stderr || err.message}`));
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch (_parseErr) {
        reject(new Error(`calc_service_invalid_json:${String(stdout).slice(0, 300)}`));
      }
    });
  });
}

(async () => {
  const tronWeb = new TronWeb({ fullHost: arg('full-host', process.env.TRON_FULL_HOST || 'https://api.nileex.io') });

  const calcServiceUrl = arg('calc-service-url', 'https://router.example.invalid/swap/router');
  const fromToken = ensureAddress(tronWeb, arg('from-token'), 'from_token');
  const toToken = ensureAddress(tronWeb, arg('to-token'), 'to_token');
  const amountIn = parseBigInt(arg('amount-in', '0'), 'amount_in');
  const typeList = parseCsv(arg('type-list', 'PSM,CURVE,CURVE_COMBINATION,WTRX,SUNSWAP_V1,SUNSWAP_V2,SUNSWAP_V3')).join(',');
  const routeIndex = Number(arg('route-index', '0'));

  if (amountIn <= 0n) throw new Error('amount_in_must_be_positive');
  if (!Number.isInteger(routeIndex) || routeIndex < 0) throw new Error('route_index_invalid');

  const query = new URLSearchParams({
    fromToken,
    toToken,
    amountIn: amountIn.toString(),
    typeList,
  });

  const url = `${calcServiceUrl}?${query.toString()}`;
  const payload = await httpGetJson(url);
  if (payload?.code !== 0) throw new Error(`calc_service_error:${payload?.code}:${payload?.message || 'unknown'}`);
  const routes = Array.isArray(payload?.data) ? payload.data : [];
  if (routes.length === 0) throw new Error('calc_service_empty_routes');
  if (routeIndex >= routes.length) throw new Error(`route_index_out_of_range:${routeIndex}:${routes.length}`);

  const selected = routes[routeIndex] || {};
  const tokens = (Array.isArray(selected.tokens) ? selected.tokens : []).map((x, idx) => ensureAddress(tronWeb, x, `route_token_${idx}`));
  const poolFees = Array.isArray(selected.poolFees) ? selected.poolFees.map((x) => Number(String(x).trim())) : [];
  const poolVersionsPerHop = Array.isArray(selected.poolVersions) ? selected.poolVersions.map((x) => String(x).trim()) : [];

  if (tokens.length < 2) throw new Error('route_tokens_invalid');
  if (poolFees.length !== tokens.length) throw new Error(`route_poolFees_length_mismatch:${poolFees.length}:${tokens.length}`);
  if (poolFees[poolFees.length - 1] !== 0) throw new Error('route_poolFees_last_must_be_zero');

  const { poolVersion, versionLen } = computeVersionLen(tokens, poolVersionsPerHop);

  console.log(JSON.stringify({
    status: 'ok',
    source: 'sun_io_calculation_service',
    calculationService: calcServiceUrl,
    request: {
      fromToken,
      toToken,
      amountIn: amountIn.toString(),
      typeList,
      routeIndex,
    },
    selectedRoute: {
      amountIn: String(selected.amountIn || ''),
      amountOut: String(selected.amountOut || ''),
      inUsd: String(selected.inUsd || ''),
      outUsd: String(selected.outUsd || ''),
      impact: String(selected.impact || ''),
      fee: String(selected.fee || ''),
      tokens,
      symbols: Array.isArray(selected.symbols) ? selected.symbols : [],
      poolFees,
      poolVersions: poolVersionsPerHop,
      stepAmountsOut: Array.isArray(selected.stepAmountsOut) ? selected.stepAmountsOut : [],
    },
    smartRouterRoute: {
      path: tokens,
      poolVersion,
      versionLen,
      fees: poolFees,
    },
    topRoutes: routes.slice(0, 3).map((route) => ({
      amountOut: String(route?.amountOut || ''),
      impact: String(route?.impact || ''),
      tokens: Array.isArray(route?.tokens) ? route.tokens : [],
      poolVersions: Array.isArray(route?.poolVersions) ? route.poolVersions : [],
      poolFees: Array.isArray(route?.poolFees) ? route.poolFees : [],
    })),
  }));
})().catch((err) => {
  console.error(JSON.stringify({
    status: 'error',
    error: err?.message || String(err),
  }, null, 2));
  process.exit(1);
});
