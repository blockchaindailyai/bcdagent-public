from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    intel_api_base: str
    ingest_token: str
    ingest_timeout_seconds: int = 20
    # Keep default ingest payloads moderate to avoid API body-size limits while
    # still batching enough items to keep request counts reasonable.
    ingest_batch_size: int = 100
    ingest_max_rate_limit_retries: int = 4
    ingest_rate_limit_backoff_seconds: float = 1.0
    log_level: str = "INFO"
    log_httpx_requests: bool = False
    run_once: bool = False
    poll_interval_seconds: int = 60
    rss_poll_interval_seconds: int = 60
    tron_poll_interval_seconds: int = 60
    ethereum_poll_interval_seconds: int = 60
    solana_poll_interval_seconds: int = 60
    bnb_poll_interval_seconds: int = 60
    btc_poll_interval_seconds: int = 60
    xrp_poll_interval_seconds: int = 60
    macro_poll_interval_seconds: int = 300
    sec_poll_interval_seconds: int = 300
    hyperliquid_poll_interval_seconds: int = 60
    polymarket_poll_interval_seconds: int = 120
    send_test_event: bool = False
    rss_feeds: str = ""
    rss_state_path: str = ".data/rss_state.json"
    rss_max_seen_per_feed: int = 2000
    rss_http_timeout_seconds: int = 15
    tron_api_base: str = "https://api.trongrid.io"
    tron_api_key: str = ""
    tron_state_path: str = ".data/tron_state.json"
    tron_http_timeout_seconds: int = 15
    tron_lookback_blocks_on_boot: int = 20
    tron_min_transfer_usd: float = 200_000_000
    tron_watchlist_min_transfer_usd: float = 1_000_000
    tron_watchlist_path: str = "config/tron_watchlist.json"
    tron_trx_usd_fallback: float = 0.25
    tron_price_cache_ttl_seconds: int = 300
    tron_price_rate_limit_backoff_seconds: int = 180
    token_price_cache_path: str = ".data/token_price_cache.json"
    token_price_rate_limit_backoff_seconds: int = 180
    # Keep requests conservative to avoid TronGrid 429 responses.
    # 0.50s spacing ~= 2 req/s, which is comfortably below common public-tier limits.
    tron_block_request_delay_seconds: float = 0.50
    tron_block_fetch_max_retries: int = 3
    tron_block_retry_backoff_seconds: float = 0.5

    eth_rpc_url: str = "https://ethereum-rpc.publicnode.com"
    eth_state_path: str = ".data/eth_state.json"
    eth_http_timeout_seconds: int = 15
    eth_lookback_blocks_on_boot: int = 20
    eth_min_transfer_usd: float = 200_000_000
    eth_watchlist_min_transfer_usd: float = 1_000_000
    eth_watchlist_path: str = "config/eth_watchlist.json"
    eth_eth_usd_fallback: float = 3000
    eth_price_cache_ttl_seconds: int = 300
    eth_block_request_delay_seconds: float = 0.20
    eth_block_fetch_max_retries: int = 3
    eth_block_retry_backoff_seconds: float = 0.5

    solana_rpc_url: str = "https://beta.helius-rpc.com/?api-key=9f0e2e8b-d910-4ef5-b972-7d46c7551481"
    solana_state_path: str = ".data/solana_state.json"
    solana_http_timeout_seconds: int = 15
    solana_lookback_slots_on_boot: int = 20
    solana_min_transfer_usd: float = 200_000_000
    solana_watchlist_min_transfer_usd: float = 1_000_000
    solana_watchlist_path: str = "config/solana_watchlist.json"
    solana_sol_usd_fallback: float = 170
    # Keep per-slot requests conservative to avoid public RPC 429 responses.
    # 0.50s spacing ~= 2 req/s, plus retries with backoff on 429 responses.
    solana_slot_request_delay_seconds: float = 0.50
    solana_slot_fetch_max_retries: int = 3
    solana_slot_retry_backoff_seconds: float = 0.5
    solana_max_slots_per_cycle: int = 256
    # Solana block scans are slot-dense; keep a small parallel default so fetch loops
    # don't stall for multiple minutes on public RPC endpoints.
    solana_parallel_block_fetch_workers: int = 4

    bnb_rpc_url: str = "https://bsc-dataseed.binance.org"
    bnb_state_path: str = ".data/bnb_state.json"
    bnb_http_timeout_seconds: int = 15
    bnb_confirmation_depth: int = 2
    bnb_min_transfer_usd: float = 200_000_000
    bnb_watchlist_min_transfer_usd: float = 1_000_000
    bnb_watchlist_path: str = "config/bnb_watchlist.json"
    bnb_bnb_usd_fallback: float = 600
    bnb_price_cache_ttl_seconds: int = 300
    bnb_block_request_delay_seconds: float = 0.20
    bnb_block_fetch_max_retries: int = 3
    bnb_block_retry_backoff_seconds: float = 0.5
    bnb_max_blocks_per_cycle: int = 256
    bnb_parallel_block_fetch_workers: int = 1

    btc_rpc_url: str = "https://mempool.space/api"
    btc_fallback_rpc_urls: str = "https://blockstream.info/api,https://mempool.emzy.de/api"
    btc_state_path: str = ".data/btc_state.json"
    btc_http_timeout_seconds: int = 15
    btc_lookback_blocks_on_boot: int = 20
    btc_min_transfer_usd: float = 200_000_000
    btc_watchlist_min_transfer_usd: float = 1_000_000
    btc_watchlist_path: str = "config/btc_watchlist.json"
    btc_usd_fallback: float = 70_000
    btc_price_cache_ttl_seconds: int = 300
    btc_block_request_delay_seconds: float = 0.35
    btc_block_fetch_max_retries: int = 3
    btc_block_retry_backoff_seconds: float = 0.5
    btc_max_blocks_per_cycle: int = 128
    btc_cycle_time_budget_seconds: int = 45
    btc_parallel_tx_fetch_workers: int = 8

    xrp_rpc_url: str = "https://xrplcluster.com"
    xrp_state_path: str = ".data/xrp_state.json"
    xrp_http_timeout_seconds: int = 15
    xrp_lookback_ledgers_on_boot: int = 20
    xrp_min_transfer_usd: float = 200_000_000
    xrp_watchlist_min_transfer_usd: float = 1_000_000
    xrp_watchlist_path: str = "config/xrp_watchlist.json"
    xrp_usd_fallback: float = 0.6
    xrp_price_cache_ttl_seconds: int = 300
    xrp_ledger_request_delay_seconds: float = 0.35
    xrp_ledger_fetch_max_retries: int = 3
    xrp_ledger_retry_backoff_seconds: float = 0.5
    xrp_max_ledgers_per_cycle: int = 256
    xrp_parallel_ledger_fetch_workers: int = 1
    xrp_cycle_time_budget_seconds: int = 45

    fred_api_key: str = ""
    bls_api_key: str = ""
    bea_api_key: str = ""
    macro_state_path: str = ".data/macro_state.json"
    macro_http_timeout_seconds: int = 20
    macro_max_seen_items: int = 5000
    macro_fred_series_ids: str = "FEDFUNDS,CPIAUCSL,ICSA,UNRATE"
    macro_bls_series_ids: str = "CUUR0000SA0,LNS14000000"
    macro_bls_min_interval_seconds: int = 60
    macro_bea_min_interval_seconds: int = 60
    macro_bea_table_name: str = "T10105"
    macro_bea_line_number: int = 1
    macro_dol_claims_url: str = "https://oui.doleta.gov/unemploy/claims_arch.asp"
    macro_treasury_yield_curve_url: str = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve"
    macro_fed_releases_feed_url: str = "https://www.federalreserve.gov/feeds/press_monetary.xml"

    sec_state_path: str = ".data/sec_state.json"
    sec_http_timeout_seconds: int = 20
    sec_max_seen_items: int = 5000
    sec_ciks: str = "320193,789019,1652044,1018724"
    sec_forms: str = "8-K,10-K,10-Q,6-K,S-1"
    sec_max_filings_per_cik: int = 10
    sec_user_agent: str = "scraper-bot/0.1 (contact: you@example.com)"
    sec_fetch_primary_doc_content: bool = True
    sec_primary_doc_max_chars: int = 200000
    sec_content_summary_max_sentences: int = 3

    hyperliquid_api_base: str = "https://api.hyperliquid.xyz"
    hyperliquid_http_timeout_seconds: int = 15
    hyperliquid_state_path: str = ".data/hyperliquid_state.json"
    hyperliquid_markets: str = "BTC,ETH,SOL,HYPE"
    hyperliquid_trade_limit_per_market: int = 200
    hyperliquid_major_trade_min_usd: float = 1_000_000
    hyperliquid_max_seen_trade_ids: int = 5000

    polymarket_events_api_url: str = "https://gamma-api.polymarket.com/events?active=true&closed=false&limit=200"
    polymarket_http_timeout_seconds: int = 20
    polymarket_state_path: str = ".data/polymarket_state.json"
    polymarket_max_seen_items: int = 10000
    polymarket_max_events: int = 200
    polymarket_max_pages_per_cycle: int = 1000
    polymarket_max_items_per_cycle: int = 5000
    polymarket_min_volume_usd: float = 5000
    polymarket_request_spacing_seconds: float = 0.25
    polymarket_max_retries_per_page: int = 3
    polymarket_retry_backoff_seconds: float = 1.0
    polymarket_leaderboard_enabled: bool = True
    polymarket_leaderboard_output_path: str = "bcd-treasury-frontend/public/polymarket/top_traders_leaderboard.json"
    polymarket_leaderboard_category: str = "overall"
    polymarket_leaderboard_time_window: str = "monthly"
    polymarket_leaderboard_time_windows: str = "daily,weekly,monthly,all"
    polymarket_leaderboard_sort: str = "profit"
    polymarket_leaderboard_top_n: int = 25


def _discover_env_files() -> tuple[str, ...]:
    candidates: list[Path] = []

    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        candidates.append(cwd_env)

    project_env = Path(__file__).resolve().parents[3] / ".env"
    if project_env.exists() and project_env not in candidates:
        candidates.append(project_env)

    return tuple(str(path) for path in candidates)


def get_settings() -> Settings:
    env_files = _discover_env_files()
    if env_files:
        return Settings(_env_file=env_files)
    return Settings()
