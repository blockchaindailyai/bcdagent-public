#!/usr/bin/env node
const { execFile } = require('child_process');
const tronwebModule = require('tronweb');
const TronWeb = tronwebModule.TronWeb || tronwebModule.default || tronwebModule;
const ethers = require('ethers');

const SMART_ROUTER_ABI = [
  {
    "inputs": [
      {"internalType": "address[]", "name": "path", "type": "address[]"},
      {"internalType": "string[]", "name": "poolVersion", "type": "string[]"},
      {"internalType": "uint256[]", "name": "versionLen", "type": "uint256[]"},
      {"internalType": "uint24[]", "name": "fees", "type": "uint24[]"},
      {
        "components": [
          {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
          {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
          {"internalType": "address", "name": "to", "type": "address"},
          {"internalType": "uint256", "name": "deadline", "type": "uint256"}
        ],
        "internalType": "struct SmartExchangeRouter.SwapData",
        "name": "data",
        "type": "tuple"
      }
    ],
    "name": "swapExactInput",
    "outputs": [{"internalType": "uint256[]", "name": "amountsOut", "type": "uint256[]"}],
    "stateMutability": "payable",
    "type": "function"
  }
];

const SMART_ROUTER_QUOTE_ABI = SMART_ROUTER_ABI.map((item) => ({
  ...item,
  stateMutability: item?.name === 'swapExactInput' ? 'view' : item.stateMutability,
}));

const SMART_ROUTER_READ_ABI = [
  {"constant": true, "inputs": [], "name": "v2Router", "outputs": [{"name": "", "type": "address"}], "type": "function"}
];

const V2_ROUTER_QUOTE_ABI = [
  {
    "constant": true,
    "inputs": [
      {"name": "amountIn", "type": "uint256"},
      {"name": "path", "type": "address[]"}
    ],
    "name": "getAmountsOut",
    "outputs": [{"name": "amounts", "type": "uint256[]"}],
    "type": "function"
  }
];

const TRC20_ABI = [
  {"constant": true, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
  {"constant": true, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"},
  {"constant": false, "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"}
];

function arg(name, def = '') {
  const prefix = `--${name}=`;
  const hit = process.argv.find(x => x.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : def;
}

function parseCsv(v, cast = x => x) {
  if (!v) return [];
  return v
    .split(',')
    .map(x => x.trim())
    .filter(x => x.length > 0)
    .map(cast);
}

function encodeSwapExactInputCalldata(routePath, poolVersion, versionLen, fees, swapData) {
  const toEvmHex = (addr) => {
    const tronHex = TronWeb.address.toHex(addr);
    if (!tronHex || tronHex.length !== 42 || !tronHex.startsWith('41')) {
      throw new Error(`invalid_tron_address_for_encoding:${addr}`);
    }
    return `0x${tronHex.slice(2)}`;
  };
  const encodedPath = routePath.map(toEvmHex);
  const encodedSwapData = [
    swapData[0],
    swapData[1],
    toEvmHex(swapData[2]),
    swapData[3],
  ];
  const iface = new ethers.Interface([
    'function swapExactInput(address[] path,string[] poolVersion,uint256[] versionLen,uint24[] fees,(uint256 amountIn,uint256 amountOutMin,address to,uint256 deadline) data) payable returns (uint256[] amountsOut)',
  ]);
  return iface.encodeFunctionData('swapExactInput', [encodedPath, poolVersion, versionLen.map(String), fees, encodedSwapData]);
}

function normalizeLower(value) {
  if (value === null || value === undefined) return '';
  return String(value).trim().toLowerCase();
}

function isTruthyFlag(value) {
  return ['true', '1', 'yes', 'y', 'on'].includes(normalizeLower(value));
}

function ensureAddress(tronWeb, value, fieldName) {
  const candidate = String(value || '').trim();
  if (!candidate) throw new Error(`missing_${fieldName}`);
  if (!tronWeb.isAddress(candidate)) {
    throw new Error(`invalid_${fieldName}:${candidate}`);
  }
  return candidate;
}


function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function decodeHexReason(value) {
  if (!value) return '';
  try {
    const clean = String(value).replace(/^0x/, '');
    if (!clean) return '';
    return Buffer.from(clean, 'hex').toString('utf8').replace(/\u0000/g, '').trim();
  } catch (_err) {
    return '';
  }
}

function extractReceiptFailureReason(info) {
  if (!info || typeof info !== 'object') return 'unknown_revert';
  const receiptResult = String(info?.receipt?.result || '').trim();
  const contractRet = String(info?.result || '').trim();
  const decodedResMessage = decodeHexReason(info?.resMessage);
  let decodedContractResult = '';
  if (Array.isArray(info?.contractResult) && info.contractResult.length > 0) {
    decodedContractResult = decodeHexReason(info.contractResult[0]);
  }
  for (const candidate of [decodedResMessage, decodedContractResult, contractRet, receiptResult]) {
    if (candidate && candidate.toUpperCase() !== 'FAILED') {
      return candidate;
    }
  }
  return receiptResult || contractRet || 'unknown_revert';
}

async function waitForReceipt(tronWeb, txid, timeoutS) {
  const timeoutMs = Math.max(1, Number(timeoutS || 90)) * 1000;
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const info = await tronWeb.trx.getTransactionInfo(txid);
    if (info && Object.keys(info).length > 0) {
      return info;
    }
    await sleep(1500);
  }
  throw new Error(`receipt_timeout:${timeoutS}`);
}

function validateRoute(path, poolVersion, versionLen, fees) {
  if (path.length < 2) throw new Error('path_length_must_be_at_least_2');
  if (poolVersion.length !== versionLen.length) throw new Error('poolVersion_versionLen_length_mismatch');
  const total = versionLen.reduce((a, b) => a + BigInt(b), 0n);
  if (total !== BigInt(path.length)) throw new Error('versionLen_sum_must_equal_path_length');
  if (fees.length !== path.length) throw new Error('fees_length_must_equal_path_length');
  if (fees[fees.length - 1] !== 0) throw new Error('fees_last_must_be_zero');
}

function toBigIntLike(value) {
  if (typeof value === 'bigint') return value;
  if (typeof value === 'number') return BigInt(Math.trunc(value));
  if (typeof value === 'string') {
    const normalized = value.trim();
    if (normalized.includes(',')) {
      const tupleParts = normalized.split(',').map((part) => part.trim()).filter(Boolean);
      if (tupleParts.length > 0) return toBigIntLike(tupleParts[tupleParts.length - 1]);
    }
    return BigInt(normalized);
  }
  if (value && typeof value === 'object') {
    if (typeof value.toString === 'function') return toBigIntLike(value.toString());
  }
  throw new Error(`cannot_parse_bigint:${String(value)}`);
}

function computeAmountOutMinFromQuote(quotedOut, slippageBps) {
  const bps = BigInt(slippageBps);
  const scaled = quotedOut * (10000n - bps);
  const outMin = scaled / 10000n;
  if (quotedOut < 0n || outMin < 0n) throw new Error('quoted_amount_invalid');
  return outMin;
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

function parseDisplayAmountToRaw(value, decimals) {
  const normalized = String(value || '').trim();
  if (!/^\d+(\.\d+)?$/.test(normalized)) throw new Error(`invalid_display_amount:${value}`);
  const [wholeRaw, fracRaw = ''] = normalized.split('.');
  const whole = BigInt(wholeRaw || '0');
  const decimalsNum = Number(decimals);
  if (!Number.isInteger(decimalsNum) || decimalsNum < 0) throw new Error(`invalid_decimals:${decimals}`);
  const scale = 10n ** BigInt(decimalsNum);
  const fracPadded = (fracRaw + '0'.repeat(decimalsNum)).slice(0, decimalsNum);
  const frac = fracPadded ? BigInt(fracPadded) : 0n;
  return whole * scale + frac;
}

async function quoteViaCalculationService(params) {
  const {
    tronWeb,
    fromToken,
    toToken,
    amountIn,
    calcServiceUrl,
    typeList,
    routePath,
    tokenOutContract,
  } = params;

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

  const normalizedPath = routePath.map((x) => String(x).trim());
  const matchedRoute = routes.find((route) => {
    const tokens = Array.isArray(route?.tokens) ? route.tokens.map((t) => String(t).trim()) : [];
    if (tokens.length !== normalizedPath.length) return false;
    return tokens.every((token, idx) => token === normalizedPath[idx]);
  }) || routes[0];

  const outDisplay = String(matchedRoute?.amountOut || '').trim();
  if (!outDisplay) throw new Error('calc_service_missing_amountOut');
  const outDecimals = Number((await tokenOutContract.decimals().call()).toString());
  const quotedOutRaw = parseDisplayAmountToRaw(outDisplay, outDecimals);

  return {
    quotedOutRaw,
    calcServiceUrl,
    selectedRoute: {
      amountOut: outDisplay,
      impact: String(matchedRoute?.impact || ''),
      tokens: Array.isArray(matchedRoute?.tokens) ? matchedRoute.tokens : [],
      poolVersions: Array.isArray(matchedRoute?.poolVersions) ? matchedRoute.poolVersions : [],
      poolFees: Array.isArray(matchedRoute?.poolFees) ? matchedRoute.poolFees : [],
    },
  };
}

function normalizeQuoteAmounts(value) {
  if (Array.isArray(value)) return value;
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
      return trimmed
        .slice(1, -1)
        .split(',')
        .map((entry) => entry.trim())
        .filter((entry) => entry.length > 0);
    }
    if (trimmed.includes(',')) {
      return trimmed
        .split(',')
        .map((entry) => entry.trim())
        .filter((entry) => entry.length > 0);
    }
    return [];
  }
  if (!value || typeof value !== 'object') return [];

  if (Array.isArray(value.amountsOut)) return value.amountsOut;
  if (Array.isArray(value._amountsOut)) return value._amountsOut;
  if (Array.isArray(value.amounts)) return value.amounts;
  if (Array.isArray(value._amounts)) return value._amounts;

  // TronWeb may return nested tuple-like objects, where the first numeric
  // field itself contains the dynamic uint[] return payload.
  if (value[0] !== undefined && value[0] !== null && !Array.isArray(value[0])) {
    const nestedZero = normalizeQuoteAmounts(value[0]);
    if (nestedZero.length > 0) return nestedZero;
  }

  const numericKeys = Object.keys(value)
    .filter((k) => /^\d+$/.test(k))
    .sort((a, b) => Number(a) - Number(b));
  if (numericKeys.length > 0) {
    const numericValues = numericKeys.map((k) => value[k]);
    if (numericValues.length === 1 && typeof numericValues[0] === 'object' && numericValues[0] !== null) {
      const nestedSingle = normalizeQuoteAmounts(numericValues[0]);
      if (nestedSingle.length > 0) return nestedSingle;
    }
    return numericValues;
  }

  for (const nestedValue of Object.values(value)) {
    const nested = normalizeQuoteAmounts(nestedValue);
    if (nested.length > 0) return nested;
  }

  return [];
}

function tryDecodeAbiUintArray(tronWeb, value) {
  if (typeof value !== 'string' || !value.trim()) return [];
  const encoded = value.startsWith('0x') ? value : `0x${value}`;
  try {
    const decoded = tronWeb.utils.abi.decodeParams(['uint256[]'], encoded);
    const candidate = decoded?.[0] ?? decoded?.amountsOut ?? decoded?.amounts;
    return normalizeQuoteAmounts(candidate);
  } catch (_err) {
    return [];
  }
}

function extractQuoteAmounts(tronWeb, rawValue) {
  let normalized = normalizeQuoteAmounts(rawValue);
  if (normalized.length > 0) return normalized;

  if (typeof rawValue === 'string' && rawValue.trim()) {
    normalized = tryDecodeAbiUintArray(tronWeb, rawValue);
    if (normalized.length > 0) return normalized;
  }

  if (rawValue && typeof rawValue === 'object') {
    const fields = [rawValue.constant_result, rawValue.constantResult, rawValue.result, rawValue._result];
    for (const field of fields) {
      if (Array.isArray(field) && field.length > 0) {
        for (const entry of field) {
          if (typeof entry === 'string' && entry.trim()) {
            normalized = tryDecodeAbiUintArray(tronWeb, entry);
            if (normalized.length > 0) return normalized;
          }
        }
      }
    }

    const hexLike = [rawValue.hex, rawValue._hex, rawValue.data].filter((x) => typeof x === 'string');
    for (const entry of hexLike) {
      normalized = tryDecodeAbiUintArray(tronWeb, entry);
      if (normalized.length > 0) return normalized;
    }
  }

  return [];
}

function selectQuotedAmountOut(amounts, expectedPathLength) {
  if (!Array.isArray(amounts) || amounts.length === 0) {
    throw new Error('quote_failed_invalid_shape');
  }

  // TronWeb can sometimes return a tuple-like string value such as
  // "1000000,48188" instead of an actual uint[] array.
  if (amounts.length === 1 && typeof amounts[0] === 'string' && amounts[0].includes(',')) {
    const split = amounts[0]
      .split(',')
      .map((entry) => entry.trim())
      .filter((entry) => entry.length > 0);
    if (split.length > 0) {
      amounts = split;
    }
  }

  // Preferred shape for swapExactInput/getAmountsOut is one amount per path token.
  // Some TronWeb responses can flatten/reshape this payload. If we still have at
  // least one decoded amount, use the terminal value as quoteOut.
  if (amounts.length < expectedPathLength) {
    return toBigIntLike(amounts[amounts.length - 1]);
  }

  return toBigIntLike(amounts[expectedPathLength - 1]);
}

function isSimpleV2Route(path, poolVersion, versionLen) {
  return (
    path.length >= 2 &&
    poolVersion.length === 1 &&
    normalizeLower(poolVersion[0]) === 'v2' &&
    versionLen.length === 1 &&
    BigInt(versionLen[0]) === BigInt(path.length)
  );
}

const debugContext = {
    stage: 'init',
    fullHost: '',
    owner: '',
    smartRouter: '',
    route: null,
    swapData: null,
    amountIn: '',
    amountOutMinCli: '',
    amountOutMinEffective: '',
    quote: null,
    txid: '',
    approveTxId: '',
    waitForReceiptEnabled: false,
    receiptTimeoutS: 0,
    feeLimit: 0,
    receipt: null,
};

(async () => {
  const prepareOnly = isTruthyFlag(arg('prepare-only', 'false'));
  const privateKey = process.env.NILE_PRIVATE_KEY || process.env.PRIVATE_KEY || '';
  if (!prepareOnly && !privateKey) throw new Error('missing_private_key_env');

  const fullHost = arg('full-host', process.env.TRON_FULL_HOST || 'https://api.nileex.io');
  const smartRouter = arg('smart-router');
  const tokenIn = arg('token-in');
  const tokenOut = arg('token-out');
  const amountIn = BigInt(arg('amount-in', '0'));
  const amountOutMinCli = BigInt(arg('amount-out-min', '1'));
  const to = arg('to') || arg('to-address');
  const deadline = BigInt(arg('deadline', String(Math.floor(Date.now() / 1000) + 1200)));
  const poolVersion = parseCsv(arg('pool-version', 'v2'), normalizeLower);
  const versionLen = parseCsv(arg('version-len', '2'), x => BigInt(x));
  const fees = parseCsv(arg('fees', '0,0'), x => Number(x));
  const path = parseCsv(arg('path', `${tokenIn},${tokenOut}`));
  const feeLimit = Number(arg('fee-limit', '500000000'));
  const ownerAddressArg = arg('owner-address', '');
  const autoApprove = isTruthyFlag(arg('auto-approve', 'false'));
  const waitForReceiptEnabled = isTruthyFlag(arg('wait-for-receipt', 'false'));
  const receiptTimeoutS = Number(arg('receipt-timeout-s', '90'));
  const autoAmountOutMin = isTruthyFlag(arg('auto-amount-out-min', 'false'));
  const quoteSlippageBps = Number(arg('quote-slippage-bps', '100'));
  const calcServiceUrl = arg('calc-service-url', process.env.SUN_IO_CALC_SERVICE_URL || 'https://router.example.invalid/swap/router');
  const calcTypeList = arg('calc-type-list', 'PSM,CURVE,CURVE_COMBINATION,WTRX,SUNSWAP_V1,SUNSWAP_V2,SUNSWAP_V3');

  debugContext.fullHost = fullHost;
  debugContext.smartRouter = smartRouter;
  debugContext.amountIn = amountIn.toString();
  debugContext.amountOutMinCli = amountOutMinCli.toString();
  debugContext.waitForReceiptEnabled = waitForReceiptEnabled;
  debugContext.receiptTimeoutS = receiptTimeoutS;
  debugContext.feeLimit = feeLimit;

  if (!smartRouter || !tokenIn || !tokenOut || !to || !String(to).trim()) {
    throw new Error('missing_required_args');
  }
  if (amountIn <= 0n) throw new Error('amount_in_must_be_positive');
  if (!Number.isInteger(quoteSlippageBps) || quoteSlippageBps < 0 || quoteSlippageBps >= 10000) {
    throw new Error('quote_slippage_bps_out_of_range');
  }

  validateRoute(path, poolVersion, versionLen, fees);

  const tronWeb = privateKey ? new TronWeb({ fullHost, privateKey }) : new TronWeb({ fullHost });
  const owner = privateKey ? tronWeb.address.fromPrivateKey(privateKey) : ensureAddress(tronWeb, ownerAddressArg, 'owner_address');
  ensureAddress(tronWeb, owner, 'owner');
  const smartRouterAddr = ensureAddress(tronWeb, smartRouter, 'smart_router');
  const tokenInAddr = ensureAddress(tronWeb, tokenIn, 'token_in');
  const tokenOutAddr = ensureAddress(tronWeb, tokenOut, 'token_out');
  const toAddr = ensureAddress(tronWeb, to, 'to');
  debugContext.owner = owner;

  const trc20 = await tronWeb.contract(TRC20_ABI, tokenInAddr);
  const tokenOutContract = await tronWeb.contract(TRC20_ABI, tokenOutAddr);
  let currentAllowance = 0n;
  if (!prepareOnly || autoApprove) {
    currentAllowance = BigInt((await trc20.allowance(owner, smartRouterAddr).call()).toString());
    debugContext.stage = 'allowance_checked';
  }

  let approveTxId = '';
  if (!prepareOnly && autoApprove && currentAllowance < amountIn) {
    debugContext.stage = 'broadcasting_approve';
    const approveAmount = amountIn.toString();
    approveTxId = await trc20.approve(smartRouterAddr, approveAmount).send({ feeLimit });
    debugContext.approveTxId = approveTxId;
  }

  const router = await tronWeb.contract(SMART_ROUTER_ABI, smartRouterAddr);
  const routePath = path.map((entry) => ensureAddress(tronWeb, entry, 'path_token'));
  debugContext.route = { path: routePath, poolVersion, versionLen: versionLen.map(String), fees };
  let amountOutMinEffective = amountOutMinCli;
  let quotedAmountOut = '';
  let quoteContext = null;
  let quoteFallbackReason = '';
  if (autoAmountOutMin) {
    debugContext.stage = 'quoting_amount_out';
    const routerRead = await tronWeb.contract(SMART_ROUTER_READ_ABI, smartRouterAddr);
    quoteContext = {
      source: 'sun_io_calculation_service',
      calculationService: calcServiceUrl,
      route: {
        path: routePath,
        poolVersion,
        versionLen: versionLen.map(String),
        fees,
      },
      request: { fromToken: tokenInAddr, toToken: tokenOutAddr, amountIn: amountIn.toString(), typeList: calcTypeList },
    };
    debugContext.quote = { ...quoteContext };
    try {
      const serviceQuote = await quoteViaCalculationService({
        tronWeb,
        fromToken: tokenInAddr,
        toToken: tokenOutAddr,
        amountIn,
        calcServiceUrl,
        typeList: calcTypeList,
        routePath,
        tokenOutContract,
      });
      quotedAmountOut = serviceQuote.quotedOutRaw.toString();
      amountOutMinEffective = computeAmountOutMinFromQuote(BigInt(quotedAmountOut), quoteSlippageBps);
      debugContext.quote = {
        ...quoteContext,
        selectedRoute: serviceQuote.selectedRoute,
        quotedAmountOut,
        slippageBps: quoteSlippageBps,
      };
    } catch (quoteErr) {
      try {
        // For quote-only calls, never enforce a non-zero min output.
        const quoteSwapData = [amountIn.toString(), '0', toAddr, Number(deadline)];
        let quoteAmounts;
        if (isSimpleV2Route(routePath, poolVersion, versionLen)) {
          const v2RouterAddr = await routerRead.v2Router().call();
          const v2Router = await tronWeb.contract(V2_ROUTER_QUOTE_ABI, v2RouterAddr);
          quoteContext.source = 'v2_router_getAmountsOut';
          quoteContext.contract = v2RouterAddr;
          quoteContext.method = 'getAmountsOut';
          quoteContext.fallbackFrom = `sun_io_calculation_service_error:${quoteErr?.message || String(quoteErr)}`;
          quoteAmounts = await v2Router.getAmountsOut(amountIn.toString(), routePath).call();
        } else {
          const quoteRouter = await tronWeb.contract(SMART_ROUTER_QUOTE_ABI, smartRouterAddr);
          quoteContext.source = 'smart_router_constant_call';
          quoteContext.contract = smartRouterAddr;
          quoteContext.method = 'swapExactInput';
          quoteContext.fallbackFrom = `sun_io_calculation_service_error:${quoteErr?.message || String(quoteErr)}`;
          quoteAmounts = await quoteRouter.swapExactInput(routePath, poolVersion, versionLen.map(String), fees, quoteSwapData).call({ from: owner });
        }
        const normalized = extractQuoteAmounts(tronWeb, quoteAmounts);
        quotedAmountOut = selectQuotedAmountOut(normalized, routePath.length).toString();
        amountOutMinEffective = computeAmountOutMinFromQuote(BigInt(quotedAmountOut), quoteSlippageBps);
        debugContext.quote = {
          ...quoteContext,
          quotedAmountOut,
          slippageBps: quoteSlippageBps,
        };
      } catch (fallbackQuoteErr) {
        const quoteMessage = fallbackQuoteErr?.message || String(fallbackQuoteErr);
        quoteFallbackReason = quoteMessage;
        amountOutMinEffective = amountOutMinCli;
        debugContext.quote = {
          ...quoteContext,
          error: quoteMessage,
          fallback: 'cli_amount_out_min',
        };
      }
    }
  }

  const swapData = [amountIn.toString(), amountOutMinEffective.toString(), toAddr, Number(deadline)];
  debugContext.swapData = {
    amountIn: amountIn.toString(),
    amountOutMin: amountOutMinEffective.toString(),
    to: toAddr,
    deadline: deadline.toString(),
  };
  debugContext.amountOutMinEffective = amountOutMinEffective.toString();
  const callData = encodeSwapExactInputCalldata(routePath, poolVersion, versionLen, fees, swapData);
  const out = {
    status: prepareOnly ? 'prepared' : 'submitted',
    owner,
    txid: '',
    approveTxId,
    route: { path: routePath, poolVersion, versionLen: versionLen.map(String), fees },
    quote: autoAmountOutMin ? {
      enabled: true,
      context: quoteContext,
      quotedAmountOut,
      slippageBps: quoteSlippageBps,
      fallbackUsed: !!quoteFallbackReason,
      fallbackReason: quoteFallbackReason,
    } : {
      enabled: false,
    },
    swapData: {
      amountIn: amountIn.toString(),
      amountOutMin: amountOutMinEffective.toString(),
      amountOutMinSource: autoAmountOutMin ? (quoteFallbackReason ? 'cli_quote_failed' : 'quoted_with_slippage') : 'cli',
      to: toAddr,
      deadline: deadline.toString(),
    },
    strategyPayload: {
      target: smartRouterAddr,
      selector: '0xcef95229',
      callData,
      amountIn: amountIn.toString(),
    },
  };

  if (prepareOnly) {
    debugContext.stage = 'prepared';
    console.log(JSON.stringify(out));
    return;
  }

  debugContext.stage = 'broadcasting_swap';
  const txid = await router.swapExactInput(routePath, poolVersion, versionLen.map(String), fees, swapData).send({
    feeLimit,
    callValue: 0,
  });

  debugContext.txid = txid;
  debugContext.stage = 'swap_submitted';
  out.txid = txid;

  if (waitForReceiptEnabled) {
    debugContext.stage = 'waiting_for_receipt';
    const receipt = await waitForReceipt(tronWeb, txid, receiptTimeoutS);
    debugContext.receipt = receipt;
    const receiptResult = String(receipt?.receipt?.result || '').trim();
    out.receipt = receipt;
    if (receiptResult === 'SUCCESS') {
      out.status = 'confirmed';
      debugContext.stage = 'receipt_confirmed';
    } else {
      out.status = 'failed';
      debugContext.stage = 'receipt_failed';
      out.failureReason = extractReceiptFailureReason(receipt);
      throw new Error(`swap_reverted:${out.failureReason}`);
    }
  }

  debugContext.stage = 'done';
  console.log(JSON.stringify(out));
})().catch((err) => {
  console.error(JSON.stringify({
    status: 'error',
    error: err?.message || String(err),
    context: typeof debugContext === 'object' ? debugContext : undefined,
  }, null, 2));
  process.exit(1);
});
