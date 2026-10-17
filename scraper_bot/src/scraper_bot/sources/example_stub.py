from datetime import datetime, timezone
from scraper_bot.sources.base import SourceScraper
from scraper_bot.schema.canonical import CanonicalItem
from scraper_bot.core.config import get_settings


class ExampleStubSource(SourceScraper):
    @property
    def name(self):
        return "example_stub"

    def fetch(self):
        settings = get_settings()
        if not settings.send_test_event:
            return []

        now = datetime.now(timezone.utc)
        return [
            CanonicalItem(
                item_type="news",
                title="🚨 TEST EVENT: Scraper Boot Successful",
                summary="This is a controlled test event to confirm pipeline + ingest.",
                published_at=now,
                scraped_at=now,
                source="scraper_test",
                tags=["test", "boot"],
                severity="info",
                classification={
                    "priority": "normal",
                    "assetClass": "general",
                    "tickers": ["$BTC", "ETH"],
                    "topics": ["test"],
                    "regions": ["US"],
                },
                data={"testMode": True, "confidence": 1.0},
            )
        ]