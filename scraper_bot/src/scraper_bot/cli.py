from __future__ import annotations

import argparse

from scraper_bot.core.config import get_settings
from scraper_bot.core.logging import TerminalStatus, configure_logging
from scraper_bot.ingest.client import IngestClient
from scraper_bot.pipeline.runner import Runner
from scraper_bot.schema.adapter import SchemaAdapter
from scraper_bot.sources.registry import get_supported_source_names


def _parse_source_arg(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    values = {part.strip().lower() for part in raw.split(",") if part.strip()}
    return values or None


def _validate_sources(selected: set[str] | None, excluded: set[str] | None) -> None:
    supported = set(get_supported_source_names())
    unknown_selected = sorted((selected or set()) - supported)
    unknown_excluded = sorted((excluded or set()) - supported)
    unknown = unknown_selected + unknown_excluded
    if unknown:
        raise SystemExit(
            f"Unknown source(s): {', '.join(unknown)}. Supported: {', '.join(sorted(supported))}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="scraper-bot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--once", action="store_true")
    run.add_argument(
        "--sources",
        help="Comma-separated source names to run. Example: --sources rss,polymarket,hyperliquid",
    )
    run.add_argument(
        "--exclude-sources",
        help="Comma-separated source names to skip. Applied after --sources selection.",
    )
    args = parser.parse_args()

    selected_sources = _parse_source_arg(args.sources)
    excluded_sources = _parse_source_arg(args.exclude_sources) or set()
    _validate_sources(selected_sources, excluded_sources)

    enabled_sources = None
    if selected_sources is not None:
        enabled_sources = selected_sources - excluded_sources
    elif excluded_sources:
        enabled_sources = set(get_supported_source_names()) - excluded_sources

    settings = get_settings()
    terminal_status = TerminalStatus(get_supported_source_names())
    configure_logging(
        settings.log_level,
        log_httpx_requests=settings.log_httpx_requests,
        terminal_status=terminal_status,
    )
    terminal_status.render()

    ingest = IngestClient(
        settings.intel_api_base,
        settings.ingest_token,
        settings.ingest_timeout_seconds,
        max_rate_limit_retries=settings.ingest_max_rate_limit_retries,
        rate_limit_backoff_seconds=settings.ingest_rate_limit_backoff_seconds,
    )
    runner = Runner(
        ingest,
        SchemaAdapter(),
        settings.ingest_batch_size,
        status_callback=terminal_status.set_source,
        enabled_source_names=enabled_sources,
    )

    if args.once or settings.run_once:
        runner.run_once()
    else:
        interval_by_source = {
            "rss": settings.rss_poll_interval_seconds or settings.poll_interval_seconds,
            "tron": settings.tron_poll_interval_seconds or settings.poll_interval_seconds,
            "ethereum": settings.ethereum_poll_interval_seconds or settings.poll_interval_seconds,
            "solana": settings.solana_poll_interval_seconds or settings.poll_interval_seconds,
            "bnb": settings.bnb_poll_interval_seconds or settings.poll_interval_seconds,
            "btc": settings.btc_poll_interval_seconds or settings.poll_interval_seconds,
            "xrp": settings.xrp_poll_interval_seconds or settings.poll_interval_seconds,
            "macro": settings.macro_poll_interval_seconds or settings.poll_interval_seconds,
            "sec": settings.sec_poll_interval_seconds or settings.poll_interval_seconds,
            "hyperliquid": settings.hyperliquid_poll_interval_seconds or settings.poll_interval_seconds,
            "polymarket": settings.polymarket_poll_interval_seconds or settings.poll_interval_seconds,
        }
        runner.run_continuously(interval_by_source)
