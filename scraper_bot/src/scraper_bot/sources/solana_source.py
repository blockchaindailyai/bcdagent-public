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

LAMPORTS_PER_SOL = 1_000_000_000
SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
SPL_TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
BLOCK_UNAVAILABLE_ERROR_CODES = ("-32004", "-32007", "-32009")
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BASE58_INDEX = {c: i for i, c in enumerate(BASE58_ALPHABET)}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_dt_from_unix(ts: int | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


@dataclass
class SolanaState:
    last_scanned_slot: int | None

    @classmethod
    def load(cls, path: Path) -> "SolanaState":
        if not path.exists():
            return cls(last_scanned_slot=None)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            slot = data.get("last_scanned_slot")
            return cls(last_scanned_slot=int(slot) if slot is not None else None)
        except Exception:
            return cls(last_scanned_slot=None)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"last_scanned_slot": self.last_scanned_slot}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )




def _decode_base58(data: str) -> bytes:
    value = 0
    for ch in (data or "").strip():
        if ch not in BASE58_INDEX:
            raise ValueError(f"invalid_base58_char={ch}")
        value = value * 58 + BASE58_INDEX[ch]

    decoded = b"" if value == 0 else value.to_bytes((value.bit_length() + 7) // 8, "big")
    leading_zeros = 0
    for ch in data:
        if ch == "1":
            leading_zeros += 1
        else:
            break
    return (b"\x00" * leading_zeros) + decoded


def _decode_instruction_data(inst: dict[str, Any]) -> bytes:
    data = inst.get("data")
    if isinstance(data, str):
        try:
            return _decode_base58(data)
        except Exception:
            return b""

    if isinstance(data, list) and len(data) >= 2 and str(data[1]).lower() == "base64":
        import base64

        try:
            return base64.b64decode(str(data[0]))
        except Exception:
            return b""

    return b""


def _resolve_instruction_accounts(inst: dict[str, Any], account_keys: list[str]) -> list[str]:
    accounts = inst.get("accounts") or []
    out: list[str] = []
    for account in accounts:
        if isinstance(account, str):
            out.append(account)
            continue
        if isinstance(account, int) and 0 <= account < len(account_keys):
            out.append(account_keys[account])
    return out


def _extract_native_transfers(tx: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    transaction = tx.get("transaction") or {}
    message = transaction.get("message") or {}
    account_keys = _account_keys(message)

    for inst in _iter_parsed_instructions(tx):
        if _instruction_program_id(inst) != SYSTEM_PROGRAM_ID:
            continue

        parsed = inst.get("parsed") or {}
        if parsed.get("type") == "transfer":
            info = parsed.get("info") or {}
            lamports = info.get("lamports")
            if lamports is None:
                continue
            try:
                lamports_int = int(lamports)
            except (TypeError, ValueError):
                continue
            out.append(
                {
                    "from": info.get("source"),
                    "to": info.get("destination"),
                    "amount_lamports": lamports_int,
                }
            )
            continue

        raw_data = _decode_instruction_data(inst)
        accounts = _resolve_instruction_accounts(inst, account_keys)
        if len(raw_data) < 12 or len(accounts) < 2:
            continue

        opcode = int.from_bytes(raw_data[:4], "little")
        if opcode != 2:
            continue

        lamports_int = int.from_bytes(raw_data[4:12], "little")
        if lamports_int <= 0:
            continue

        out.append({"from": accounts[0], "to": accounts[1], "amount_lamports": lamports_int})
    return out


def _account_keys(message: dict[str, Any]) -> list[str]:
    keys = message.get("accountKeys") or []
    out: list[str] = []
    for key in keys:
        if isinstance(key, str):
            out.append(key)
            continue
        if isinstance(key, dict):
            pubkey = key.get("pubkey")
            if isinstance(pubkey, str):
                out.append(pubkey)
    return out


def _instruction_program_id(inst: dict[str, Any]) -> str | None:
    program_id = inst.get("programId")
    if isinstance(program_id, str) and program_id:
        return program_id
    return None


def _iter_parsed_instructions(tx: dict[str, Any]):
    transaction = tx.get("transaction") or {}
    message = transaction.get("message") or {}
    account_keys = _account_keys(message)
    for inst in message.get("instructions") or []:
        normalized_inst = dict(inst or {})
        program_id = _instruction_program_id_from_account_keys(normalized_inst, account_keys)
        if program_id:
            normalized_inst["programId"] = program_id
        yield normalized_inst

    meta = tx.get("meta") or {}
    for inner in meta.get("innerInstructions") or []:
        for inst in (inner or {}).get("instructions") or []:
            normalized_inst = dict(inst or {})
            program_id = _instruction_program_id_from_account_keys(normalized_inst, account_keys)
            if program_id:
                normalized_inst["programId"] = program_id
            yield normalized_inst


def _instruction_program_id_from_account_keys(inst: dict[str, Any], account_keys: list[str]) -> str | None:
    program_id = _instruction_program_id(inst)
    if program_id:
        return program_id
    program_id_index = inst.get("programIdIndex")
    if not isinstance(program_id_index, int) or program_id_index < 0 or program_id_index >= len(account_keys):
        return None
    return account_keys[program_id_index]


def _extract_spl_token_transfers(tx: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    transaction = tx.get("transaction") or {}
    meta = tx.get("meta") or {}
    message = transaction.get("message") or {}
    keys = _account_keys(message)

    account_to_mint: dict[str, str] = {}
    account_to_decimals: dict[str, int] = {}
    for token_balance in (meta.get("preTokenBalances") or []) + (meta.get("postTokenBalances") or []):
        idx = token_balance.get("accountIndex")
        mint = token_balance.get("mint")
        if not isinstance(idx, int) or idx < 0 or idx >= len(keys):
            continue
        if not isinstance(mint, str) or not mint:
            continue
        account_pubkey = keys[idx]
        account_to_mint[account_pubkey] = mint
        ui_info = token_balance.get("uiTokenAmount") or {}
        try:
            account_to_decimals[account_pubkey] = int(ui_info.get("decimals") or 0)
        except (TypeError, ValueError):
            continue

    for inst in _iter_parsed_instructions(tx):
        if _instruction_program_id(inst) != SPL_TOKEN_PROGRAM_ID:
            continue

        source = None
        destination = None
        mint = None
        raw_amount = None
        decimals = None

        parsed = inst.get("parsed") or {}
        inst_type = parsed.get("type")
        if inst_type in {"transfer", "transferChecked"}:
            info = parsed.get("info") or {}
            source = info.get("source")
            destination = info.get("destination")
            raw_amount = info.get("amount")
            token_amount = info.get("tokenAmount") or {}
            if raw_amount is None:
                raw_amount = token_amount.get("amount")
            if token_amount:
                try:
                    decimals = int(token_amount.get("decimals"))
                except (TypeError, ValueError):
                    decimals = None
            mint = info.get("mint")
        else:
            raw_data = _decode_instruction_data(inst)
            accounts = _resolve_instruction_accounts(inst, keys)
            if len(raw_data) >= 9 and len(accounts) >= 3 and raw_data[0] == 3:
                source = accounts[0]
                destination = accounts[1]
                raw_amount = int.from_bytes(raw_data[1:9], "little")
                mint = account_to_mint.get(source) or account_to_mint.get(destination)
            elif len(raw_data) >= 10 and len(accounts) >= 4 and raw_data[0] == 12:
                source = accounts[0]
                mint = accounts[1]
                destination = accounts[2]
                raw_amount = int.from_bytes(raw_data[1:9], "little")
                decimals = int(raw_data[9])

        if not source or not destination:
            continue

        if decimals is None:
            decimals = account_to_decimals.get(source)
        if decimals is None:
            decimals = account_to_decimals.get(destination)
        if decimals is None:
            decimals = 0

        mint = mint or account_to_mint.get(source) or account_to_mint.get(destination)
        if not mint:
            continue

        try:
            amount_raw_int = int(raw_amount)
        except (TypeError, ValueError):
            continue
        if amount_raw_int <= 0:
            continue

        out.append(
            {
                "from": source,
                "to": destination,
                "mint": mint,
                "amount_raw": amount_raw_int,
                "decimals": int(decimals),
            }
        )
    return out


class SolanaSource(SourceScraper):
    @property
    def name(self) -> str:
        return "solana"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.rpc_url = self.settings.solana_rpc_url.strip()
        self.state_path = Path(self.settings.solana_state_path)
        self.timeout = httpx.Timeout(int(self.settings.solana_http_timeout_seconds))
        self.lookback_slots = int(self.settings.solana_lookback_slots_on_boot)
        self.min_transfer_usd = float(self.settings.solana_min_transfer_usd)
        self.watchlist_min_transfer_usd = float(self.settings.solana_watchlist_min_transfer_usd)
        self.watchlist = self._load_watchlist(Path(self.settings.solana_watchlist_path))
        self.sol_usd_fallback = float(self.settings.solana_sol_usd_fallback)
        # Allow operators to tune request spacing (including 0) for faster catch-up on high-slot-throughput chains.
        self.slot_request_delay_seconds = max(0.0, float(self.settings.solana_slot_request_delay_seconds))
        self.slot_fetch_max_retries = int(self.settings.solana_slot_fetch_max_retries)
        self.slot_retry_backoff_seconds = float(self.settings.solana_slot_retry_backoff_seconds)
        self.max_slots_per_cycle = max(1, int(self.settings.solana_max_slots_per_cycle))
        self.parallel_block_fetch_workers = max(1, int(self.settings.solana_parallel_block_fetch_workers))
        self._fetch_executor: ThreadPoolExecutor | None = None
        if self.parallel_block_fetch_workers > 1:
            self._fetch_executor = ThreadPoolExecutor(
                max_workers=self.parallel_block_fetch_workers,
                thread_name_prefix="solana-block-fetch",
            )
        self._last_slot_request_monotonic: float | None = None
        self._rpc_id = 0
        self._cached_sol_usd: float | None = None
        self._cached_sol_usd_monotonic: float | None = None
        self._spl_price_cache: dict[str, float] = {}
        self._bootstrapped = False

    @staticmethod
    def _parse_retry_after_seconds(response: httpx.Response | None) -> float | None:
        if response is None:
            return None
        retry_after = response.headers.get("retry-after")
        if retry_after is None:
            return None
        try:
            seconds = float(retry_after)
        except (TypeError, ValueError):
            return None
        return seconds if seconds > 0 else None

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
        except Exception as e:
            log.warning("solana.watchlist_load_failed path=%s err=%s", path, e)
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

    def _throttle_rpc_request(self) -> None:
        min_spacing = max(0.0, self.slot_request_delay_seconds)
        if min_spacing <= 0:
            return

        now = time.monotonic()
        if self._last_slot_request_monotonic is not None:
            elapsed = now - self._last_slot_request_monotonic
            remaining = min_spacing - elapsed
            if remaining > 0:
                self._sleep(remaining)

        self._last_slot_request_monotonic = time.monotonic()

    def _rpc(self, client: httpx.Client, method: str, params: list[object]) -> object:
        if self.parallel_block_fetch_workers <= 1:
            self._throttle_rpc_request()
        self._rpc_id += 1
        payload = {"jsonrpc": "2.0", "id": self._rpc_id, "method": method, "params": params}
        resp = client.post(self.rpc_url, json=payload)
        resp.raise_for_status()
        body = resp.json()
        if body.get("error"):
            raise RuntimeError(f"rpc_error method={method} error={body['error']}")
        return body.get("result")

    def _get_now_slot(self, client: httpx.Client) -> int:
        return int(self._rpc(client, "getSlot", [{"commitment": "confirmed"}]))

    def _get_block(self, client: httpx.Client, slot: int) -> dict[str, Any]:
        result = self._rpc(
            client,
            "getBlock",
            [
                slot,
                {
                    "commitment": "confirmed",
                    "encoding": "json",
                    "maxSupportedTransactionVersion": 0,
                    "transactionDetails": "full",
                    "rewards": False,
                },
            ],
        )
        return dict(result or {})

    def _get_block_with_retries(self, client: httpx.Client, slot: int) -> dict[str, Any]:
        max_attempts = max(1, self.slot_fetch_max_retries + 1)
        for attempt in range(1, max_attempts + 1):
            try:
                return self._get_block(client, slot)
            except httpx.HTTPStatusError as e:
                status = e.response.status_code if e.response is not None else None
                if status != 429 or attempt >= max_attempts:
                    raise
                configured_backoff = self.slot_retry_backoff_seconds * (2 ** (attempt - 1))
                retry_after_seconds = self._parse_retry_after_seconds(e.response)
                wait_seconds = max(configured_backoff, retry_after_seconds or 0.0)
                log.warning(
                    "solana.rate_limited slot=%s attempt=%s/%s wait_seconds=%.2f retry_after=%s rpc_url=%s",
                    slot,
                    attempt,
                    max_attempts,
                    wait_seconds,
                    f"{retry_after_seconds:.2f}" if retry_after_seconds is not None else "none",
                    self.rpc_url,
                )
                self._sleep(wait_seconds)

    @staticmethod
    def _is_block_unavailable_error(error: Exception) -> bool:
        message = str(error)
        return any(code in message for code in BLOCK_UNAVAILABLE_ERROR_CODES) or (
            "block not available" in message.lower()
        )

    def _get_sol_usd_price(self, client: httpx.Client) -> float:
        now = time.monotonic()
        if self._cached_sol_usd is not None and self._cached_sol_usd_monotonic is not None:
            if (now - self._cached_sol_usd_monotonic) < 300:
                return self._cached_sol_usd

        if is_price_lookup_backoff_active(now):
            if self._cached_sol_usd is not None:
                return self._cached_sol_usd
            return self.sol_usd_fallback

        try:
            resp = client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "solana", "vs_currencies": "usd"},
            )
            resp.raise_for_status()
            data = resp.json()
            price = float((data.get("solana") or {}).get("usd"))
            if price > 0:
                register_price_lookup_backoff_success()
                self._cached_sol_usd = price
                self._cached_sol_usd_monotonic = now
                return price
        except Exception as e:
            if isinstance(e, httpx.HTTPStatusError):
                status = e.response.status_code if e.response is not None else None
                if status == 429:
                    retry_after_seconds = self._parse_retry_after_seconds(e.response) or 0.0
                    backoff_seconds = activate_price_lookup_backoff(retry_after_seconds=retry_after_seconds)
                    if register_price_lookup_backoff_failure():
                        log.warning(
                            "solana.price_lookup_rate_limited global_backoff_seconds=%.2f retry_after_seconds=%.2f err=%s",
                            backoff_seconds,
                            retry_after_seconds,
                            e,
                        )
                    if self._cached_sol_usd is not None:
                        return self._cached_sol_usd
                    return self.sol_usd_fallback
            log.warning("solana.price_lookup_failed err=%s", e)
        if self._cached_sol_usd is not None:
            return self._cached_sol_usd
        return self.sol_usd_fallback

    def _get_spl_usd_price(self, client: httpx.Client, mint: str) -> float:
        mint_addr = (mint or "").strip()
        if not mint_addr:
            return 0.0
        cached = self._spl_price_cache.get(mint_addr)
        if cached is not None:
            return cached

        if is_price_lookup_backoff_active():
            return 0.0

        price = 0.0
        try:
            resp = client.get(
                "https://api.coingecko.com/api/v3/simple/token_price/solana",
                params={"contract_addresses": mint_addr, "vs_currencies": "usd"},
            )
            resp.raise_for_status()
            data = resp.json() or {}
            token_data = data.get(mint_addr) or {}
            price = float((token_data or {}).get("usd") or 0)
        except Exception as e:
            if isinstance(e, httpx.HTTPStatusError):
                status = e.response.status_code if e.response is not None else None
                if status == 429:
                    retry_after_seconds = self._parse_retry_after_seconds(e.response) or 0.0
                    backoff_seconds = activate_price_lookup_backoff(retry_after_seconds=retry_after_seconds)
                    if register_price_lookup_backoff_failure():
                        log.warning(
                            "solana.spl_price_lookup_rate_limited mint=%s global_backoff_seconds=%.2f retry_after_seconds=%.2f err=%s",
                            mint_addr,
                            backoff_seconds,
                            retry_after_seconds,
                            e,
                        )
            price = 0.0

        self._spl_price_cache[mint_addr] = max(0.0, price)
        return self._spl_price_cache[mint_addr]

    def __del__(self) -> None:
        executor = getattr(self, "_fetch_executor", None)
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def fetch(self) -> list[CanonicalItem]:
        state = SolanaState.load(self.state_path)
        out: list[CanonicalItem] = []

        with httpx.Client(timeout=self.timeout, headers={"User-Agent": "scraper-bot/0.1"}) as client:
            try:
                now_slot = self._get_now_slot(client)
            except Exception as e:
                log.warning("solana.now_slot_failed err=%s", e)
                return []

            sol_usd = self._get_sol_usd_price(client)
            if not self._bootstrapped or state.last_scanned_slot is None:
                # Cold starts should begin at the latest slot and never backfill historical slots.
                start_slot = now_slot
                self._bootstrapped = True
            else:
                start_slot = min(now_slot, state.last_scanned_slot + 1)

            min_allowed_start_slot = max(0, now_slot - self.max_slots_per_cycle + 1)
            if start_slot < min_allowed_start_slot:
                log.warning(
                    "solana.scan_window_clamped start_slot=%s clamped_start_slot=%s now_slot=%s max_slots_per_cycle=%s",
                    start_slot,
                    min_allowed_start_slot,
                    now_slot,
                    self.max_slots_per_cycle,
                )
                start_slot = min_allowed_start_slot

            slots = list(range(start_slot, now_slot + 1))
            scanned_slots = len(slots)
            blocks_by_slot: dict[int, dict[str, Any]] = {}

            if self.parallel_block_fetch_workers <= 1:
                for slot in slots:
                    try:
                        blocks_by_slot[slot] = self._get_block_with_retries(client, slot)
                    except Exception as e:
                        if self._is_block_unavailable_error(e):
                            log.debug("solana.block_skipped_or_missing slot=%s", slot)
                        else:
                            log.warning("solana.block_fetch_failed slot=%s err=%s", slot, e)
            else:
                executor = self._fetch_executor
                if executor is None:
                    executor = ThreadPoolExecutor(max_workers=self.parallel_block_fetch_workers)
                futures = {executor.submit(self._get_block_with_retries, client, slot): slot for slot in slots}
                for future in as_completed(futures):
                    slot = futures[future]
                    try:
                        blocks_by_slot[slot] = future.result()
                    except Exception as e:
                        if self._is_block_unavailable_error(e):
                            log.debug("solana.block_skipped_or_missing slot=%s", slot)
                        else:
                            log.warning("solana.block_fetch_failed slot=%s err=%s", slot, e)

            for slot in slots:
                block = blocks_by_slot.get(slot)
                if block is None:
                    continue

                block_time = block.get("blockTime")
                published_at = _to_dt_from_unix(int(block_time)) if block_time is not None else None

                for tx in block.get("transactions") or []:
                    sigs = ((tx.get("transaction") or {}).get("signatures") or [])
                    tx_sig = sigs[0] if sigs else None
                    for transfer in _extract_native_transfers(tx):
                        from_addr = transfer["from"]
                        to_addr = transfer["to"]
                        from_watched = self.watchlist.get(self._normalize_address(from_addr) or "")
                        to_watched = self.watchlist.get(self._normalize_address(to_addr) or "")
                        amount_sol = transfer["amount_lamports"] / LAMPORTS_PER_SOL
                        amount_usd = amount_sol * sol_usd

                        transfer_threshold = self.min_transfer_usd
                        if from_watched or to_watched:
                            transfer_threshold = min(self.min_transfer_usd, self.watchlist_min_transfer_usd)
                        if amount_usd < transfer_threshold:
                            continue

                        watched_labels = [w["label"] for w in (from_watched, to_watched) if w and w.get("label")]
                        watched_title_prefix = (
                            "🎯 SOL watched-address movement" if watched_labels else "🚨 SOL major movement"
                        )
                        out.append(
                            CanonicalItem(
                                item_type="onchain_transfer",
                                title=f"{watched_title_prefix}: ${amount_usd:,.0f} ({amount_sol:,.0f} SOL)",
                                summary=(
                                    f"Native SOL transfer >= ${transfer_threshold:,.0f}: "
                                    f"from {from_addr} to {to_addr}"
                                    + (f" | watchlist={', '.join(watched_labels)}" if watched_labels else "")
                                ),
                                url=f"https://solscan.io/tx/{tx_sig}" if tx_sig else None,
                                published_at=published_at,
                                scraped_at=_utc_now(),
                                source="solana",
                                tags=["onchain", "solana", "whale"] + (["watchlist"] if watched_labels else []),
                                severity="high",
                                classification={
                                    "priority": "high",
                                    "assetClass": "crypto",
                                    "subcategory": "onchain_transfer",
                                    "tickers": ["SOL"],
                                    "topics": ["whale_movement", "onchain"]
                                    + (["watchlist"] if watched_labels else []),
                                    "regions": [],
                                },
                                data={
                                    "chain": "solana",
                                    "txId": tx_sig,
                                    "slot": slot,
                                    "from": from_addr,
                                    "to": to_addr,
                                    "amountLamports": transfer["amount_lamports"],
                                    "amountSol": amount_sol,
                                    "amountUsd": amount_usd,
                                    "solUsd": sol_usd,
                                    "thresholdUsd": transfer_threshold,
                                    "isWatchlist": bool(watched_labels),
                                    "watchlistFrom": from_watched,
                                    "watchlistTo": to_watched,
                                },
                            )
                        )

                    for transfer in _extract_spl_token_transfers(tx):
                        mint = str(transfer["mint"])
                        token_usd = self._get_spl_usd_price(client, mint)
                        if token_usd <= 0:
                            continue

                        decimals = int(transfer["decimals"])
                        amount_token = transfer["amount_raw"] / (10**max(0, decimals))
                        amount_usd = amount_token * token_usd
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
                        watched_title_prefix = (
                            "🎯 SOL watched-address token movement" if watched_labels else "🚨 SOL major token movement"
                        )
                        out.append(
                            CanonicalItem(
                                item_type="onchain_transfer",
                                title=f"{watched_title_prefix}: ${amount_usd:,.0f} ({amount_token:,.0f} SPL)",
                                summary=(
                                    f"SPL token transfer >= ${transfer_threshold:,.0f}: "
                                    f"from {from_addr} to {to_addr} | mint={mint}"
                                    + (f" | watchlist={', '.join(watched_labels)}" if watched_labels else "")
                                ),
                                url=f"https://solscan.io/tx/{tx_sig}" if tx_sig else None,
                                published_at=published_at,
                                scraped_at=_utc_now(),
                                source="solana",
                                tags=["onchain", "solana", "whale", "spl", "token"]
                                + (["watchlist"] if watched_labels else []),
                                severity="high",
                                classification={
                                    "priority": "high",
                                    "assetClass": "crypto",
                                    "subcategory": "onchain_transfer",
                                    "tickers": [mint],
                                    "topics": ["whale_movement", "onchain", "spl", "token"]
                                    + (["watchlist"] if watched_labels else []),
                                    "regions": [],
                                },
                                data={
                                    "chain": "solana",
                                    "txId": tx_sig,
                                    "slot": slot,
                                    "from": from_addr,
                                    "to": to_addr,
                                    "amountRaw": transfer["amount_raw"],
                                    "amountToken": amount_token,
                                    "amountUsd": amount_usd,
                                    "tokenAddress": mint,
                                    "tokenSymbol": mint,
                                    "tokenDecimals": decimals,
                                    "tokenUsd": token_usd,
                                    "thresholdUsd": transfer_threshold,
                                    "isWatchlist": bool(watched_labels),
                                    "watchlistFrom": from_watched,
                                    "watchlistTo": to_watched,
                                    "tokenType": "spl",
                                },
                            )
                        )

            state.last_scanned_slot = now_slot
            state.save(self.state_path)
            log.debug(
                "solana.scan_done now_slot=%s start_slot=%s slots=%s major_moves=%s",
                now_slot,
                start_slot,
                scanned_slots,
                len(out),
            )

        return out
