from __future__ import annotations

import json
import logging
import time
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

SUN_PER_TRX = 1_000_000
TRC20_TRANSFER_SELECTOR = "a9059cbb"

TRACKED_TRC20_TOKENS = {
    "a614f803b6fd780986a42c78ec9c7f77e6ded13c": {"symbol": "USDT", "decimals": 6, "class": "stablecoin", "usd_fallback": 1.0},
    "3487b63d30b5b2c87fb7ffa8bcfade38eaac1abe": {"symbol": "USDC", "decimals": 6, "class": "stablecoin", "usd_fallback": 1.0},
    "41ca6c0f7a8f7f8f40f2de0f706032ef1f6f4f94": {"symbol": "BTT", "decimals": 18, "class": "meme", "usd_fallback": 0.000001},
    "50327c6c5a14dcade707abad2e27eb517df87ab5": {"symbol": "JST", "decimals": 18, "class": "defi", "usd_fallback": 0.03},
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_dt_from_ms(ms: int | None) -> datetime | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


@dataclass
class TronState:
    last_scanned_block: int | None

    @classmethod
    def load(cls, path: Path) -> "TronState":
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




def _extract_trc20_transfers(tx: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    contracts = ((tx.get("raw_data") or {}).get("contract") or [])
    for c in contracts:
        if c.get("type") != "TriggerSmartContract":
            continue
        value = ((c.get("parameter") or {}).get("value") or {})
        data_hex = str(value.get("data") or "").lower()
        if not data_hex.startswith(TRC20_TRANSFER_SELECTOR) or len(data_hex) < 8 + 64 + 64:
            continue

        contract = str(value.get("contract_address") or "").lower()
        if contract not in TRACKED_TRC20_TOKENS:
            continue

        to_hex_32 = data_hex[8:8 + 64]
        amount_hex_32 = data_hex[8 + 64:8 + 128]
        try:
            amount_raw = int(amount_hex_32, 16)
        except ValueError:
            continue

        meta = TRACKED_TRC20_TOKENS[contract]
        out.append(
            {
                "contract": contract,
                "symbol": str(meta["symbol"]),
                "category": str(meta["class"]),
                "from": value.get("owner_address"),
                "to": f"41{to_hex_32[-40:]}",
                "amount_raw": amount_raw,
                "amount": amount_raw / (10 ** int(meta["decimals"])),
            }
        )
    return out

def _extract_native_transfers(tx: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    contracts = ((tx.get("raw_data") or {}).get("contract") or [])
    for c in contracts:
        parameter = c.get("parameter") or {}
        value = parameter.get("value") or {}
        if c.get("type") != "TransferContract":
            continue
        amount_sun = value.get("amount")
        if amount_sun is None:
            continue
        out.append(
            {
                "from": value.get("owner_address"),
                "to": value.get("to_address"),
                "amount_sun": int(amount_sun),
            }
        )
    return out


class TronSource(SourceScraper):
    @property
    def name(self) -> str:
        return "tron"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.api_base = self.settings.tron_api_base.rstrip("/")
        self.api_key = self.settings.tron_api_key.strip()
        self.state_path = Path(self.settings.tron_state_path)
        self.timeout = httpx.Timeout(int(self.settings.tron_http_timeout_seconds))
        self.lookback_blocks = int(self.settings.tron_lookback_blocks_on_boot)
        self.min_transfer_usd = float(self.settings.tron_min_transfer_usd)
        self.watchlist_min_transfer_usd = float(self.settings.tron_watchlist_min_transfer_usd)
        self.watchlist = self._load_watchlist(Path(self.settings.tron_watchlist_path))
        self.trx_usd_fallback = float(self.settings.tron_trx_usd_fallback)
        self.price_cache_ttl_seconds = max(0, int(self.settings.tron_price_cache_ttl_seconds))
        self.trx_price_rate_limit_backoff_seconds = max(
            0, int(self.settings.tron_price_rate_limit_backoff_seconds)
        )
        self.token_price_cache_path = Path(self.settings.token_price_cache_path)
        self.token_price_rate_limit_backoff_seconds = max(
            0, int(self.settings.token_price_rate_limit_backoff_seconds)
        )
        self.block_request_delay_seconds = float(self.settings.tron_block_request_delay_seconds)
        self.block_fetch_max_retries = int(self.settings.tron_block_fetch_max_retries)
        self.block_retry_backoff_seconds = float(self.settings.tron_block_retry_backoff_seconds)
        self._last_block_request_monotonic: float | None = None
        self._cached_trx_usd: float | None = None
        self._cached_trx_usd_monotonic: float | None = None
        self._trx_price_rate_limited_until_monotonic: float | None = None
        self._cached_token_prices: dict[str, float] | None = None
        self._cached_token_prices_monotonic: float | None = None
        self._bootstrapped = False

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
            log.warning("tron.watchlist_load_failed path=%s err=%s", path, e)
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

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": "scraper-bot/0.1"}
        if self.api_key:
            headers["TRON-PRO-API-KEY"] = self.api_key
        return headers

    def _get_now_block_number(self, client: httpx.Client) -> int:
        resp = client.post(f"{self.api_base}/wallet/getnowblock", json={})
        resp.raise_for_status()
        data = resp.json()
        return int((((data.get("block_header") or {}).get("raw_data") or {}).get("number")))

    def _get_block(self, client: httpx.Client, block_num: int) -> dict[str, Any]:
        self._throttle_block_request()
        resp = client.post(f"{self.api_base}/wallet/getblockbynum", json={"num": block_num})
        resp.raise_for_status()
        return resp.json()

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

    def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def _get_block_with_retries(self, client: httpx.Client, block_num: int) -> dict[str, Any]:
        max_attempts = max(1, self.block_fetch_max_retries + 1)
        for attempt in range(1, max_attempts + 1):
            try:
                return self._get_block(client, block_num)
            except httpx.HTTPStatusError as e:
                status = e.response.status_code if e.response is not None else None
                if status != 429 or attempt >= max_attempts:
                    raise

                retry_after = 0.0
                if e.response is not None:
                    retry_after_header = e.response.headers.get("Retry-After")
                    if retry_after_header:
                        try:
                            retry_after = float(retry_after_header)
                        except ValueError:
                            retry_after = 0.0

                wait_seconds = max(retry_after, self.block_retry_backoff_seconds * attempt)
                log.warning(
                    "tron.rate_limited block=%s attempt=%s/%s wait_seconds=%.2f",
                    block_num,
                    attempt,
                    max_attempts,
                    wait_seconds,
                )
                self._sleep(wait_seconds)

    def _get_trx_usd_price(self, client: httpx.Client) -> float:
        now = time.monotonic()
        if (
            self._cached_trx_usd is not None
            and self._cached_trx_usd_monotonic is not None
            and (now - self._cached_trx_usd_monotonic) < self.price_cache_ttl_seconds
        ):
            return self._cached_trx_usd

        if is_price_lookup_backoff_active(now):
            if self._cached_trx_usd is not None:
                return self._cached_trx_usd
            return self.trx_usd_fallback

        if self._trx_price_rate_limited_until_monotonic is not None and now < self._trx_price_rate_limited_until_monotonic:
            if self._cached_trx_usd is not None:
                return self._cached_trx_usd
            return self.trx_usd_fallback

        try:
            resp = client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "tron", "vs_currencies": "usd"},
            )
            resp.raise_for_status()
            data = resp.json()
            price = float((data.get("tron") or {}).get("usd"))
            if price > 0:
                register_price_lookup_backoff_success()
                self._trx_price_rate_limited_until_monotonic = None
                self._cached_trx_usd = price
                self._cached_trx_usd_monotonic = now
                return price
        except httpx.HTTPStatusError as e:
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
                backoff_seconds = max(
                    retry_after_seconds,
                    float(self.trx_price_rate_limit_backoff_seconds),
                )
                self._trx_price_rate_limited_until_monotonic = now + backoff_seconds
                global_backoff_seconds = activate_price_lookup_backoff(retry_after_seconds=retry_after_seconds)
                if register_price_lookup_backoff_failure():
                    log.warning(
                        "tron.price_lookup_rate_limited global_backoff_seconds=%.2f retry_after_seconds=%.2f err=%s",
                        global_backoff_seconds,
                        retry_after_seconds,
                        e,
                    )
                return self._cached_trx_usd if self._cached_trx_usd is not None else self.trx_usd_fallback
            log.warning("tron.price_lookup_failed err=%s", e)
        except Exception as e:
            if isinstance(e, httpx.RequestError):
                backoff_seconds = activate_price_lookup_backoff()
                if register_price_lookup_backoff_failure():
                    log.warning(
                        "tron.price_lookup_request_failed global_backoff_seconds=%.2f err=%s",
                        backoff_seconds,
                        e,
                    )
                return self._cached_trx_usd if self._cached_trx_usd is not None else self.trx_usd_fallback
            log.warning("tron.price_lookup_failed err=%s", e)

        if self._cached_trx_usd is not None:
            return self._cached_trx_usd
        return self.trx_usd_fallback

    def _get_tracked_token_prices_usd(self, client: httpx.Client) -> dict[str, float]:
        now = time.monotonic()
        if (
            self._cached_token_prices is not None
            and self._cached_token_prices_monotonic is not None
            and (now - self._cached_token_prices_monotonic) < self.price_cache_ttl_seconds
        ):
            return self._cached_token_prices

        fallback = {addr: float(meta["usd_fallback"]) for addr, meta in TRACKED_TRC20_TOKENS.items()}
        prices = fetch_token_prices_from_dexscreener(
            client=client,
            chain="tron",
            contract_addresses=list(TRACKED_TRC20_TOKENS.keys()),
            fallback_prices=fallback,
            cache_path=self.token_price_cache_path,
            rate_limit_backoff_seconds=self.token_price_rate_limit_backoff_seconds,
        )
        self._cached_token_prices = prices
        self._cached_token_prices_monotonic = now
        return prices


    def fetch(self) -> list[CanonicalItem]:
        state = TronState.load(self.state_path)
        out: list[CanonicalItem] = []

        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            try:
                now_block = self._get_now_block_number(client)
            except Exception as e:
                log.warning(f"tron.now_block_failed err={e}")
                return []

            trx_usd = self._get_trx_usd_price(client)
            token_prices = self._get_tracked_token_prices_usd(client)
            if not self._bootstrapped:
                start_block = now_block
                self._bootstrapped = True
            elif state.last_scanned_block is None:
                start_block = now_block
            else:
                start_block = min(now_block, state.last_scanned_block + 1)

            scanned_blocks = 0
            for block_num in range(start_block, now_block + 1):
                scanned_blocks += 1
                try:
                    block = self._get_block_with_retries(client, block_num)
                except Exception as e:
                    log.warning(f"tron.block_fetch_failed block={block_num} err={e}")
                    continue

                block_ts_ms = (((block.get("block_header") or {}).get("raw_data") or {}).get("timestamp"))
                published_at = _to_dt_from_ms(block_ts_ms)

                for tx in block.get("transactions") or []:
                    txid = tx.get("txID")
                    for transfer in _extract_native_transfers(tx):
                        from_addr = transfer["from"]
                        to_addr = transfer["to"]
                        from_watched = self.watchlist.get(self._normalize_address(from_addr) or "")
                        to_watched = self.watchlist.get(self._normalize_address(to_addr) or "")
                        amount_trx = transfer["amount_sun"] / SUN_PER_TRX
                        amount_usd = amount_trx * trx_usd
                        transfer_threshold = self.min_transfer_usd
                        if from_watched or to_watched:
                            transfer_threshold = min(self.min_transfer_usd, self.watchlist_min_transfer_usd)

                        if amount_usd < transfer_threshold:
                            continue

                        watched_labels = [
                            w["label"]
                            for w in (from_watched, to_watched)
                            if w and w.get("label")
                        ]
                        watched_title_prefix = "🎯 TRON watched-address movement" if watched_labels else "🚨 TRON major movement"

                        scraped_at = _utc_now()
                        out.append(
                            CanonicalItem(
                                item_type="onchain_transfer",
                                title=f"{watched_title_prefix}: ${amount_usd:,.0f} ({amount_trx:,.0f} TRX)",
                                summary=(
                                    f"Native TRX transfer >= ${transfer_threshold:,.0f}: "
                                    f"from {from_addr} to {to_addr}"
                                    + (f" | watchlist={', '.join(watched_labels)}" if watched_labels else "")
                                ),
                                url=f"https://tronscan.org/#/transaction/{txid}" if txid else None,
                                published_at=published_at,
                                scraped_at=scraped_at,
                                source="tron",
                                tags=["onchain", "tron", "whale"] + (["watchlist"] if watched_labels else []),
                                severity="high",
                                classification={
                                    "priority": "high",
                                    "assetClass": "crypto",
                                    "subcategory": "onchain_transfer",
                                    "tickers": ["TRX"],
                                    "topics": ["whale_movement", "onchain"] + (["watchlist"] if watched_labels else []),
                                    "regions": [],
                                },
                                data={
                                    "chain": "tron",
                                    "txId": txid,
                                    "blockNumber": block_num,
                                    "from": from_addr,
                                    "to": to_addr,
                                    "amountSun": transfer["amount_sun"],
                                    "amountTrx": amount_trx,
                                    "amountUsd": amount_usd,
                                    "trxUsd": trx_usd,
                                    "thresholdUsd": transfer_threshold,
                                    "isWatchlist": bool(watched_labels),
                                    "watchlistFrom": from_watched,
                                    "watchlistTo": to_watched,
                                },
                            )
                        )

                    for transfer in _extract_trc20_transfers(tx):
                        from_addr = transfer["from"]
                        to_addr = transfer["to"]
                        from_watched = self.watchlist.get(self._normalize_address(from_addr) or "")
                        to_watched = self.watchlist.get(self._normalize_address(to_addr) or "")
                        token_price = token_prices.get(transfer["contract"], 0.0)
                        amount_usd = transfer["amount"] * token_price

                        transfer_threshold = self.min_transfer_usd
                        if from_watched or to_watched:
                            transfer_threshold = min(self.min_transfer_usd, self.watchlist_min_transfer_usd)
                        if amount_usd < transfer_threshold:
                            continue

                        watched_labels = [w["label"] for w in (from_watched, to_watched) if w and w.get("label")]
                        watched_title_prefix = (
                            "🎯 TRON watched-address token movement"
                            if watched_labels
                            else "🚨 TRON major token movement"
                        )
                        token_symbol = transfer["symbol"]
                        token_category = transfer["category"]
                        out.append(
                            CanonicalItem(
                                item_type="onchain_token_transfer",
                                title=(
                                    f"{watched_title_prefix}: ${amount_usd:,.0f} "
                                    f"({transfer['amount']:,.0f} {token_symbol})"
                                ),
                                summary=(
                                    f"{token_symbol} ({token_category}) transfer >= ${transfer_threshold:,.0f}: "
                                    f"from {from_addr} to {to_addr}"
                                    + (f" | watchlist={', '.join(watched_labels)}" if watched_labels else "")
                                ),
                                url=f"https://tronscan.org/#/transaction/{txid}" if txid else None,
                                published_at=published_at,
                                scraped_at=_utc_now(),
                                source="tron",
                                tags=["onchain", "tron", "whale", "token", token_category]
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
                                    "chain": "tron",
                                    "txId": txid,
                                    "blockNumber": block_num,
                                    "tokenContract": transfer["contract"],
                                    "tokenSymbol": token_symbol,
                                    "tokenCategory": token_category,
                                    "from": from_addr,
                                    "to": to_addr,
                                    "amountRaw": transfer["amount_raw"],
                                    "amount": transfer["amount"],
                                    "amountUsd": amount_usd,
                                    "tokenUsd": token_price,
                                    "thresholdUsd": transfer_threshold,
                                    "isWatchlist": bool(watched_labels),
                                    "watchlistFrom": from_watched,
                                    "watchlistTo": to_watched,
                                },
                            )
                        )
            state.last_scanned_block = now_block
            state.save(self.state_path)
            log.debug(
                "tron.scan_done now_block=%s start_block=%s blocks=%s major_moves=%s",
                now_block,
                start_block,
                scanned_blocks,
                len(out),
            )

        return out
