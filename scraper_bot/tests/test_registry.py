import os
import unittest

from scraper_bot.sources.registry import get_enabled_sources, get_supported_source_names


class RegistryTest(unittest.TestCase):
    def test_includes_supported_sources(self):
        old_env = dict(os.environ)
        try:
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")
            names = [s.name for s in get_enabled_sources()]
            self.assertEqual(
                names,
                [
                    "rss",
                    "tron",
                    "ethereum",
                    "solana",
                    "bnb",
                    "btc",
                    "xrp",
                    "macro",
                    "sec",
                    "hyperliquid",
                    "polymarket",
                ],
            )
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_get_enabled_sources_can_filter(self):
        old_env = dict(os.environ)
        try:
            os.environ.setdefault("INTEL_API_BASE", "http://localhost")
            os.environ.setdefault("INGEST_TOKEN", "x")
            names = [s.name for s in get_enabled_sources(only={"rss", "polymarket"})]
            self.assertEqual(names, ["rss", "polymarket"])

            names_excluded = [s.name for s in get_enabled_sources(exclude={"rss", "polymarket"})]
            self.assertNotIn("rss", names_excluded)
            self.assertNotIn("polymarket", names_excluded)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_supported_names_includes_polymarket(self):
        names = get_supported_source_names()
        self.assertIn("polymarket", names)


if __name__ == "__main__":
    unittest.main()
