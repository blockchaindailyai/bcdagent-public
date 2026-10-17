from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

UA = {"user-agent": "Mozilla/5.0 (compatible; BCDAgent/1.0)"}
RE_ADDR = re.compile(r"0x[a-fA-F0-9]{40}")


HARD_TAGGED = [
    {"address": "0xf977814e90da44bfa03b6295a0616a897441acec", "label": "Binance 8", "category": "exchange"},
    {"address": "0x28c6c06298d514db089934071355e5743bf21d60", "label": "Binance 14", "category": "exchange"},
    {"address": "0x564286362092d8e7936f0549571a803b203aaced", "label": "Binance 17", "category": "exchange"},
    {"address": "0x21a31ee1afc51d94c2efccaa2092ad1028285549", "label": "Binance 15", "category": "exchange"},
    {"address": "0x4e9ce36e442e55ecd9025b9a6e0d88485d628a67", "label": "Binance 7", "category": "exchange"},
    {"address": "0x3f5ce5fbfe3e9af3971d0efccecac660d25f50", "label": "Binance 4", "category": "exchange"},
    {"address": "0x742d35cc6634c0532925a3b844bc454e4438f44e", "label": "Bitfinex", "category": "exchange"},
    {"address": "0xdc76cd25977e0a5ae17155770273ad58648900d3", "label": "Kraken 7", "category": "exchange"},
    {"address": "0x503828976d22510aad0201ac7ec88293211d23da", "label": "Coinbase 10", "category": "exchange"},
    {"address": "0x7be8076f4ea4a4ad08075c2508e481d6c946d12b", "label": "OpenSea: Seaport", "category": "nft_marketplace"},
    {"address": "0x0000000000000000000000000000000000000000", "label": "Null Address", "category": "system"},
    {"address": "0x000000000000000000000000000000000000dead", "label": "Burn Address", "category": "system"},
]

