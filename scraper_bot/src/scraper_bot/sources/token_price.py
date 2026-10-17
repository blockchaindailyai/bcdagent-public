from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from pathlib import Path
import json

import httpx

from scraper_bot.sources.price_backoff import (
    activate_price_lookup_backoff,
    is_price_lookup_backoff_active,
    register_price_lookup_backoff_failure,
    register_price_lookup_backoff_success,
)

log = logging.getLogger(__name__)

_RATE_LIMIT_BACKOFF_UNTIL: dict[str, float] = {}


def fetch_token_prices_from_dexscreener(
    client: httpx.Client,
    chain: str,
    contract_addresses: list[str],
    fallback_prices: Mapping[str, float],
    cache_path: Path | None = None,
    rate_limit_backoff_seconds: int = 180,
) -> dict[str, float]:
    """Fetch token USD prices from Dexscreener.

    This endpoint supports querying multiple tokens in one request and is less restrictive
    than CoinGecko for contract-by-contract lookups.
    """
    prices = {addr.lower(): float(price) for addr, price in fallback_prices.items()}
    prices.update(load_cached_token_prices(cache_path=cache_path, chain=chain))
    normalized_addresses = [addr.lower() for addr in contract_addresses]
    if not normalized_addresses:
        return prices

    if is_price_lookup_backoff_active():
        return prices

    backoff_until = _RATE_LIMIT_BACKOFF_UNTIL.get(chain, 0.0)
    if time.time() < backoff_until:
        remaining_seconds = backoff_until - time.time()
        log.warning(
            "token_price.dexscreener_backoff_active chain=%s remaining_seconds=%.2f",
            chain,
            remaining_seconds,
        )
        return prices

    try:
        response = client.get(
            f"https://api.dexscreener.com/tokens/v1/{chain}/" + ",".join(normalized_addresses)
        )
        if getattr(response, "status_code", None) == 429:
            retry_after_seconds = 0.0
            retry_after_header = None
            if getattr(response, "headers", None) is not None:
                retry_after_header = response.headers.get("Retry-After")
            if retry_after_header:
                try:
                    retry_after_seconds = float(retry_after_header)
                except ValueError:
                    retry_after_seconds = 0.0
            backoff_seconds = max(float(rate_limit_backoff_seconds), retry_after_seconds)
            _RATE_LIMIT_BACKOFF_UNTIL[chain] = time.time() + max(0.0, backoff_seconds)
            global_backoff_seconds = activate_price_lookup_backoff(retry_after_seconds=retry_after_seconds)
            if register_price_lookup_backoff_failure():
                log.warning(
                    "token_price.dexscreener_rate_limited chain=%s backoff_seconds=%.2f retry_after_seconds=%.2f global_backoff_seconds=%.2f",
                    chain,
                    backoff_seconds,
                    retry_after_seconds,
                    global_backoff_seconds,
                )
            return prices
        response.raise_for_status()
        register_price_lookup_backoff_success()
        payload = response.json()
        pairs = payload if isinstance(payload, list) else []

        best_prices: dict[str, tuple[float, float]] = {}
        for pair in pairs:
            if not isinstance(pair, dict):
                continue

            pair_price = pair.get("priceUsd")
            pair_liquidity = ((pair.get("liquidity") or {}).get("usd"))
            base_token = pair.get("baseToken") or {}
            quote_token = pair.get("quoteToken") or {}

            for token_key, token_info in (("base", base_token), ("quote", quote_token)):
                token_address = str((token_info or {}).get("address") or "").lower()
                if token_address not in prices:
                    continue

                token_price: float | None = None
                if token_key == "base" and pair_price is not None:
                    token_price = _to_positive_float(pair_price)
                elif token_key == "quote" and pair_price is not None:
                    base_price = _to_positive_float(pair_price)
                    if base_price:
                        token_price = 1.0 / base_price

                if not token_price:
                    continue

                liquidity = _to_positive_float(pair_liquidity) or 0.0
                previous = best_prices.get(token_address)
                if previous is None or liquidity > previous[1]:
                    best_prices[token_address] = (token_price, liquidity)

        for token_address, (token_price, _) in best_prices.items():
            prices[token_address] = token_price
        save_cached_token_prices(cache_path=cache_path, chain=chain, prices=prices)

    except Exception as e:
        if isinstance(e, httpx.RequestError):
            backoff_seconds = float(rate_limit_backoff_seconds)
            _RATE_LIMIT_BACKOFF_UNTIL[chain] = time.time() + max(0.0, backoff_seconds)
            global_backoff_seconds = activate_price_lookup_backoff(minimum_backoff_seconds=backoff_seconds)
            if register_price_lookup_backoff_failure():
                log.warning(
                    "token_price.dexscreener_request_failed chain=%s backoff_seconds=%.2f global_backoff_seconds=%.2f err=%s",
                    chain,
                    backoff_seconds,
                    global_backoff_seconds,
                    e,
                )
        log.warning("token_price.dexscreener_lookup_failed chain=%s err=%s", chain, e)

    return prices


def load_cached_token_prices(cache_path: Path | None, chain: str) -> dict[str, float]:
    if cache_path is None or not cache_path.exists():
        return {}
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("token_price.cache_load_failed path=%s err=%s", cache_path, e)
        return {}

    chain_prices = payload.get(str(chain).lower()) if isinstance(payload, dict) else None
    if not isinstance(chain_prices, dict):
        return {}

    out: dict[str, float] = {}
    for address, price in chain_prices.items():
        parsed_price = _to_positive_float(price)
        if parsed_price is None:
            continue
        out[str(address).lower()] = parsed_price
    return out


def save_cached_token_prices(cache_path: Path | None, chain: str, prices: Mapping[str, float]) -> None:
    if cache_path is None:
        return

    payload: dict[str, dict[str, float]] = {}
    if cache_path.exists():
        try:
            existing = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                for existing_chain, existing_prices in existing.items():
                    if isinstance(existing_prices, dict):
                        payload[str(existing_chain).lower()] = {
                            str(addr).lower(): float(price)
                            for addr, price in existing_prices.items()
                            if _to_positive_float(price) is not None
                        }
        except Exception as e:
            log.warning("token_price.cache_parse_failed path=%s err=%s", cache_path, e)

    payload[str(chain).lower()] = {
        str(addr).lower(): float(price)
        for addr, price in prices.items()
        if _to_positive_float(price) is not None
    }
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("token_price.cache_write_failed path=%s err=%s", cache_path, e)




def reset_token_price_backoff_for_tests() -> None:
    _RATE_LIMIT_BACKOFF_UNTIL.clear()

def _to_positive_float(value: object) -> float | None:
    try:
        out = float(value)
        if out > 0:
            return out
    except (TypeError, ValueError):
        return None
    return None
