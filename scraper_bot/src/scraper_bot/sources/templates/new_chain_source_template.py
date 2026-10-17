"""Template source scraper for onboarding a new chain.

Usage:
1) Copy this file to `scraper_bot/src/scraper_bot/sources/<chain>_source.py`.
2) Replace all `newchain` placeholders.
3) Wire into registry + settings + CLI/runner (see docs/ADDING_NEW_CHAIN.md).

IMPORTANT:
- Keep terminal status lifecycle consistent (`running` -> `idle`/`error`) by
  using the existing Runner flow instead of custom loops/log printers.
- Emit CanonicalItem objects only; let SchemaAdapter handle intel schema mapping.
"""

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

# Example constants: replace with chain-specific values.
BASE_UNITS_PER_COIN = 10**18

# Replace with tracked token contracts for this chain.
TRACKED_TOKENS = {
    # "0xcontract": {"symbol": "TOKEN", "decimals": 18, "class": "defi", "usd_fallback": 1.0},
}


@dataclass
class NewChainState:
    last_scanned_block: int | None

    @classmethod
    def load(cls, path: Path) -> "NewChainState":
        if not path.exists():
            return cls(last_scanned_block=None)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            value = data.get("last_scanned_block")
            return cls(last_scanned_block=int(value) if value is not None else None)
        except Exception:
            return cls(last_scanned_block=None)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"last_scanned_block": self.last_scanned_block}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class NewChainSource(SourceScraper):
    @property
    def name(self) -> str:
        # MUST match registry/CLI interval map keys.
        return "newchain"

    def __init__(self) -> None:
        self.settings = get_settings()

        # Add these config keys in scraper_bot/core/config.py.
        self.rpc_url = self.settings.newchain_rpc_url.strip()
        self.state_path = Path(self.settings.newchain_state_path)
        self.timeout = httpx.Timeout(int(self.settings.newchain_http_timeout_seconds))
        self.lookback_blocks = int(self.settings.newchain_lookback_blocks_on_boot)

        self.min_transfer_usd = float(self.settings.newchain_min_transfer_usd)
        self.watchlist_min_transfer_usd = float(self.settings.newchain_watchlist_min_transfer_usd)
        self.watchlist = self._load_watchlist(Path(self.settings.newchain_watchlist_path))

        self.native_usd_fallback = float(self.settings.newchain_native_usd_fallback)
        self.price_cache_ttl_seconds = max(0, int(self.settings.newchain_price_cache_ttl_seconds))

        self.block_request_delay_seconds = float(self.settings.newchain_block_request_delay_seconds)
        self.block_fetch_max_retries = int(self.settings.newchain_block_fetch_max_retries)
        self.block_retry_backoff_seconds = float(self.settings.newchain_block_retry_backoff_seconds)

        self._rpc_id = 0
        self._last_block_request_monotonic: float | None = None
        self._cached_native_usd: float | None = None
        self._cached_native_usd_monotonic: float | None = None
        self._cached_token_prices: dict[str, float] | None = None
        self._cached_token_prices_monotonic: float | None = None

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
        except Exception as exc:
            log.warning("newchain.watchlist_load_failed path=%s err=%s", path, exc)
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
            raise RuntimeError(f"newchain.rpc_error method={method} error={body['error']}")
        return body.get("result")

    def _get_now_block_number(self, client: httpx.Client) -> int:
        # Replace with chain-specific method + parsing.
        return int(str(self._rpc(client, "eth_blockNumber", [])), 16)

    def _get_block(self, client: httpx.Client, block_num: int) -> dict[str, Any]:
        self._throttle_block_request()
        return dict(self._rpc(client, "eth_getBlockByNumber", [hex(block_num), True]) or {})

    def _get_block_with_retries(self, client: httpx.Client, block_num: int) -> dict[str, Any]:
        max_attempts = max(1, self.block_fetch_max_retries + 1)
        for attempt in range(1, max_attempts + 1):
            try:
                return self._get_block(client, block_num)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status != 429 or attempt >= max_attempts:
                    raise
                wait_seconds = self.block_retry_backoff_seconds * attempt
                log.warning(
                    "newchain.rate_limited block=%s attempt=%s/%s wait_seconds=%.2f",
                    block_num,
                    attempt,
                    max_attempts,
                    wait_seconds,
                )
                self._sleep(wait_seconds)

    def _get_native_usd_price(self, client: httpx.Client) -> float:
        now = time.monotonic()
        if (
            self._cached_native_usd is not None
            and self._cached_native_usd_monotonic is not None
            and (now - self._cached_native_usd_monotonic) < self.price_cache_ttl_seconds
        ):
            return self._cached_native_usd

        if is_price_lookup_backoff_active(now):
            if self._cached_native_usd is not None:
                return self._cached_native_usd
            return self.native_usd_fallback

        try:
            # Replace coingecko id for target chain.
            response = client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "ethereum", "vs_currencies": "usd"},
            )
            response.raise_for_status()
            price = float((response.json().get("ethereum") or {}).get("usd"))
            if price > 0:
                register_price_lookup_backoff_success()
                self._cached_native_usd = price
                self._cached_native_usd_monotonic = now
                return price
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code if exc.response is not None else None
                if status == 429:
                    retry_after_seconds = 0.0
                    if exc.response is not None:
                        retry_after_header = exc.response.headers.get("Retry-After")
                        if retry_after_header:
                            try:
                                retry_after_seconds = float(retry_after_header)
                            except ValueError:
                                retry_after_seconds = 0.0
                    backoff_seconds = activate_price_lookup_backoff(retry_after_seconds=retry_after_seconds)
                    if register_price_lookup_backoff_failure():
                        log.warning(
                            "newchain.price_lookup_rate_limited global_backoff_seconds=%.2f retry_after_seconds=%.2f err=%s",
                            backoff_seconds,
                            retry_after_seconds,
                            exc,
                        )
                    return self._cached_native_usd if self._cached_native_usd is not None else self.native_usd_fallback
            if isinstance(exc, httpx.RequestError):
                backoff_seconds = activate_price_lookup_backoff()
                if register_price_lookup_backoff_failure():
                    log.warning(
                        "newchain.price_lookup_request_failed global_backoff_seconds=%.2f err=%s",
                        backoff_seconds,
                        exc,
                    )
                return self._cached_native_usd if self._cached_native_usd is not None else self.native_usd_fallback
            log.warning("newchain.price_lookup_failed err=%s", exc)

        if self._cached_native_usd is not None:
            return self._cached_native_usd
        return self.native_usd_fallback

    def _get_tracked_token_prices_usd(self, client: httpx.Client) -> dict[str, float]:
        now = time.monotonic()
        if (
            self._cached_token_prices is not None
            and self._cached_token_prices_monotonic is not None
            and (now - self._cached_token_prices_monotonic) < self.price_cache_ttl_seconds
        ):
            return self._cached_token_prices

        fallback = {addr: float(meta["usd_fallback"]) for addr, meta in TRACKED_TOKENS.items()}
        prices = fetch_token_prices_from_dexscreener(
            client=client,
            chain="ethereum",  # replace with chain slug supported by Dexscreener
            contract_addresses=list(TRACKED_TOKENS.keys()),
            fallback_prices=fallback,
        )
        self._cached_token_prices = prices
        self._cached_token_prices_monotonic = now
        return prices

    def _extract_native_transfers(self, block: dict[str, Any]) -> list[dict[str, Any]]:
        """Replace with chain-specific native transfer decoding.

        Return list entries with: from, to, amount_base_units, txid
        """
        out: list[dict[str, Any]] = []
        for tx in block.get("transactions") or []:
            value_hex = tx.get("value")
            if not value_hex:
                continue
            try:
                amount_base_units = int(str(value_hex), 16)
            except Exception:
                continue
            if amount_base_units <= 0:
                continue
            out.append(
                {
                    "from": tx.get("from"),
                    "to": tx.get("to"),
                    "amount_base_units": amount_base_units,
                    "txid": tx.get("hash"),
                }
            )
        return out

    def _extract_token_transfers(self, client: httpx.Client, block_num: int) -> list[dict[str, Any]]:
        """Replace with chain-specific token transfer extraction.

        Return list entries with:
        contract, symbol, category, from, to, amount_raw, amount, txid
        """
        return []

    def fetch(self) -> list[CanonicalItem]:
        state = NewChainState.load(self.state_path)
        out: list[CanonicalItem] = []

        with httpx.Client(timeout=self.timeout, headers={"User-Agent": "scraper-bot/0.1"}) as client:
            try:
                now_block = self._get_now_block_number(client)
            except Exception as exc:
                log.warning("newchain.now_block_failed err=%s", exc)
                return []

            native_usd = self._get_native_usd_price(client)
            token_prices = self._get_tracked_token_prices_usd(client)

            if state.last_scanned_block is None:
                # Cold starts should begin at tip and never backfill historical blocks.
                start_block = now_block
            else:
                start_block = min(now_block, state.last_scanned_block + 1)

            scanned_blocks = 0
            for block_num in range(start_block, now_block + 1):
                scanned_blocks += 1
                try:
                    block = self._get_block_with_retries(client, block_num)
                except Exception as exc:
                    log.warning("newchain.block_fetch_failed block=%s err=%s", block_num, exc)
                    continue

                published_at = datetime.now(timezone.utc)  # replace with block timestamp when available

                for transfer in self._extract_native_transfers(block):
                    from_addr = transfer["from"]
                    to_addr = transfer["to"]
                    amount_coin = transfer["amount_base_units"] / BASE_UNITS_PER_COIN
                    amount_usd = amount_coin * native_usd

                    from_watched = self.watchlist.get(self._normalize_address(from_addr) or "")
                    to_watched = self.watchlist.get(self._normalize_address(to_addr) or "")
                    transfer_threshold = self.min_transfer_usd
                    if from_watched or to_watched:
                        transfer_threshold = min(self.min_transfer_usd, self.watchlist_min_transfer_usd)
                    if amount_usd < transfer_threshold:
                        continue

                    watched_labels = [w["label"] for w in (from_watched, to_watched) if w and w.get("label")]
                    watched_prefix = (
                        "🎯 NEWCHAIN watched-address movement" if watched_labels else "🚨 NEWCHAIN major movement"
                    )

                    out.append(
                        CanonicalItem(
                            item_type="onchain_transfer",
                            title=f"{watched_prefix}: ${amount_usd:,.0f} ({amount_coin:,.0f} NATIVE)",
                            summary=(
                                f"Native transfer >= ${transfer_threshold:,.0f}: from {from_addr} to {to_addr}"
                                + (f" | watchlist={', '.join(watched_labels)}" if watched_labels else "")
                            ),
                            url=None,  # put block explorer URL here
                            published_at=published_at,
                            scraped_at=datetime.now(timezone.utc),
                            source=self.name,
                            tags=["onchain", self.name, "whale"] + (["watchlist"] if watched_labels else []),
                            severity="high",
                            classification={
                                "priority": "high",
                                "assetClass": "crypto",
                                "subcategory": "onchain_transfer",
                                "tickers": ["NATIVE"],
                                "topics": ["whale_movement", "onchain"] + (["watchlist"] if watched_labels else []),
                                "regions": [],
                            },
                            data={
                                "chain": self.name,
                                "txId": transfer.get("txid"),
                                "blockNumber": block_num,
                                "from": from_addr,
                                "to": to_addr,
                                "amountBaseUnits": transfer["amount_base_units"],
                                "amountNative": amount_coin,
                                "amountUsd": amount_usd,
                                "nativeUsd": native_usd,
                                "thresholdUsd": transfer_threshold,
                                "isWatchlist": bool(watched_labels),
                                "watchlistFrom": from_watched,
                                "watchlistTo": to_watched,
                            },
                        )
                    )

                for transfer in self._extract_token_transfers(client, block_num):
                    token_price = token_prices.get(transfer["contract"], 0.0)
                    amount_usd = transfer["amount"] * token_price

                    from_addr = transfer["from"]
                    to_addr = transfer["to"]
                    from_watched = self.watchlist.get(self._normalize_address(from_addr) or "")
                    to_watched = self.watchlist.get(self._normalize_address(to_addr) or "")
                    transfer_threshold = self.min_transfer_usd
                    if from_watched or to_watched:
                        transfer_threshold = min(self.min_transfer_usd, self.watchlist_min_transfer_usd)
                    if amount_usd < transfer_threshold:
                        continue

                    watched_labels = [w["label"] for w in (from_watched, to_watched) if w and w.get("label")]
                    watched_prefix = (
                        "🎯 NEWCHAIN watched-address token movement"
                        if watched_labels
                        else "🚨 NEWCHAIN major token movement"
                    )

                    token_symbol = transfer["symbol"]
                    token_category = transfer["category"]
                    out.append(
                        CanonicalItem(
                            item_type="onchain_token_transfer",
                            title=f"{watched_prefix}: ${amount_usd:,.0f} ({transfer['amount']:,.0f} {token_symbol})",
                            summary=(
                                f"{token_symbol} ({token_category}) transfer >= ${transfer_threshold:,.0f}: "
                                f"from {from_addr} to {to_addr}"
                                + (f" | watchlist={', '.join(watched_labels)}" if watched_labels else "")
                            ),
                            url=None,
                            published_at=published_at,
                            scraped_at=datetime.now(timezone.utc),
                            source=self.name,
                            tags=["onchain", self.name, "whale", "token", token_category]
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
                                "chain": self.name,
                                "txId": transfer.get("txid"),
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
                "newchain.scan_done now_block=%s start_block=%s blocks=%s major_moves=%s",
                now_block,
                start_block,
                scanned_blocks,
                len(out),
            )

        return out
