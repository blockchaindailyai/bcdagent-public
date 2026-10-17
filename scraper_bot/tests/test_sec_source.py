import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scraper_bot.sources.sec_source import SecSource


class DummyResponse:
    def __init__(self, payload=None, text=""):
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class DummyClient:
    def __init__(self, responses_by_url):
        self.responses_by_url = responses_by_url
        self.urls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url):
        self.urls.append(url)
        response = self.responses_by_url.get(url)
        if response is None:
            raise AssertionError(f"No response configured for URL: {url}")
        return response


class SecSourceTest(unittest.TestCase):
    def setUp(self):
        self.old_env = dict(os.environ)
        self.tmpdir = tempfile.TemporaryDirectory()
        os.environ.setdefault("INTEL_API_BASE", "http://localhost")
        os.environ.setdefault("INGEST_TOKEN", "x")
        os.environ["SEC_USER_AGENT"] = "scraper-bot-tests/0.1 (contact: test@example.com)"
        os.environ["SEC_CIKS"] = "320193"
        os.environ["SEC_FORMS"] = "8-K,10-Q"
        os.environ["SEC_MAX_FILINGS_PER_CIK"] = "5"
        os.environ["SEC_STATE_PATH"] = str(Path(self.tmpdir.name) / "sec_state.json")
        os.environ["SEC_FETCH_PRIMARY_DOC_CONTENT"] = "true"
        os.environ["SEC_PRIMARY_DOC_MAX_CHARS"] = "50000"
        os.environ["SEC_CONTENT_SUMMARY_MAX_SENTENCES"] = "2"

    def tearDown(self):
        self.tmpdir.cleanup()
        os.environ.clear()
        os.environ.update(self.old_env)

    def test_fetch_parses_recent_filings_and_persists_seen_ids(self):
        payload = {
            "name": "Apple Inc.",
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-24-000010", "0000320193-24-000011"],
                    "form": ["8-K", "10-K"],
                    "filingDate": ["20240501", "20240201"],
                    "acceptanceDateTime": ["2024-05-01T12:00:01.000Z", "2024-02-01T12:00:01.000Z"],
                    "primaryDocument": ["a8k.htm", "a10k.htm"],
                    "primaryDocDescription": ["Current report", "Annual report"],
                }
            },
        }

        sec_url = "https://data.sec.gov/submissions/CIK0000320193.json"
        filing_url = "https://www.sec.gov/Archives/edgar/data/320193/000032019324000010/a8k.htm"
        filing_doc = """
            <html><body>
            <h1>Item 2.02 Results of Operations and Financial Condition</h1>
            <p>Revenue increased 12% year over year and operating margin expanded.</p>
            <p>Management updated full-year guidance.</p>
            </body></html>
        """
        client = DummyClient(
            {
                sec_url: DummyResponse(payload=payload),
                filing_url: DummyResponse(text=filing_doc),
            }
        )
        with patch("scraper_bot.sources.sec_source.httpx.Client", return_value=client):
            source = SecSource()
            items = source.fetch()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source, "sec")
        self.assertEqual(items[0].data["formType"], "8-K")
        self.assertTrue(items[0].data["contentExtracted"])
        self.assertIn("Revenue increased", items[0].data["contentSummary"])
        self.assertEqual(len(client.urls), 2)
        self.assertIn("CIK0000320193.json", client.urls[0])

        state_path = Path(os.environ["SEC_STATE_PATH"])
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(len(state["seen_ids"]), 1)

    def test_fetch_returns_empty_when_user_agent_missing(self):
        os.environ["SEC_USER_AGENT"] = ""
        source = SecSource()
        self.assertEqual(source.fetch(), [])


if __name__ == "__main__":
    unittest.main()
