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

log = logging.getLogger(__name__)

SATOSHIS_PER_BTC = 100_000_000


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class BtcState:
    last_scanned_block: int | None

    @classmethod
    def load(cls, path: Path) -> "BtcState":
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


class BtcSource(SourceScraper):
    @property
    def name(self) -> str:
        return "btc"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.rpc_url = self.settings.btc_rpc_url.strip().rstrip("/")
        self.fallback_rpc_urls = [
            url.strip().rstrip("/")
            for url in self.settings.btc_fallback_rpc_urls.split(",")
            if url.strip()
        ]
        self.state_path = Path(self.settings.btc_state_path)
        self.timeout = httpx.Timeout(int(self.settings.btc_http_timeout_seconds))
        self.lookback_blocks = int(self.settings.btc_lookback_blocks_on_boot)
        self.min_transfer_usd = float(self.settings.btc_min_transfer_usd)
        self.watchlist_min_transfer_usd = float(self.settings.btc_watchlist_min_transfer_usd)
        self.watchlist = self._load_watchlist(Path(self.settings.btc_watchlist_path))
        self.btc_usd_fallback = float(self.settings.btc_usd_fallback)
        self.price_cache_ttl_seconds = max(0, int(self.settings.btc_price_cache_ttl_seconds))
        self.block_request_delay_seconds = float(self.settings.btc_block_request_delay_seconds)
        self.block_fetch_max_retries = int(self.settings.btc_block_fetch_max_retries)
        self.block_retry_backoff_seconds = float(self.settings.btc_block_retry_backoff_seconds)
        self.max_blocks_per_cycle = max(1, int(self.settings.btc_max_blocks_per_cycle))
        self.cycle_time_budget_seconds = max(1, int(self.settings.btc_cycle_time_budget_seconds))
        self.parallel_tx_fetch_workers = max(1, int(self.settings.btc_parallel_tx_fetch_workers))
        self._active_rpc_url = self.rpc_url
        self._last_request_monotonic: float | None = None
        self._cached_btc_usd: float | None = None
        self._cached_btc_usd_monotonic: float | None = None

    @staticmethod
    def _normalize_address(address: str | None) -> str | None:
        if not address:
            return None
        return address.strip()

    def _load_watchlist(self, path: Path) -> dict[str, dict[str, str]]:
        if not path.exists():
            return {}
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("btc.watchlist_load_failed path=%s err=%s", path, exc)
            return {}

        watchlist: dict[str, dict[str, str]] = {}
        for entry in entries:
            address = self._normalize_address(entry.get("address"))
            if not address:
                continue
            watchlist[address] = {
                "label": str(entry.get("label") or "unknown"),
                "category": str(entry.get("category") or "unknown"),
            }
        return watchlist

    def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def _throttle_request(self) -> None:
        min_spacing = max(0.0, self.block_request_delay_seconds)
        if min_spacing <= 0:
            return

        now = time.monotonic()
        if self._last_request_monotonic is not None:
            elapsed = now - self._last_request_monotonic
            remaining = min_spacing - elapsed
            if remaining > 0:
                self._sleep(remaining)

        self._last_request_monotonic = time.monotonic()

    @staticmethod
    def _is_rate_limited_error(err: Exception) -> bool:
        if isinstance(err, httpx.HTTPStatusError):
            status = err.response.status_code if err.response is not None else None
            return status == 429

        err_text = str(err).lower()
        return any(marker in err_text for marker in ("429", "rate limit", "too many requests", "too busy"))

    def _get_candidate_rpc_urls(self) -> list[str]:
        urls = [self._active_rpc_url]
        urls.extend(url for url in self.fallback_rpc_urls if url and url != self._active_rpc_url)
        if self.rpc_url not in urls:
            urls.append(self.rpc_url)
        return urls

    def _request_json(self, client: httpx.Client, path: str, *, throttle: bool = True) -> Any:
        last_error: Exception | None = None
        for base_url in self._get_candidate_rpc_urls():
            url = f"{base_url}{path}"
            try:
                if throttle:
                    self._throttle_request()
                response = client.get(url)
                response.raise_for_status()
                self._active_rpc_url = base_url
                return response.json()
            except Exception as exc:
                last_error = exc
                continue

        if last_error:
            raise last_error
        raise RuntimeError("btc.request_failed_no_rpc_candidate")

    def _request_text(self, client: httpx.Client, path: str, *, throttle: bool = True) -> str:
        last_error: Exception | None = None
        for base_url in self._get_candidate_rpc_urls():
            url = f"{base_url}{path}"
            try:
                if throttle:
                    self._throttle_request()
                response = client.get(url)
                response.raise_for_status()
                self._active_rpc_url = base_url
                return response.text.strip()
            except Exception as exc:
                last_error = exc
                continue

        if last_error:
            raise last_error
        raise RuntimeError("btc.request_failed_no_rpc_candidate")

    def _get_tip_block_height(self, client: httpx.Client) -> int:
        return int(self._request_text(client, "/blocks/tip/height"))

    def _get_block_hash_by_height(self, client: httpx.Client, block_height: int) -> str:
        return self._request_text(client, f"/block-height/{block_height}")

    def _get_block_txids(self, client: httpx.Client, block_hash: str) -> list[str]:
        result = self._request_json(client, f"/block/{block_hash}/txids")
        return list(result or [])

    def _get_transaction(self, client: httpx.Client, txid: str) -> dict[str, Any]:
        # Transaction fetches are the dominant request volume. Avoid globally spacing
        # each tx request so worker concurrency can actually improve throughput.
        result = self._request_json(client, f"/tx/{txid}", throttle=False)
        return dict(result or {})

    def _get_transaction_with_retries(self, client: httpx.Client, txid: str) -> dict[str, Any]:
        max_attempts = max(1, self.block_fetch_max_retries + 1)
        for attempt in range(1, max_attempts + 1):
            try:
                return self._get_transaction(client, txid)
            except Exception as exc:
                if not self._is_rate_limited_error(exc) or attempt >= max_attempts:
                    raise
                wait_seconds = self.block_retry_backoff_seconds * attempt
                log.warning(
                    "btc.tx_rate_limited txid=%s attempt=%s/%s wait_seconds=%.2f",
                    txid,
                    attempt,
                    max_attempts,
                    wait_seconds,
                )
                self._sleep(wait_seconds)

    def _get_block_txids_with_retries(self, client: httpx.Client, block_hash: str) -> list[str]:
        max_attempts = max(1, self.block_fetch_max_retries + 1)
        for attempt in range(1, max_attempts + 1):
            try:
                return self._get_block_txids(client, block_hash)
            except Exception as exc:
                if not self._is_rate_limited_error(exc) or attempt >= max_attempts:
                    raise
                wait_seconds = self.block_retry_backoff_seconds * attempt
                log.warning(
                    "btc.rate_limited block_hash=%s attempt=%s/%s wait_seconds=%.2f",
                    block_hash,
                    attempt,
                    max_attempts,
                    wait_seconds,
                )
                self._sleep(wait_seconds)

    def _get_btc_usd_price(self, client: httpx.Client) -> float:
        now = time.monotonic()
        if (
            self._cached_btc_usd is not None
            and self._cached_btc_usd_monotonic is not None
            and (now - self._cached_btc_usd_monotonic) < self.price_cache_ttl_seconds
        ):
            return self._cached_btc_usd

        if is_price_lookup_backoff_active(now):
            if self._cached_btc_usd is not None:
                return self._cached_btc_usd
            return self.btc_usd_fallback

        try:
            response = client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin", "vs_currencies": "usd"},
            )
            response.raise_for_status()
            price = float((response.json().get("bitcoin") or {}).get("usd"))
            if price > 0:
                register_price_lookup_backoff_success()
                self._cached_btc_usd = price
                self._cached_btc_usd_monotonic = now
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
                            "btc.price_lookup_rate_limited global_backoff_seconds=%.2f retry_after_seconds=%.2f err=%s",
                            backoff_seconds,
                            retry_after_seconds,
                            exc,
                        )
                    return self._cached_btc_usd if self._cached_btc_usd is not None else self.btc_usd_fallback
            if isinstance(exc, httpx.RequestError):
                backoff_seconds = activate_price_lookup_backoff()
                if register_price_lookup_backoff_failure():
                    log.warning(
                        "btc.price_lookup_request_failed global_backoff_seconds=%.2f err=%s",
                        backoff_seconds,
                        exc,
                    )
                return self._cached_btc_usd if self._cached_btc_usd is not None else self.btc_usd_fallback
            log.warning("btc.price_lookup_failed err=%s", exc)

        if self._cached_btc_usd is not None:
            return self._cached_btc_usd
        return self.btc_usd_fallback

    def fetch(self) -> list[CanonicalItem]:
        state = BtcState.load(self.state_path)
        out: list[CanonicalItem] = []

        with httpx.Client(timeout=self.timeout, headers={"User-Agent": "scraper-bot/0.1"}) as client:
            try:
                now_block = self._get_tip_block_height(client)
            except Exception as exc:
                log.warning("btc.now_block_failed err=%s", exc)
                return []

            btc_usd = self._get_btc_usd_price(client)
            if state.last_scanned_block is None:
                # Match other chains: on a cold start, begin at tip and avoid historical backfill.
                start_block = now_block
            else:
                start_block = min(now_block, state.last_scanned_block + 1)

            min_allowed_start_block = max(0, now_block - self.max_blocks_per_cycle + 1)
            if start_block < min_allowed_start_block:
                log.warning(
                    "btc.scan_window_clamped start_block=%s clamped_start_block=%s now_block=%s max_blocks_per_cycle=%s",
                    start_block,
                    min_allowed_start_block,
                    now_block,
                    self.max_blocks_per_cycle,
                )
                start_block = min_allowed_start_block

            cycle_deadline = time.monotonic() + self.cycle_time_budget_seconds
            last_completed_block = state.last_scanned_block

            for block_num in range(start_block, now_block + 1):
                if time.monotonic() >= cycle_deadline:
                    log.warning(
                        "btc.cycle_time_budget_reached cycle_budget_seconds=%s now_block=%s last_completed_block=%s",
                        self.cycle_time_budget_seconds,
                        now_block,
                        last_completed_block,
                    )
                    break
                try:
                    block_hash = self._get_block_hash_by_height(client, block_num)
                    txids = self._get_block_txids_with_retries(client, block_hash)
                except Exception as exc:
                    log.warning("btc.block_fetch_failed block=%s err=%s", block_num, exc)
                    continue

                txs_by_txid: dict[str, dict[str, Any]] = {}
                with ThreadPoolExecutor(max_workers=self.parallel_tx_fetch_workers) as executor:
                    future_to_txid = {
                        executor.submit(self._get_transaction_with_retries, client, txid): txid
                        for txid in txids
                    }
                    had_tx_fetch_failure = False
                    for future in as_completed(future_to_txid):
                        txid = future_to_txid[future]
                        try:
                            txs_by_txid[txid] = future.result()
                        except Exception as exc:
                            had_tx_fetch_failure = True
                            log.warning("btc.tx_fetch_failed txid=%s err=%s", txid, exc)

                if had_tx_fetch_failure:
                    log.warning(
                        "btc.block_incomplete_due_to_tx_fetch_failure block=%s tx_count=%s fetched=%s",
                        block_num,
                        len(txids),
                        len(txs_by_txid),
                    )
                    # Do not advance state for partially-fetched blocks. This guarantees
                    # no transaction loss at the cost of retrying the block next cycle.
                    break

                for txid in txids:
                    tx = txs_by_txid.get(txid)
                    if tx is None:
                        continue

                    status = tx.get("status") or {}
                    block_time = status.get("block_time")
                    published_at = datetime.fromtimestamp(block_time, tz=timezone.utc) if block_time else None

                    input_addresses: set[str] = set()
                    for vin in tx.get("vin") or []:
                        prevout = vin.get("prevout") or {}
                        in_addr = self._normalize_address(prevout.get("scriptpubkey_address"))
                        if in_addr:
                            input_addresses.add(in_addr)

                    # Bitcoin quirk: UTXO txs fan-out to many outputs and include change outputs.
                    # We still alert on individual large outputs, with watchlist-aware thresholds.
                    for vout in tx.get("vout") or []:
                        value_sat = int(vout.get("value") or 0)
                        if value_sat <= 0:
                            continue

                        to_addr = self._normalize_address(vout.get("scriptpubkey_address"))
                        amount_btc = value_sat / SATOSHIS_PER_BTC
                        amount_usd = amount_btc * btc_usd
                        from_watched = next(
                            (self.watchlist[a] for a in input_addresses if a in self.watchlist),
                            None,
                        )
                        to_watched = self.watchlist.get(to_addr or "")

                        transfer_threshold = self.min_transfer_usd
                        if from_watched or to_watched:
                            transfer_threshold = min(self.min_transfer_usd, self.watchlist_min_transfer_usd)
                        if amount_usd < transfer_threshold:
                            continue

                        watched_labels = [w["label"] for w in (from_watched, to_watched) if w and w.get("label")]
                        title_prefix = "🎯 BTC watched-address movement" if watched_labels else "🚨 BTC major movement"

                        out.append(
                            CanonicalItem(
                                item_type="onchain_transfer",
                                title=f"{title_prefix}: ${amount_usd:,.0f} ({amount_btc:,.2f} BTC)",
                                summary=(
                                    f"BTC output >= ${transfer_threshold:,.0f}: "
                                    f"to {to_addr or 'unknown'}"
                                    + (f" | watchlist={', '.join(watched_labels)}" if watched_labels else "")
                                ),
                                url=f"https://mempool.space/tx/{txid}",
                                published_at=published_at,
                                scraped_at=_utc_now(),
                                source="btc",
                                tags=["onchain", "bitcoin", "whale"] + (["watchlist"] if watched_labels else []),
                                severity="high",
                                classification={
                                    "priority": "high",
                                    "assetClass": "crypto",
                                    "subcategory": "onchain_transfer",
                                    "tickers": ["BTC"],
                                    "topics": ["whale_movement", "onchain"] + (["watchlist"] if watched_labels else []),
                                    "regions": [],
                                },
                                data={
                                    "chain": "bitcoin",
                                    "txId": txid,
                                    "blockNumber": block_num,
                                    "blockHash": status.get("block_hash"),
                                    "from": sorted(input_addresses),
                                    "to": to_addr,
                                    "amountSats": value_sat,
                                    "amountBtc": amount_btc,
                                    "amountUsd": amount_usd,
                                    "btcUsd": btc_usd,
                                    "thresholdUsd": transfer_threshold,
                                    "isWatchlist": bool(watched_labels),
                                    "watchlistFrom": from_watched,
                                    "watchlistTo": to_watched,
                                },
                            )
                        )

                last_completed_block = block_num

            if last_completed_block is not None:
                state.last_scanned_block = last_completed_block
            state.save(self.state_path)

        return out
