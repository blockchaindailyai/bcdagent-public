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

DROPS_PER_XRP = 1_000_000
RIPPLE_EPOCH_OFFSET_SECONDS = 946684800
STABLE_ISSUED_CURRENCIES = {"USD", "USDT", "USDC"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ripple_time_to_utc(ripple_seconds: int | None) -> datetime | None:
    if ripple_seconds is None:
        return None
    return datetime.fromtimestamp(ripple_seconds + RIPPLE_EPOCH_OFFSET_SECONDS, tz=timezone.utc)


@dataclass
class XrpState:
    last_scanned_ledger: int | None

    @classmethod
    def load(cls, path: Path) -> "XrpState":
        if not path.exists():
            return cls(last_scanned_ledger=None)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            value = data.get("last_scanned_ledger")
            return cls(last_scanned_ledger=int(value) if value is not None else None)
        except Exception:
            return cls(last_scanned_ledger=None)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"last_scanned_ledger": self.last_scanned_ledger}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _extract_successful_payments(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tx_entry in ledger.get("transactions") or []:
        # XRPL servers can return expanded tx objects directly (with `metaData`)
        # or nested `{"tx": ..., "meta": ...}` entries, depending on endpoint flavor.
        tx = (tx_entry.get("tx") if isinstance(tx_entry, dict) else None) or tx_entry or {}
        meta = {}
        if isinstance(tx_entry, dict):
            meta = tx_entry.get("meta") or tx_entry.get("metaData") or {}
        if not meta and isinstance(tx, dict):
            meta = tx.get("meta") or tx.get("metaData") or {}

        if tx.get("TransactionType") != "Payment":
            continue
        if meta.get("TransactionResult") != "tesSUCCESS":
            continue

        # XRPL quirk: Amount can be issuer object or drops string. delivered_amount in meta
        # is the canonical settled amount for partial payments.
        delivered = meta.get("delivered_amount")
        if delivered in (None, "unavailable"):
            delivered = tx.get("Amount")

        out.append(
            {
                "txid": tx.get("hash"),
                "from": tx.get("Account"),
                "to": tx.get("Destination"),
                "destination_tag": tx.get("DestinationTag"),
                "source_tag": tx.get("SourceTag"),
                "amount": delivered,
            }
        )
    return out


class XrpSource(SourceScraper):
    @property
    def name(self) -> str:
        return "xrp"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.rpc_url = self.settings.xrp_rpc_url.strip()
        self.state_path = Path(self.settings.xrp_state_path)
        self.timeout = httpx.Timeout(int(self.settings.xrp_http_timeout_seconds))
        self.lookback_ledgers = int(self.settings.xrp_lookback_ledgers_on_boot)
        self.min_transfer_usd = float(self.settings.xrp_min_transfer_usd)
        self.watchlist_min_transfer_usd = float(self.settings.xrp_watchlist_min_transfer_usd)
        self.watchlist = self._load_watchlist(Path(self.settings.xrp_watchlist_path))
        self.xrp_usd_fallback = float(self.settings.xrp_usd_fallback)
        self.price_cache_ttl_seconds = max(0, int(self.settings.xrp_price_cache_ttl_seconds))
        self.ledger_request_delay_seconds = float(self.settings.xrp_ledger_request_delay_seconds)
        self.ledger_fetch_max_retries = int(self.settings.xrp_ledger_fetch_max_retries)
        self.ledger_retry_backoff_seconds = float(self.settings.xrp_ledger_retry_backoff_seconds)
        self.max_ledgers_per_cycle = max(1, int(self.settings.xrp_max_ledgers_per_cycle))
        self.parallel_ledger_fetch_workers = max(1, int(self.settings.xrp_parallel_ledger_fetch_workers))
        self.cycle_time_budget_seconds = max(1, int(self.settings.xrp_cycle_time_budget_seconds))
        self._fetch_executor: ThreadPoolExecutor | None = None
        if self.parallel_ledger_fetch_workers > 1:
            self._fetch_executor = ThreadPoolExecutor(
                max_workers=self.parallel_ledger_fetch_workers,
                thread_name_prefix="xrp-ledger-fetch",
            )
        self._rpc_id = 0
        self._last_ledger_request_monotonic: float | None = None
        self._cached_xrp_usd: float | None = None
        self._cached_xrp_usd_monotonic: float | None = None
        self._last_cycle_budget_warning_monotonic: float | None = None
        self._last_cycle_budget_warning_lag: int | None = None
        self._cycle_budget_warning_cooldown_seconds = 300.0
        self._is_first_fetch_cycle = True

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
            log.warning("xrp.watchlist_load_failed path=%s err=%s", path, exc)
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

    def _throttle_ledger_request(self) -> None:
        min_spacing = max(0.0, self.ledger_request_delay_seconds)
        if min_spacing <= 0:
            return

        now = time.monotonic()
        if self._last_ledger_request_monotonic is not None:
            elapsed = now - self._last_ledger_request_monotonic
            remaining = min_spacing - elapsed
            if remaining > 0:
                self._sleep(remaining)

        self._last_ledger_request_monotonic = time.monotonic()

    def _rpc(self, client: httpx.Client, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._rpc_id += 1
        payload = {"method": method, "params": [params], "id": self._rpc_id, "jsonrpc": "2.0"}
        response = client.post(self.rpc_url, json=payload)
        response.raise_for_status()
        body = response.json()
        result = body.get("result") or {}
        if result.get("status") not in (None, "success"):
            raise RuntimeError(f"xrp.rpc_error method={method} result={result}")
        return result

    def _get_now_ledger_index(self, client: httpx.Client) -> int:
        result = self._rpc(client, "ledger", {"ledger_index": "validated", "transactions": False})
        return int((result.get("ledger") or {}).get("ledger_index"))

    def _get_ledger(self, client: httpx.Client, ledger_index: int) -> dict[str, Any]:
        if self.parallel_ledger_fetch_workers <= 1:
            self._throttle_ledger_request()
        result = self._rpc(
            client,
            "ledger",
            {
                "ledger_index": ledger_index,
                "transactions": True,
                "expand": True,
                "owner_funds": False,
            },
        )
        return result.get("ledger") or {}

    @staticmethod
    def _is_rate_limited_error(err: Exception) -> bool:
        if isinstance(err, httpx.HTTPStatusError):
            status = err.response.status_code if err.response is not None else None
            return status == 429

        err_text = str(err).lower()
        rate_limit_markers = (
            "rate limit",
            "too many requests",
            "slow down",
            "code': 429",
            'code": 429',
            "toobusy",
        )
        return any(marker in err_text for marker in rate_limit_markers)

    def _get_ledger_with_retries(self, client: httpx.Client, ledger_index: int) -> dict[str, Any]:
        max_attempts = max(1, self.ledger_fetch_max_retries + 1)
        for attempt in range(1, max_attempts + 1):
            try:
                return self._get_ledger(client, ledger_index)
            except Exception as exc:
                if not self._is_rate_limited_error(exc) or attempt >= max_attempts:
                    raise
                wait_seconds = self.ledger_retry_backoff_seconds * attempt
                log.warning(
                    "xrp.rate_limited ledger=%s attempt=%s/%s wait_seconds=%.2f",
                    ledger_index,
                    attempt,
                    max_attempts,
                    wait_seconds,
                )
                self._sleep(wait_seconds)

    def _effective_max_ledgers_per_cycle(self) -> int:
        # In serial mode we enforce per-request spacing, so a large static cycle window can
        # guarantee budget overrun even on healthy RPCs. Cap the scan window by budget-derived
        # capacity to avoid unavoidable lag spam.
        if self.parallel_ledger_fetch_workers <= 1 and self.ledger_request_delay_seconds > 0:
            by_spacing = max(1, int(self.cycle_time_budget_seconds / self.ledger_request_delay_seconds))
            return min(self.max_ledgers_per_cycle, by_spacing)
        return self.max_ledgers_per_cycle

    def _maybe_log_cycle_time_budget_reached(self, *, now_ledger: int, last_completed_ledger: int) -> None:
        backlog_ledgers = max(0, now_ledger - last_completed_ledger)
        now_monotonic = time.monotonic()
        should_log = False

        if self._last_cycle_budget_warning_monotonic is None:
            should_log = True
        elif (now_monotonic - self._last_cycle_budget_warning_monotonic) >= self._cycle_budget_warning_cooldown_seconds:
            should_log = True
        elif self._last_cycle_budget_warning_lag is None or backlog_ledgers >= (self._last_cycle_budget_warning_lag + 10):
            should_log = True

        self._last_cycle_budget_warning_monotonic = now_monotonic
        self._last_cycle_budget_warning_lag = backlog_ledgers

        if should_log:
            log.warning(
                "xrp.cycle_time_budget_reached cycle_budget_seconds=%s now_ledger=%s last_completed_ledger=%s backlog_ledgers=%s",
                self.cycle_time_budget_seconds,
                now_ledger,
                last_completed_ledger,
                backlog_ledgers,
            )

    def _get_xrp_usd_price(self, client: httpx.Client) -> float:
        now = time.monotonic()
        if (
            self._cached_xrp_usd is not None
            and self._cached_xrp_usd_monotonic is not None
            and (now - self._cached_xrp_usd_monotonic) < self.price_cache_ttl_seconds
        ):
            return self._cached_xrp_usd

        if is_price_lookup_backoff_active(now):
            if self._cached_xrp_usd is not None:
                return self._cached_xrp_usd
            return self.xrp_usd_fallback

        try:
            response = client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "ripple", "vs_currencies": "usd"},
            )
            response.raise_for_status()
            price = float((response.json().get("ripple") or {}).get("usd"))
            if price > 0:
                register_price_lookup_backoff_success()
                self._cached_xrp_usd = price
                self._cached_xrp_usd_monotonic = now
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
                            "xrp.price_lookup_rate_limited global_backoff_seconds=%.2f retry_after_seconds=%.2f err=%s",
                            backoff_seconds,
                            retry_after_seconds,
                            exc,
                        )
                    return self._cached_xrp_usd if self._cached_xrp_usd is not None else self.xrp_usd_fallback
            if isinstance(exc, httpx.RequestError):
                backoff_seconds = activate_price_lookup_backoff()
                if register_price_lookup_backoff_failure():
                    log.warning(
                        "xrp.price_lookup_request_failed global_backoff_seconds=%.2f err=%s",
                        backoff_seconds,
                        exc,
                    )
                return self._cached_xrp_usd if self._cached_xrp_usd is not None else self.xrp_usd_fallback
            log.warning("xrp.price_lookup_failed err=%s", exc)

        if self._cached_xrp_usd is not None:
            return self._cached_xrp_usd
        return self.xrp_usd_fallback

    def __del__(self) -> None:
        executor = getattr(self, "_fetch_executor", None)
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def fetch(self) -> list[CanonicalItem]:
        state = XrpState.load(self.state_path)
        out: list[CanonicalItem] = []

        with httpx.Client(timeout=self.timeout, headers={"User-Agent": "scraper-bot/0.1"}) as client:
            try:
                now_ledger = self._get_now_ledger_index(client)
            except Exception as exc:
                log.warning("xrp.now_ledger_failed err=%s", exc)
                return []

            xrp_usd = self._get_xrp_usd_price(client)
            if self._is_first_fetch_cycle:
                # On process startup, skip historical backlog and begin at the validated tip.
                start_ledger = now_ledger
            elif state.last_scanned_ledger is None:
                # Match other chains: on a cold start, begin at the latest validated ledger.
                start_ledger = now_ledger
            else:
                start_ledger = min(now_ledger, state.last_scanned_ledger + 1)

            effective_max_ledgers_per_cycle = self._effective_max_ledgers_per_cycle()
            min_allowed_start_ledger = max(1, now_ledger - effective_max_ledgers_per_cycle + 1)
            if start_ledger < min_allowed_start_ledger:
                log.warning(
                    "xrp.scan_window_clamped start_ledger=%s clamped_start_ledger=%s now_ledger=%s max_ledgers_per_cycle=%s effective_max_ledgers_per_cycle=%s",
                    start_ledger,
                    min_allowed_start_ledger,
                    now_ledger,
                    self.max_ledgers_per_cycle,
                    effective_max_ledgers_per_cycle,
                )
                start_ledger = min_allowed_start_ledger

            ledger_indexes = list(range(start_ledger, now_ledger + 1))
            ledgers_by_index: dict[int, dict[str, Any]] = {}
            cycle_deadline = time.monotonic() + self.cycle_time_budget_seconds

            if self.parallel_ledger_fetch_workers <= 1:
                for ledger_index in ledger_indexes:
                    if time.monotonic() >= cycle_deadline:
                        self._maybe_log_cycle_time_budget_reached(
                            now_ledger=now_ledger,
                            last_completed_ledger=max(ledgers_by_index) if ledgers_by_index else start_ledger - 1,
                        )
                        break
                    try:
                        ledgers_by_index[ledger_index] = self._get_ledger_with_retries(client, ledger_index)
                    except Exception as exc:
                        log.warning("xrp.ledger_fetch_failed ledger=%s err=%s", ledger_index, exc)
            else:
                executor = self._fetch_executor
                if executor is None:
                    executor = ThreadPoolExecutor(max_workers=self.parallel_ledger_fetch_workers)
                futures = {
                    executor.submit(self._get_ledger_with_retries, client, ledger_index): ledger_index
                    for ledger_index in ledger_indexes
                }
                for future in as_completed(futures):
                    if time.monotonic() >= cycle_deadline:
                        self._maybe_log_cycle_time_budget_reached(
                            now_ledger=now_ledger,
                            last_completed_ledger=max(ledgers_by_index) if ledgers_by_index else start_ledger - 1,
                        )
                        break
                    ledger_index = futures[future]
                    try:
                        ledgers_by_index[ledger_index] = future.result()
                    except Exception as exc:
                        log.warning("xrp.ledger_fetch_failed ledger=%s err=%s", ledger_index, exc)

            processed_indexes = sorted(ledgers_by_index)
            for ledger_index in processed_indexes:
                ledger = ledgers_by_index.get(ledger_index)
                if ledger is None:
                    continue

                published_at = _ripple_time_to_utc(ledger.get("close_time")) or _utc_now()

                for transfer in _extract_successful_payments(ledger):
                    from_addr = transfer["from"]
                    to_addr = transfer["to"]
                    from_watched = self.watchlist.get(self._normalize_address(from_addr) or "")
                    to_watched = self.watchlist.get(self._normalize_address(to_addr) or "")
                    transfer_threshold = self.min_transfer_usd
                    if from_watched or to_watched:
                        transfer_threshold = min(self.min_transfer_usd, self.watchlist_min_transfer_usd)

                    amount_data = transfer["amount"]
                    if isinstance(amount_data, str):
                        amount_xrp = int(amount_data) / DROPS_PER_XRP
                        amount_usd = amount_xrp * xrp_usd
                        symbol = "XRP"
                        token_class = "native"
                        token_descriptor = f"{amount_xrp:,.0f} XRP"
                    elif isinstance(amount_data, dict):
                        currency = str(amount_data.get("currency") or "UNKNOWN")
                        symbol = currency
                        token_class = "issued_currency"
                        amount_value = float(amount_data.get("value") or 0.0)
                        amount_usd = amount_value if currency.upper() in STABLE_ISSUED_CURRENCIES else 0.0
                        token_descriptor = f"{amount_value:,.0f} {currency}"
                    else:
                        continue

                    if amount_usd < transfer_threshold:
                        continue

                    watched_labels = [w["label"] for w in (from_watched, to_watched) if w and w.get("label")]
                    watched_prefix = "🎯 XRP watched-address movement" if watched_labels else "🚨 XRP major movement"

                    out.append(
                        CanonicalItem(
                            item_type="onchain_transfer",
                            title=f"{watched_prefix}: ${amount_usd:,.0f} ({token_descriptor})",
                            summary=(
                                f"XRP Ledger payment >= ${transfer_threshold:,.0f}: from {from_addr} to {to_addr}"
                                + (f" | watchlist={', '.join(watched_labels)}" if watched_labels else "")
                            ),
                            url=(
                                f"https://livenet.xrpl.org/transactions/{transfer.get('txid')}/detailed"
                                if transfer.get("txid")
                                else None
                            ),
                            published_at=published_at,
                            scraped_at=_utc_now(),
                            source=self.name,
                            tags=["onchain", self.name, "whale"] + (["watchlist"] if watched_labels else []),
                            severity="high",
                            classification={
                                "priority": "high",
                                "assetClass": "crypto",
                                "subcategory": "onchain_transfer",
                                "tickers": [symbol],
                                "topics": ["whale_movement", "onchain", token_class]
                                + (["watchlist"] if watched_labels else []),
                                "regions": [],
                            },
                            data={
                                "chain": self.name,
                                "txId": transfer.get("txid"),
                                "ledgerIndex": ledger_index,
                                "from": from_addr,
                                "to": to_addr,
                                "destinationTag": transfer.get("destination_tag"),
                                "sourceTag": transfer.get("source_tag"),
                                "amount": amount_data,
                                "amountUsd": amount_usd,
                                "xrpUsd": xrp_usd,
                                "thresholdUsd": transfer_threshold,
                                "isWatchlist": bool(watched_labels),
                                "watchlistFrom": from_watched,
                                "watchlistTo": to_watched,
                            },
                        )
                    )

            if processed_indexes:
                state.last_scanned_ledger = processed_indexes[-1]
            state.save(self.state_path)
            self._is_first_fetch_cycle = False

        return out
