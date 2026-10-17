from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

UA = {"user-agent": "Mozilla/5.0 (compatible; BCDAgent/1.0)"}
BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# Well-known programs and entities to seed the watchlist.
CURATED_ADDRESSES: list[dict[str, Any]] = [
    {"address": "11111111111111111111111111111111", "label": "System Program", "category": "program", "source": ["curated"]},
    {"address": "ComputeBudget111111111111111111111111111111", "label": "Compute Budget Program", "category": "program", "source": ["curated"]},
    {"address": "Vote111111111111111111111111111111111111111", "label": "Vote Program", "category": "program", "source": ["curated"]},
    {"address": "Stake11111111111111111111111111111111111111", "label": "Stake Program", "category": "program", "source": ["curated"]},
    {"address": "AddressLookupTab1e1111111111111111111111111", "label": "Address Lookup Table Program", "category": "program", "source": ["curated"]},
    {"address": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "label": "SPL Token Program", "category": "program", "source": ["curated"]},
    {"address": "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL", "label": "Associated Token Account Program", "category": "program", "source": ["curated"]},
    {"address": "TokenzQdYUNz9JvxcxVYf6JJM6M4dcT6M8zhm2n2qk1", "label": "Token-2022 Program", "category": "program", "source": ["curated"]},
    {"address": "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr", "label": "Memo Program", "category": "program", "source": ["curated"]},
    {"address": "So11111111111111111111111111111111111111112", "label": "Wrapped SOL Mint", "category": "native_wrapped", "source": ["curated"]},
    {"address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "label": "USDC Mint", "category": "stablecoin", "source": ["curated"]},
    {"address": "Es9vMFrzaCERmJfrF4H2xj9KTi27C3BZfyqabuX41sJL", "label": "USDT Mint", "category": "stablecoin", "source": ["curated"]},
    {"address": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", "label": "Jupiter Mint", "category": "major_project", "source": ["curated"]},
    {"address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6V9LynLQzK5B1pPB", "label": "Bonk Mint", "category": "memecoin", "source": ["curated"]},
    {"address": "7vfCXTUXx5WQ9xQpimeJVFRCGDpa2BkLomPvKgJo6ao", "label": "Wormhole ETH Mint", "category": "wrapped_asset", "source": ["curated"]},
    {"address": "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E", "label": "Wrapped BTC Mint", "category": "wrapped_asset", "source": ["curated"]},
    {"address": "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5n8b8R8RF", "label": "Jupiter v6 Program", "category": "dex", "source": ["curated"]},
    {"address": "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc", "label": "Orca Whirlpools", "category": "dex", "source": ["curated"]},
    {"address": "CAMMCzo5YL8w4VFF8KVHrK22GGUQ7hPqk8m4LiV6hK", "label": "Raydium CLMM", "category": "dex", "source": ["curated"]},
    {"address": "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9Gbe8J2kQ9b6", "label": "Raydium AMM", "category": "dex", "source": ["curated"]},
    {"address": "srmqPvymJeFKQ4zGQed1GFppgk6xH9w8CZ6nPSJxAo", "label": "OpenBook Program", "category": "dex", "source": ["curated"]},
    {"address": "worm2ZoG2kUd4vFXhvjh93UUH596ayRfgQ2MgjNMTth", "label": "Wormhole Core Program", "category": "bridge", "source": ["curated"]},
    {"address": "wormDTUJ6AWPNvk59vGQbDvGJmqbDTdgWgAqcLBCgUb", "label": "Wormhole Token Bridge", "category": "bridge", "source": ["curated"]},
    {"address": "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s", "label": "Metaplex Metadata Program", "category": "nft", "source": ["curated"]},
    {"address": "M2mx93ekt1bxTL58FmS6rWCYpgGN7DDDeWGPAHDq6La", "label": "Magic Eden", "category": "nft_marketplace", "source": ["curated"]},
]


def valid_address(address: str) -> bool:
    return bool(BASE58_RE.match(address))


def dedupe(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in entries:
        address = str(row.get("address") or "").strip()
        if not valid_address(address):
            continue
        key = address
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def coingecko_get(client: httpx.Client, path: str, params: dict[str, Any]) -> Any:
    # Respect free-tier rate limits: sparse requests + explicit 429 handling.
    base_wait = 1.6
    for attempt in range(1, 6):
        time.sleep(base_wait)
        resp = client.get(f"https://api.coingecko.com/api/v3{path}", params=params, headers=UA, timeout=40)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp.json()

        retry_after = resp.headers.get("Retry-After")
        wait = base_wait * (2 ** attempt)
        if retry_after:
            try:
                wait = max(wait, float(retry_after))
            except ValueError:
                pass
        time.sleep(wait)

    resp.raise_for_status()
    return resp.json()


def fetch_top_market_coin_ids(client: httpx.Client, pages: int = 5, per_page: int = 250) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for page in range(1, pages + 1):
        rows = coingecko_get(
            client,
            "/coins/markets",
            {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": per_page,
                "page": page,
                "sparkline": "false",
                "price_change_percentage": "24h",
            },
        )
        if not rows:
            break
        for row in rows:
            coin_id = str(row.get("id") or "")
            if coin_id and coin_id not in seen:
                seen.add(coin_id)
                ids.append(coin_id)
    return ids


def fetch_solana_platform_map(client: httpx.Client) -> dict[str, str]:
    rows = coingecko_get(client, "/coins/list", {"include_platform": "true"})
    out: dict[str, str] = {}
    for row in rows:
        coin_id = str(row.get("id") or "")
        platforms = row.get("platforms") or {}
        sol_address = str(platforms.get("solana") or "").strip()
        if coin_id and sol_address and valid_address(sol_address):
            out[coin_id] = sol_address
    return out


def build_token_watchlist(top_ids: list[str], sol_platform_by_id: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rank, coin_id in enumerate(top_ids, start=1):
        address = sol_platform_by_id.get(coin_id)
        if not address:
            continue
        rows.append(
            {
                "address": address,
                "label": f"{coin_id} token mint",
                "category": "coingecko_top_market_token",
                "coingecko_id": coin_id,
                "market_cap_rank": rank,
                "source": ["coingecko_markets", "coingecko_platform_mapping"],
                "verify_url": f"https://www.coingecko.com/en/coins/{coin_id}",
            }
        )
    return rows


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    watchlist_path = repo_root / "scraper_bot" / "config" / "solana_watchlist.json"
    report_path = repo_root / "solana_addresses_report.md"

    with httpx.Client(follow_redirects=True) as client:
        top_ids = fetch_top_market_coin_ids(client, pages=2, per_page=250)
        sol_platform_by_id = fetch_solana_platform_map(client)

    market_rows = build_token_watchlist(top_ids, sol_platform_by_id)
    curated_rows = [dict(row) for row in CURATED_ADDRESSES]
    combined = dedupe(curated_rows + market_rows)

    watchlist_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    meta = {
        "generated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "sources": [
            "https://api.coingecko.com/api/v3/coins/markets",
            "https://api.coingecko.com/api/v3/coins/list?include_platform=true",
            "curated_well_known_solana_programs_and_entities",
        ],
        "counts": {
            "curated_seed_rows": len(curated_rows),
            "coingecko_market_rows": len(market_rows),
            "watchlist_rows": len(combined),
        },
    }

    report = "\n".join(
        [
            "# Solana combined dataset for scraper bot",
            "",
            "Machine-readable sections are enclosed between `BEGIN_*` / `END_*` markers as JSON arrays.",
            "",
            "BEGIN_META_JSON",
            json.dumps(meta, ensure_ascii=False),
            "END_META_JSON",
            "",
            "BEGIN_CURATED_ADDRESSES_JSON",
            json.dumps(curated_rows, ensure_ascii=False),
            "END_CURATED_ADDRESSES_JSON",
            "",
            "BEGIN_COINGECKO_TOP_SOLANA_TOKENS_JSON",
            json.dumps(market_rows, ensure_ascii=False),
            "END_COINGECKO_TOP_SOLANA_TOKENS_JSON",
            "",
        ]
    )
    report_path.write_text(report, encoding="utf-8")

    print(f"Wrote {watchlist_path} rows={len(combined)}")
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
