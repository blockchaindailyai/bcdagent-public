import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scraper_bot import cli


class CliSourceSelectionTest(unittest.TestCase):
    @patch("scraper_bot.cli.TerminalStatus")
    @patch("scraper_bot.cli.configure_logging")
    @patch("scraper_bot.cli.IngestClient")
    @patch("scraper_bot.cli.Runner")
    @patch("scraper_bot.cli.get_settings")
    def test_sources_flag_is_passed_to_runner(
        self,
        get_settings,
        runner_cls,
        ingest_cls,
        _configure_logging,
        terminal_status_cls,
    ):
        get_settings.return_value = SimpleNamespace(
            intel_api_base="http://intel",
            ingest_token="token",
            ingest_timeout_seconds=10,
            ingest_batch_size=50,
            ingest_max_rate_limit_retries=4,
            ingest_rate_limit_backoff_seconds=1.0,
            log_level="INFO",
            log_httpx_requests=False,
            run_once=False,
            poll_interval_seconds=60,
            rss_poll_interval_seconds=60,
            tron_poll_interval_seconds=60,
            ethereum_poll_interval_seconds=60,
            solana_poll_interval_seconds=60,
            bnb_poll_interval_seconds=60,
            btc_poll_interval_seconds=60,
            xrp_poll_interval_seconds=60,
            macro_poll_interval_seconds=300,
            sec_poll_interval_seconds=300,
            hyperliquid_poll_interval_seconds=60,
            polymarket_poll_interval_seconds=120,
        )
        terminal_status_cls.return_value = MagicMock()
        runner = MagicMock()
        runner_cls.return_value = runner

        with patch("sys.argv", ["scraper-bot", "run", "--once", "--sources", "rss,polymarket"]):
            cli.main()

        kwargs = runner_cls.call_args.kwargs
        self.assertEqual(kwargs["enabled_source_names"], {"rss", "polymarket"})
        runner.run_once.assert_called_once()

    @patch("scraper_bot.cli.get_settings")
    def test_unknown_source_exits(self, get_settings):
        get_settings.return_value = SimpleNamespace()
        with (
            patch("sys.argv", ["scraper-bot", "run", "--sources", "rss,unknown"]),
            self.assertRaises(SystemExit),
        ):
            cli.main()


if __name__ == "__main__":
    unittest.main()
