from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from scraper_bot.core.config import get_settings
from scraper_bot.schema.canonical import CanonicalItem
from scraper_bot.sources.base import SourceScraper
from scraper_bot.sources.price_backoff import (
    activate_price_lookup_backoff,
    is_price_lookup_backoff_active,
    register_price_lookup_backoff_failure,
    register_price_lookup_backoff_success,
)
from scraper_bot.sources.token_price import fetch_token_prices_from_dexscreener

log = logging.getLogger(__name__)

WEI_PER_BNB = 10**18
ERC20_TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ETH_GET_LOGS_LIMIT_ERROR_CODE = -32005

# BNB Chain tracked BEP-20 tokens for major movement alerts.
TRACKED_BEP20_TOKENS = {
    "0x55d398326f99059ff775485246999027b3197955": {
        "symbol": "USDT",
        "decimals": 18,
        "class": "stablecoin",
        "usd_fallback": 1.0,
    },
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": {
        "symbol": "USDC",
        "decimals": 18,
        "class": "stablecoin",
        "usd_fallback": 1.0,
    },
    "0xe9e7cea3dedca5984780bafc599bd69add087d56": {
        "symbol": "BUSD",
        "decimals": 18,
        "class": "stablecoin",
        "usd_fallback": 1.0,
    },
    "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82": {
        "symbol": "CAKE",
        "decimals": 18,
        "class": "defi",
        "usd_fallback": 3.0,
    },
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _from_hex_int(v: str | None) -> int | None:
    if not v:
        return None
    if not isinstance(v, str) or not v.startswith("0x"):
        return None
    try:
        return int(v, 16)
    except Exception:
        return None


def _decode_bep20_transfer_log(log_entry: dict[str, Any]) -> dict[str, Any] | None:
    topics = log_entry.get("topics") or []
    if len(topics) < 3:
        return None
    if str(topics[0]).lower() != ERC20_TRANSFER_TOPIC0:
        return None

    contract = str(log_entry.get("address") or "").lower()
    if contract not in TRACKED_BEP20_TOKENS:
        return None

    amount_raw = _from_hex_int(log_entry.get("data"))
    if amount_raw is None:
        return None

    from_addr = f"0x{str(topics[1])[-40:]}".lower()
    to_addr = f"0x{str(topics[2])[-40:]}".lower()
    token_meta = TRACKED_BEP20_TOKENS[contract]
    decimals = int(token_meta["decimals"])
    amount = amount_raw / (10**decimals)
    return {
        "contract": contract,
        "symbol": str(token_meta["symbol"]),
        "category": str(token_meta["class"]),
        "from": from_addr,
        "to": to_addr,
        "amount_raw": amount_raw,
        "amount": amount,
    }


