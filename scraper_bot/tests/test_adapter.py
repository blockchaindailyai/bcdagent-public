from datetime import datetime, timezone
import unittest

from scraper_bot.schema.adapter import SchemaAdapter
from scraper_bot.schema.canonical import CanonicalItem


class AdapterTest(unittest.TestCase):
    def test_normalizes_non_v1_classification_values(self):
        item = CanonicalItem(
            item_type="onchain_transfer",
            title="Big transfer",
            scraped_at=datetime.now(timezone.utc),
            source="tron",
            classification={
                "priority": "high",
                "assetClass": "crypto",
                "subcategory": "onchain_transfer",
                "tickers": ["trx"],
            },
        )

        event = SchemaAdapter().to_intel_event(item)
        self.assertEqual(event.classification.priority, "breaking")
        self.assertEqual(event.classification.assetClass, "crypto")
        self.assertEqual(event.classification.subcategory, "crypto")
        self.assertEqual(event.classification.tickers, ["TRX"])


if __name__ == "__main__":
    unittest.main()
