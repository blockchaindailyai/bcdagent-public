from __future__ import annotations

from collections.abc import Iterable

from scraper_bot.sources.bnb_source import BnbSource
from scraper_bot.sources.btc_source import BtcSource
from scraper_bot.sources.eth_source import EthSource
from scraper_bot.sources.hyperliquid_source import HyperliquidSource
from scraper_bot.sources.macro_source import MacroSource
from scraper_bot.sources.polymarket_source import PolymarketSource
from scraper_bot.sources.rss_source import RssSource
from scraper_bot.sources.sec_source import SecSource
from scraper_bot.sources.solana_source import SolanaSource
from scraper_bot.sources.tron_source import TronSource
from scraper_bot.sources.xrp_source import XrpSource

_SOURCE_FACTORIES = {
    "rss": RssSource,
    "tron": TronSource,
    "ethereum": EthSource,
    "solana": SolanaSource,
    "bnb": BnbSource,
    "btc": BtcSource,
    "xrp": XrpSource,
    "macro": MacroSource,
    "sec": SecSource,
    "hyperliquid": HyperliquidSource,
    "polymarket": PolymarketSource,
}


def get_supported_source_names() -> list[str]:
    return list(_SOURCE_FACTORIES.keys())


def _normalize(names: Iterable[str] | None) -> set[str] | None:
    if names is None:
        return None
    out = {str(name or "").strip().lower() for name in names if str(name or "").strip()}
    return out


def get_enabled_sources(*, only: Iterable[str] | None = None, exclude: Iterable[str] | None = None):
    only_set = _normalize(only)
    exclude_set = _normalize(exclude) or set()

    sources = []
    for name, factory in _SOURCE_FACTORIES.items():
        if only_set is not None and name not in only_set:
            continue
        if name in exclude_set:
            continue
        sources.append(factory())
    return sources