CURATED_PROJECTS = [
    {"id": "tether", "name": "Tether", "symbol": "USDT", "category": "stablecoin", "contract": "0xdac17f958d2ee523a2206206994597c13d831ec7"},
    {"id": "usd-coin", "name": "USD Coin", "symbol": "USDC", "category": "stablecoin", "contract": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"},
    {"id": "dai", "name": "Dai", "symbol": "DAI", "category": "stablecoin", "contract": "0x6b175474e89094c44da98b954eedeac495271d0f"},
    {"id": "ethena-usde", "name": "Ethena USDe", "symbol": "USDE", "category": "stablecoin", "contract": "0x4c9edd5852cd905f086c759e8383e09bff1e68b3"},
    {"id": "first-digital-usd", "name": "First Digital USD", "symbol": "FDUSD", "category": "stablecoin", "contract": "0xc5f0f7b66764f6ec8c8dff7ba683102295e16409"},
    {"id": "true-usd", "name": "TrueUSD", "symbol": "TUSD", "category": "stablecoin", "contract": "0x0000000000085d4780b73119b644ae5ecd22b376"},
    {"id": "frax", "name": "Frax", "symbol": "FRAX", "category": "stablecoin", "contract": "0x853d955acef822db058eb8505911ed77f175b99e"},
    {"id": "paypal-usd", "name": "PayPal USD", "symbol": "PYUSD", "category": "stablecoin", "contract": "0x6c3ea9036406852006290770bedfcaba0e23a0e8"},
    {"id": "wrapped-bitcoin", "name": "Wrapped BTC", "symbol": "WBTC", "category": "major_project", "contract": "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"},
    {"id": "wrapped-steth", "name": "Wrapped stETH", "symbol": "WSTETH", "category": "major_project", "contract": "0x7f39c581f595b53c5cb5bb8c6f4fce6ab8827270"},
    {"id": "chainlink", "name": "Chainlink", "symbol": "LINK", "category": "major_project", "contract": "0x514910771af9ca656af840dff83e8264ecf986ca"},
    {"id": "uniswap", "name": "Uniswap", "symbol": "UNI", "category": "major_project", "contract": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"},
    {"id": "aave", "name": "Aave", "symbol": "AAVE", "category": "major_project", "contract": "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9"},
    {"id": "maker", "name": "Maker", "symbol": "MKR", "category": "major_project", "contract": "0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2"},
    {"id": "the-graph", "name": "The Graph", "symbol": "GRT", "category": "major_project", "contract": "0xc944e90c64b2c07662a292be6244bdf05cda44a7"},
    {"id": "lido-dao", "name": "Lido DAO", "symbol": "LDO", "category": "major_project", "contract": "0x5a98fcbea516cf06857215779fd812ca3bef1b32"},
    {"id": "rocket-pool", "name": "Rocket Pool", "symbol": "RPL", "category": "major_project", "contract": "0xd33526068d116ce69f19a9ee46f0bd304f21a51f"},
    {"id": "ondo-finance", "name": "Ondo", "symbol": "ONDO", "category": "major_project", "contract": "0xfaba6f8e4a5e8ab82f62fe7c39859fa577269be3"},
    {"id": "shiba-inu", "name": "Shiba Inu", "symbol": "SHIB", "category": "memecoin", "contract": "0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce"},
    {"id": "pepe", "name": "Pepe", "symbol": "PEPE", "category": "memecoin", "contract": "0x6982508145454ce325ddbe47a25d4ec3d2311933"},
    {"id": "floki", "name": "Floki", "symbol": "FLOKI", "category": "memecoin", "contract": "0xcf0c122c6b73beac65e7a6b0f1f7b5b9a10d2e9e"},
    {"id": "mog-coin", "name": "Mog Coin", "symbol": "MOG", "category": "memecoin", "contract": "0xaae6cf8d90ec4f8ee0c1b4bcf9a6e5f6f8d5c658"},
    {"id": "dogelon-mars", "name": "Dogelon Mars", "symbol": "ELON", "category": "memecoin", "contract": "0x761d38e5ddf6ccf6cf7c55759d5210750b5d60f3"},
    {"id": "baby-doge-coin", "name": "Baby Doge Coin", "symbol": "BABYDOGE", "category": "memecoin", "contract": "0xac57de9c1a09fec648e93eb98875b212db0d460b"}
]


def dedupe(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out = []
    for e in entries:
        key = (str(e.get("address", "")).lower(), str(e.get("category", "")), str(e.get("label", "")))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def fetch_top_eth_addresses(client: httpx.Client) -> list[dict[str, Any]]:
    html = client.get("https://etherscan.io/accounts?ps=100", headers=UA, timeout=40).text
    addrs = []
    seen = set()
    for addr in RE_ADDR.findall(html):
        a = addr.lower()
        if a in seen:
            continue
        seen.add(a)
        addrs.append(a)
        if len(addrs) == 100:
            break

    return [
        {
            "address": addr,
            "label": f"ETH top balance #{i}",
            "category": "eth_top_balance",
            "rank": i,
            "source": ["etherscan_top_accounts"],
            "verify_url": f"https://etherscan.io/address/{addr}",
        }
        for i, addr in enumerate(addrs, start=1)
    ]


def fetch_tagged_addresses(client: httpx.Client, candidates: list[str]) -> list[dict[str, Any]]:
    out = []
    for addr in candidates:
        try:
            data = client.get(f"https://api.ethplorer.io/getAddressInfo/{addr}", params={"apiKey": "freekey"}, timeout=30).json()
        except Exception:
            continue
        name = data.get("name") or ((data.get("contractInfo") or {}).get("name"))
        if not name:
            continue
        out.append(
            {
                "address": addr,
                "label": str(name),
                "category": "etherscan_like_tag",
                "source": ["ethplorer_address_info"],
                "verify_url": f"https://etherscan.io/address/{addr}",
            }
        )
    return out


def fetch_project_holders(client: httpx.Client, projects: list[dict[str, str]], top_n: int = 100) -> list[dict[str, Any]]:
    rows = []
    for p in projects:
        try:
            html = client.get(
                "https://etherscan.io/token/generic-tokenholders2",
                params={"a": p["contract"]},
                headers=UA,
                timeout=30,
            ).text
        except Exception:
            continue

        raw_addresses = [a.lower() for a in RE_ADDR.findall(html)]
        holder_addresses: list[str] = []
        seen: set[str] = set()
        contract_addr = p["contract"].lower()
        for addr in raw_addresses:
            if addr == contract_addr or addr in seen:
                continue
            seen.add(addr)
            holder_addresses.append(addr)
            if len(holder_addresses) >= top_n:
                break

        for i, addr in enumerate(holder_addresses, start=1):
            rows.append(
                {
                    "address": addr,
                    "label": f"{p['symbol']} holder #{i}",
                    "category": (
                        "project_top_holder"
                        if p["category"] == "major_project"
                        else "stablecoin_top_holder" if p["category"] == "stablecoin" else "memecoin_top_holder"
                    ),
                    "project_name": p["name"],
                    "project_symbol": p["symbol"],
                    "coingecko_id": p["id"],
                    "contract": p["contract"],
                    "holder_rank": i,
                    "source": ["etherscan_token_holders"],
                    "verify_url": f"https://etherscan.io/address/{addr}",
                }
            )
        time.sleep(0.15)
    return rows


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    report_path = repo_root / "eth_addresses_report.md"
    watchlist_path = repo_root / "scraper_bot" / "config" / "eth_watchlist.json"

    with httpx.Client(follow_redirects=True) as client:
        top_eth = fetch_top_eth_addresses(client)
        project_holders = fetch_project_holders(client, CURATED_PROJECTS, top_n=100)
        tagged_candidates = [r["address"] for r in top_eth] + [p["contract"] for p in CURATED_PROJECTS]
        tagged = fetch_tagged_addresses(client, tagged_candidates)
        if not project_holders:
            project_holders = [
                {
                    "address": p["contract"],
                    "label": f"{p['symbol']} core contract",
                    "category": "project_core_address" if p["category"] == "major_project" else "stablecoin_core_address" if p["category"] == "stablecoin" else "memecoin_core_address",
                    "project_name": p["name"],
                    "project_symbol": p["symbol"],
                    "coingecko_id": p["id"],
                    "contract": p["contract"],
                    "holder_rank": 0,
                    "source": ["curated_project_contract"],
                    "verify_url": f"https://etherscan.io/address/{p['contract']}",
                }
                for p in CURATED_PROJECTS
            ]

    tagged = dedupe(tagged + [dict(item, source=["curated_tagged"]) for item in HARD_TAGGED])
    top_eth = dedupe(top_eth)
    project_holders = dedupe(project_holders)
    combined = dedupe(tagged + top_eth + project_holders)

    watchlist_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    meta = {
        "generated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "sources": [
            "https://etherscan.io/accounts?ps=100",
            "https://api.ethplorer.io/getAddressInfo/<address>?apiKey=freekey",
            "https://etherscan.io/token/generic-tokenholders2?a=<contract>",
        ],
        "counts": {
            "tagged_addresses": len(tagged),
            "top_eth_addresses": len(top_eth),
            "projects_selected": len(CURATED_PROJECTS),
            "project_holder_rows": len(project_holders),
            "watchlist_rows": len(combined),
        },
    }

    report = "\n".join(
        [
            "# Ethereum combined dataset for scraper bot",
            "",
            "Machine-readable sections are enclosed between `BEGIN_*` / `END_*` markers as JSON arrays.",
            "",
            "BEGIN_META_JSON",
            json.dumps(meta, ensure_ascii=False),
            "END_META_JSON",
            "",
            "BEGIN_TAGGED_ADDRESSES_JSON",
            json.dumps(tagged, ensure_ascii=False),
            "END_TAGGED_ADDRESSES_JSON",
            "",
            "BEGIN_TOP_ETH_ADDRESSES_JSON",
            json.dumps(top_eth, ensure_ascii=False),
            "END_TOP_ETH_ADDRESSES_JSON",
            "",
            "BEGIN_PROJECTS_JSON",
            json.dumps(CURATED_PROJECTS, ensure_ascii=False),
            "END_PROJECTS_JSON",
            "",
            "BEGIN_PROJECT_TOP_HOLDERS_JSON",
            json.dumps(project_holders, ensure_ascii=False),
            "END_PROJECT_TOP_HOLDERS_JSON",
            "",
        ]
    )
    report_path.write_text(report, encoding="utf-8")
    print(f"Wrote {watchlist_path} rows={len(combined)}")
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