@dataclass
class BnbState:
    last_scanned_block: int | None

    @classmethod
    def load(cls, path: Path) -> "BnbState":
        if not path.exists():
            return cls(last_scanned_block=None)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            v = data.get("last_scanned_block")
            return cls(last_scanned_block=int(v) if v is not None else None)
        except Exception:
            return cls(last_scanned_block=None)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"last_scanned_block": self.last_scanned_block}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class BnbSource(SourceScraper):
    @property
    def name(self) -> str:
        return "bnb"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.rpc_url = self.settings.bnb_rpc_url.strip()
        self.state_path = Path(self.settings.bnb_state_path)
        self.timeout = httpx.Timeout(int(self.settings.bnb_http_timeout_seconds))
        self.confirmation_depth = max(0, int(self.settings.bnb_confirmation_depth))
        self.min_transfer_usd = float(self.settings.bnb_min_transfer_usd)
        self.watchlist_min_transfer_usd = float(self.settings.bnb_watchlist_min_transfer_usd)
        self.watchlist = self._load_watchlist(Path(self.settings.bnb_watchlist_path))
        self.bnb_usd_fallback = float(self.settings.bnb_bnb_usd_fallback)
        self.price_cache_ttl_seconds = max(0, int(self.settings.bnb_price_cache_ttl_seconds))
        self.token_price_cache_path = Path(self.settings.token_price_cache_path)
        self.token_price_rate_limit_backoff_seconds = max(
            0, int(self.settings.token_price_rate_limit_backoff_seconds)
        )
        self.block_request_delay_seconds = float(self.settings.bnb_block_request_delay_seconds)
        self.block_fetch_max_retries = int(self.settings.bnb_block_fetch_max_retries)
        self.block_retry_backoff_seconds = float(self.settings.bnb_block_retry_backoff_seconds)
        self.max_blocks_per_cycle = max(1, int(self.settings.bnb_max_blocks_per_cycle))
        self.parallel_block_fetch_workers = max(1, int(self.settings.bnb_parallel_block_fetch_workers))
        self._fetch_executor: ThreadPoolExecutor | None = None
        if self.parallel_block_fetch_workers > 1:
            self._fetch_executor = ThreadPoolExecutor(
                max_workers=self.parallel_block_fetch_workers,
                thread_name_prefix="bnb-block-fetch",
            )
        self._last_block_request_monotonic: float | None = None
        self._cached_bnb_usd: float | None = None
        self._cached_bnb_usd_monotonic: float | None = None
        self._rpc_id = 0
        self._cached_token_prices: dict[str, float] | None = None
        self._cached_token_prices_monotonic: float | None = None
        self._transfer_logs_backoff_until_monotonic: float = 0.0
        self._bootstrapped = False

    @staticmethod
    def _is_get_logs_limit_error(err: Exception) -> bool:
        err_text = str(err)
        return (
            "method=eth_getLogs" in err_text
            and f"'code': {ETH_GET_LOGS_LIMIT_ERROR_CODE}" in err_text
            and "limit exceeded" in err_text
        )

    @staticmethod
    def _is_rate_limited_error(err: Exception) -> bool:
        if isinstance(err, httpx.HTTPStatusError):
            status = err.response.status_code if err.response is not None else None
            return status == 429

        err_text = str(err).lower()
        rate_limit_markers = (
            "rate limit",
            "too many requests",
            "limit exceeded",
            "code': 429",
            'code": 429',
            "code=-32005",
            "code': -32005",
            'code": -32005',
        )
        return any(marker in err_text for marker in rate_limit_markers)

    def _set_transfer_logs_backoff(self, block_num: int, err: Exception) -> None:
        backoff_seconds = max(30.0, self.block_retry_backoff_seconds * 20)
        self._transfer_logs_backoff_until_monotonic = time.monotonic() + backoff_seconds
        log.warning(
            "bnb.transfer_logs_rate_limited block=%s backoff_seconds=%.2f err=%s",
            block_num,
            backoff_seconds,
            err,
        )

    @staticmethod
    def _normalize_address(address: str | None) -> str | None:
        if not address:
            return None
        return address.strip().lower()

    def _load_watchlist(self, path: Path) -> dict[str, dict[str, str]]:
        if not path.exists():
            return {}
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("bnb.watchlist_load_failed path=%s err=%s", path, e)
            return {}

        watchlist: dict[str, dict[str, str]] = {}
        for entry in entries:
            addr = self._normalize_address(entry.get("address"))
            if not addr:
                continue
            watchlist[addr] = {
                "label": str(entry.get("label") or "unknown"),
                "category": str(entry.get("category") or "unknown"),
            }
        return watchlist

    def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def _throttle_block_request(self) -> None:
        min_spacing = max(0.0, self.block_request_delay_seconds)
        if min_spacing <= 0:
            return

        now = time.monotonic()
        if self._last_block_request_monotonic is not None:
            elapsed = now - self._last_block_request_monotonic
            remaining = min_spacing - elapsed
            if remaining > 0:
                self._sleep(remaining)

        self._last_block_request_monotonic = time.monotonic()

    def _rpc(self, client: httpx.Client, method: str, params: list[object]) -> object:
        self._rpc_id += 1
        payload = {"jsonrpc": "2.0", "id": self._rpc_id, "method": method, "params": params}
        resp = client.post(self.rpc_url, json=payload)
        resp.raise_for_status()
        body = resp.json()
        if body.get("error"):
            raise RuntimeError(f"rpc_error method={method} error={body['error']}")
        return body.get("result")

    def _get_now_block_number(self, client: httpx.Client) -> int:
        return int(str(self._rpc(client, "eth_blockNumber", [])), 16)

    def _get_block(self, client: httpx.Client, block_num: int) -> dict:
        if self.parallel_block_fetch_workers <= 1:
            self._throttle_block_request()
        block_hex = hex(block_num)
        return dict(self._rpc(client, "eth_getBlockByNumber", [block_hex, True]) or {})

    def _get_bep20_transfer_logs(self, client: httpx.Client, block_num: int) -> list[dict[str, Any]]:
        block_hex = hex(block_num)
        result = self._rpc(
            client,
            "eth_getLogs",
            [
                {
                    "fromBlock": block_hex,
                    "toBlock": block_hex,
                    "address": list(TRACKED_BEP20_TOKENS.keys()),
                    "topics": [ERC20_TRANSFER_TOPIC0],
                }
            ],
        )
        return list(result or [])

    def _get_tracked_token_prices_usd(self, client: httpx.Client) -> dict[str, float]:
        now = time.monotonic()
        if (
            self._cached_token_prices is not None
            and self._cached_token_prices_monotonic is not None
            and (now - self._cached_token_prices_monotonic) < self.price_cache_ttl_seconds
        ):
            return self._cached_token_prices

        fallback = {addr: float(meta["usd_fallback"]) for addr, meta in TRACKED_BEP20_TOKENS.items()}
        prices = fetch_token_prices_from_dexscreener(
            client=client,
            chain="bsc",
            contract_addresses=list(TRACKED_BEP20_TOKENS.keys()),
            fallback_prices=fallback,
            cache_path=self.token_price_cache_path,
            rate_limit_backoff_seconds=self.token_price_rate_limit_backoff_seconds,
        )
        self._cached_token_prices = prices
        self._cached_token_prices_monotonic = now
        return prices

    def _get_block_with_retries(self, client: httpx.Client, block_num: int) -> dict:
        max_attempts = max(1, self.block_fetch_max_retries + 1)
        for attempt in range(1, max_attempts + 1):
            try:
                return self._get_block(client, block_num)
            except Exception as e:
                if not self._is_rate_limited_error(e) or attempt >= max_attempts:
                    raise
                wait_seconds = self.block_retry_backoff_seconds * attempt
                log.warning(
                    "bnb.rate_limited block=%s attempt=%s/%s wait_seconds=%.2f",
                    block_num,
                    attempt,
                    max_attempts,
                    wait_seconds,
                )
                self._sleep(wait_seconds)

    def _get_bnb_usd_price(self, client: httpx.Client) -> float:
        now = time.monotonic()
        if (
            self._cached_bnb_usd is not None
            and self._cached_bnb_usd_monotonic is not None
            and (now - self._cached_bnb_usd_monotonic) < self.price_cache_ttl_seconds
        ):
            return self._cached_bnb_usd

        if is_price_lookup_backoff_active(now):
            if self._cached_bnb_usd is not None:
                return self._cached_bnb_usd
            return self.bnb_usd_fallback

        try:
            resp = client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "binancecoin", "vs_currencies": "usd"},
            )
            resp.raise_for_status()
            data = resp.json()
            price = float((data.get("binancecoin") or {}).get("usd"))
            if price > 0:
                register_price_lookup_backoff_success()
                self._cached_bnb_usd = price
                self._cached_bnb_usd_monotonic = now
                return price
        except Exception as e:
            if isinstance(e, httpx.HTTPStatusError):
                status = e.response.status_code if e.response is not None else None
                if status == 429:
                    retry_after_seconds = 0.0
                    if e.response is not None:
                        retry_after_header = e.response.headers.get("Retry-After")
                        if retry_after_header:
                            try:
                                retry_after_seconds = float(retry_after_header)
                            except ValueError:
                                retry_after_seconds = 0.0
                    backoff_seconds = activate_price_lookup_backoff(retry_after_seconds=retry_after_seconds)
                    if register_price_lookup_backoff_failure():
                        log.warning(
                            "bnb.price_lookup_rate_limited global_backoff_seconds=%.2f retry_after_seconds=%.2f err=%s",
                            backoff_seconds,
                            retry_after_seconds,
                            e,
                        )
                    return self._cached_bnb_usd if self._cached_bnb_usd is not None else self.bnb_usd_fallback
            if isinstance(e, httpx.RequestError):
                backoff_seconds = activate_price_lookup_backoff()
                if register_price_lookup_backoff_failure():
                    log.warning(
                        "bnb.price_lookup_request_failed global_backoff_seconds=%.2f err=%s",
                        backoff_seconds,
                        e,
                    )
                return self._cached_bnb_usd if self._cached_bnb_usd is not None else self.bnb_usd_fallback
            log.warning("bnb.price_lookup_failed err=%s", e)

        if self._cached_bnb_usd is not None:
            return self._cached_bnb_usd
        return self.bnb_usd_fallback

    def __del__(self) -> None:
        executor = getattr(self, "_fetch_executor", None)
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def fetch(self) -> list[CanonicalItem]:
        state = BnbState.load(self.state_path)
        out: list[CanonicalItem] = []

        with httpx.Client(timeout=self.timeout, headers={"User-Agent": "scraper-bot/0.1"}) as client:
            try:
                now_block = self._get_now_block_number(client)
            except Exception as e:
                log.warning("bnb.now_block_failed err=%s", e)
                return []

            # Quirk: BNB Chain can occasionally reorg very recent tip blocks on public RPCs.
            # Scan only finalized-enough blocks by lagging the head by confirmation_depth.
            safe_now_block = max(0, now_block - self.confirmation_depth)
            bnb_usd = self._get_bnb_usd_price(client)
            token_prices = self._get_tracked_token_prices_usd(client)
            if not self._bootstrapped:
                start_block = safe_now_block
                self._bootstrapped = True
            elif state.last_scanned_block is None:
                start_block = safe_now_block
            else:
                start_block = min(safe_now_block, state.last_scanned_block + 1)

            min_allowed_start_block = max(0, safe_now_block - self.max_blocks_per_cycle + 1)
            if start_block < min_allowed_start_block:
                log.warning(
                    "bnb.scan_window_clamped start_block=%s clamped_start_block=%s now_block=%s max_blocks_per_cycle=%s",
                    start_block,
                    min_allowed_start_block,
                    safe_now_block,
                    self.max_blocks_per_cycle,
                )
                start_block = min_allowed_start_block

            block_numbers = list(range(start_block, safe_now_block + 1))
            scanned_blocks = len(block_numbers)
            blocks_by_number: dict[int, dict[str, Any]] = {}

            if self.parallel_block_fetch_workers <= 1:
                for block_num in block_numbers:
                    try:
                        blocks_by_number[block_num] = self._get_block_with_retries(client, block_num)
                    except Exception as e:
                        log.warning("bnb.block_fetch_failed block=%s err=%s", block_num, e)
            else:
                executor = self._fetch_executor
                if executor is None:
                    executor = ThreadPoolExecutor(max_workers=self.parallel_block_fetch_workers)
                futures = {
                    executor.submit(self._get_block_with_retries, client, block_num): block_num
                    for block_num in block_numbers
                }
                for future in as_completed(futures):
                    block_num = futures[future]
                    try:
                        blocks_by_number[block_num] = future.result()
                    except Exception as e:
                        log.warning("bnb.block_fetch_failed block=%s err=%s", block_num, e)

            for block_num in block_numbers:
                block = blocks_by_number.get(block_num)
                if block is None:
                    continue

                block_ts = _from_hex_int(block.get("timestamp"))
                published_at = datetime.fromtimestamp(block_ts, tz=timezone.utc) if block_ts else None

                for tx in block.get("transactions") or []:
                    txhash = tx.get("hash")
                    from_addr = tx.get("from")
                    to_addr = tx.get("to")

                    value_wei = _from_hex_int(tx.get("value")) or 0
                    if value_wei > 0:
                        amount_bnb = value_wei / WEI_PER_BNB
                        amount_usd = amount_bnb * bnb_usd

                        from_watched = self.watchlist.get(self._normalize_address(from_addr) or "")
                        to_watched = self.watchlist.get(self._normalize_address(to_addr) or "")
                        transfer_threshold = self.min_transfer_usd
                        if from_watched or to_watched:
                            transfer_threshold = min(self.min_transfer_usd, self.watchlist_min_transfer_usd)
                        if amount_usd >= transfer_threshold:
                            watched_labels = [w["label"] for w in (from_watched, to_watched) if w and w.get("label")]
                            watched_title_prefix = (
                                "🎯 BNB watched-address movement" if watched_labels else "🚨 BNB major movement"
                            )
                            out.append(
                                CanonicalItem(
                                    item_type="onchain_transfer",
                                    title=f"{watched_title_prefix}: ${amount_usd:,.0f} ({amount_bnb:,.0f} BNB)",
                                    summary=(
                                        f"BNB transfer >= ${transfer_threshold:,.0f}: "
                                        f"from {from_addr} to {to_addr}"
                                        + (f" | watchlist={', '.join(watched_labels)}" if watched_labels else "")
                                    ),
                                    url=f"https://bscscan.com/tx/{txhash}" if txhash else None,
                                    published_at=published_at,
                                    scraped_at=_utc_now(),
                                    source="bnb",
                                    tags=["onchain", "bnb", "whale"] + (["watchlist"] if watched_labels else []),
                                    severity="high",
                                    classification={
                                        "priority": "high",
                                        "assetClass": "crypto",
                                        "subcategory": "onchain_transfer",
                                        "tickers": ["BNB"],
                                        "topics": ["whale_movement", "onchain"]
                                        + (["watchlist"] if watched_labels else []),
                                        "regions": [],
                                    },
                                    data={
                                        "chain": "bnb",
                                        "txId": txhash,
                                        "blockNumber": block_num,
                                        "from": from_addr,
                                        "to": to_addr,
                                        "amountWei": value_wei,
                                        "amountBnb": amount_bnb,
                                        "amountUsd": amount_usd,
                                        "bnbUsd": bnb_usd,
                                        "thresholdUsd": transfer_threshold,
                                        "isWatchlist": bool(watched_labels),
                                        "watchlistFrom": from_watched,
                                        "watchlistTo": to_watched,
                                    },
                                )
                            )

                transfer_logs = []
                if time.monotonic() >= self._transfer_logs_backoff_until_monotonic:
                    try:
                        transfer_logs = self._get_bep20_transfer_logs(client, block_num)
                    except Exception as e:
                        if self._is_get_logs_limit_error(e):
                            self._set_transfer_logs_backoff(block_num, e)
                        else:
                            log.warning("bnb.transfer_logs_fetch_failed block=%s err=%s", block_num, e)

                for event in transfer_logs:
                    token_transfer = _decode_bep20_transfer_log(event)
                    if not token_transfer:
                        continue

                    token_price = token_prices.get(token_transfer["contract"], 0.0)
                    amount_usd = token_transfer["amount"] * token_price

                    transfer_from = token_transfer["from"]
                    transfer_to = token_transfer["to"]
                    from_watched = self.watchlist.get(self._normalize_address(transfer_from) or "")
                    to_watched = self.watchlist.get(self._normalize_address(transfer_to) or "")
                    transfer_threshold = self.min_transfer_usd
                    if from_watched or to_watched:
                        transfer_threshold = min(self.min_transfer_usd, self.watchlist_min_transfer_usd)
                    if amount_usd < transfer_threshold:
                        continue

                    watched_labels = [w["label"] for w in (from_watched, to_watched) if w and w.get("label")]
                    watched_title_prefix = (
                        "🎯 BNB watched-address token movement" if watched_labels else "🚨 BNB major token movement"
                    )
                    txhash = event.get("transactionHash")
                    token_symbol = token_transfer["symbol"]
                    token_category = token_transfer["category"]
                    out.append(
                        CanonicalItem(
                            item_type="onchain_token_transfer",
                            title=(
                                f"{watched_title_prefix}: ${amount_usd:,.0f} "
                                f"({token_transfer['amount']:,.0f} {token_symbol})"
                            ),
                            summary=(
                                f"{token_symbol} ({token_category}) transfer >= ${transfer_threshold:,.0f}: "
                                f"from {transfer_from} to {transfer_to}"
                                + (f" | watchlist={', '.join(watched_labels)}" if watched_labels else "")
                            ),
                            url=f"https://bscscan.com/tx/{txhash}" if txhash else None,
                            published_at=published_at,
                            scraped_at=_utc_now(),
                            source="bnb",
                            tags=["onchain", "bnb", "whale", "token", token_category]
                            + (["watchlist"] if watched_labels else []),
                            severity="high",
                            classification={
                                "priority": "high",
                                "assetClass": "crypto",
                                "subcategory": "onchain_token_transfer",
                                "tickers": [token_symbol],
                                "topics": ["whale_movement", "onchain", "token", token_category]
                                + (["watchlist"] if watched_labels else []),
                                "regions": [],
                            },
                            data={
                                "chain": "bnb",
                                "txId": txhash,
                                "blockNumber": block_num,
                                "tokenContract": token_transfer["contract"],
                                "tokenSymbol": token_symbol,
                                "tokenCategory": token_category,
                                "from": transfer_from,
                                "to": transfer_to,
                                "amountRaw": token_transfer["amount_raw"],
                                "amount": token_transfer["amount"],
                                "amountUsd": amount_usd,
                                "tokenUsd": token_price,
                                "thresholdUsd": transfer_threshold,
                                "isWatchlist": bool(watched_labels),
                                "watchlistFrom": from_watched,
                                "watchlistTo": to_watched,
                            },
                        )
                    )
            state.last_scanned_block = safe_now_block
            state.save(self.state_path)
            log.debug(
                "bnb.scan_done now_block=%s safe_now_block=%s start_block=%s blocks=%s major_moves=%s",
                now_block,
                safe_now_block,
                start_block,
                scanned_blocks,
                len(out),
            )

        return out
