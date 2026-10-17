from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from scraper_bot.core.config import get_settings
from scraper_bot.schema.canonical import CanonicalItem
from scraper_bot.sources.base import SourceScraper

log = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class HyperliquidState:
    seen_trade_ids: list[int]

    @classmethod
    def load(cls, path: Path, max_seen: int) -> "HyperliquidState":
        if not path.exists():
            return cls(seen_trade_ids=[])

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            values = payload.get("seen_trade_ids") or []
            normalized = [int(v) for v in values]
            if max_seen > 0:
                normalized = normalized[-max_seen:]
            return cls(seen_trade_ids=normalized)
        except Exception:
            return cls(seen_trade_ids=[])

    def prune(self, *, max_seen: int) -> None:
        if max_seen > 0:
            self.seen_trade_ids = self.seen_trade_ids[-max_seen:]
        else:
            self.seen_trade_ids = []

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"seen_trade_ids": self.seen_trade_ids}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class HyperliquidSource(SourceScraper):
    @property
    def name(self) -> str:
        return "hyperliquid"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.api_base = self.settings.hyperliquid_api_base.rstrip("/")
        self.timeout = httpx.Timeout(int(self.settings.hyperliquid_http_timeout_seconds))
        self.state_path = Path(self.settings.hyperliquid_state_path)
        self.market_universe = [m.strip().upper() for m in self.settings.hyperliquid_markets.split(",") if m.strip()]
        self.trade_limit_per_market = max(1, int(self.settings.hyperliquid_trade_limit_per_market))
        self.major_trade_min_usd = float(self.settings.hyperliquid_major_trade_min_usd)
        self.max_seen_trade_ids = max(0, int(self.settings.hyperliquid_max_seen_trade_ids))

    def _fetch_recent_trades(self, client: httpx.Client, coin: str) -> list[dict[str, Any]]:
        response = client.post(
            f"{self.api_base}/info",
            json={"type": "recentTrades", "coin": coin},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            return []
        return payload[: self.trade_limit_per_market]

    def fetch(self) -> list[CanonicalItem]:
        state = HyperliquidState.load(self.state_path, self.max_seen_trade_ids)
        seen_set = set(state.seen_trade_ids)
        now = _utc_now()

        items: list[CanonicalItem] = []

        with httpx.Client(timeout=self.timeout, headers={"User-Agent": "scraper-bot/0.1"}) as client:
            for market in self.market_universe:
                try:
                    trades = self._fetch_recent_trades(client, market)
                except Exception as exc:
                    log.warning("hyperliquid.fetch_failed market=%s err=%s", market, exc)
                    continue

                for trade in trades:
                    try:
                        tid = int(trade.get("tid"))
                        if tid in seen_set:
                            continue

                        price = float(trade.get("px"))
                        size = float(trade.get("sz"))
                        notional_usd = price * size
                        users = [str(u).lower() for u in (trade.get("users") or []) if u]
                        traded_at_ms = int(trade.get("time"))
                        traded_at = datetime.fromtimestamp(traded_at_ms / 1000, tz=timezone.utc)
                        side = str(trade.get("side") or "?")

                        is_major = notional_usd >= self.major_trade_min_usd
                        items.append(
                            CanonicalItem(
                                item_type="onchain_trade",
                                title=f"Hyperliquid {market} trade (${notional_usd:,.0f})",
                                summary=f"{market} trade {side} of {size} at ${price:,.2f} (~${notional_usd:,.0f}).",
                                scraped_at=now,
                                published_at=traded_at,
                                source="hyperliquid",
                                tags=["hyperliquid", "perps", "recent-trade", market.lower()]
                                + (["major-trade"] if is_major else []),
                                severity="high" if is_major else "low",
                                classification={
                                    "priority": "normal",
                                    "assetClass": "crypto",
                                    "subcategory": "defi",
                                    "tickers": [market],
                                    "topics": ["derivatives", "flow"],
                                    "regions": [],
                                },
                                data={
                                    "market": market,
                                    "tradeId": tid,
                                    "side": side,
                                    "price": price,
                                    "size": size,
                                    "notionalUsd": notional_usd,
                                    "txHash": str(trade.get("hash") or ""),
                                    "users": users,
                                    "exchange": "hyperliquid",
                                    "kind": "recent_trade",
                                    "isMajor": is_major,
                                },
                            )
                        )

                        state.seen_trade_ids.append(tid)
                        seen_set.add(tid)
                    except Exception:
                        continue

        state.prune(max_seen=self.max_seen_trade_ids)
        state.save(self.state_path)
        return items
