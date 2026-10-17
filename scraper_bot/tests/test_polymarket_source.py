import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from scraper_bot.sources.polymarket_source import PolymarketSource, PolymarketState, _infer_topics_from_text, _utc_now


class PolymarketSourceTest(unittest.TestCase):
    def setUp(self):
        self._old_env = dict(os.environ)
        os.environ.setdefault("INTEL_API_BASE", "http://localhost")
        os.environ.setdefault("INGEST_TOKEN", "x")
        os.environ.setdefault("POLYMARKET_MIN_VOLUME_USD", "0")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)

    def test_build_market_url_prefers_event_and_market_slug(self):
        source = PolymarketSource()
        event = {"slug": "colombia-presidential-election"}
        market = {"slug": "will-abelardo-de-la-espriella-win-the-2026-colombian-presidential-election"}

        url = source._build_market_url(event, market)

        self.assertEqual(
            url,
            "https://polymarket.com/event/colombia-presidential-election/will-abelardo-de-la-espriella-win-the-2026-colombian-presidential-election",
        )

    def test_fetch_supports_markets_api_shape_and_non_integer_probability_display(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ.update(
                {
                    "POLYMARKET_STATE_PATH": str(Path(td) / "polymarket_state.json"),
                    "POLYMARKET_MAX_EVENTS": "10",
                }
            )
            source = PolymarketSource()

            market_rows = [
                {
                    "id": "m1",
                    "slug": "will-abelardo-de-la-espriella-win-the-2026-colombian-presidential-election",
                    "question": "Will Abelardo de la Espriella win the 2026 Colombian presidential election?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.348", "0.652"]',
                    "volumeNum": "125000",
                    "updatedAt": "2026-01-01T00:00:00Z",
                    "events": [
                        {
                            "id": "e1",
                            "slug": "colombia-presidential-election",
                            "title": "Colombia presidential election",
                            "startDate": "2026-01-01T00:00:00Z",
                            "tags": [{"label": "Politics"}],
                        }
                    ],
                }
            ]

            items = source._event_to_items(source._coerce_market_rows_to_event_rows(market_rows)[0], _utc_now(), PolymarketState(market_fingerprints={}))

            self.assertEqual(len(items), 1)
            self.assertIn("65.2%", items[0].summary)
            self.assertEqual(
                str(items[0].url),
                "https://polymarket.com/event/colombia-presidential-election/will-abelardo-de-la-espriella-win-the-2026-colombian-presidential-election",
            )
            self.assertAlmostEqual(items[0].data["topOutcomeProbability"], 0.652)
            self.assertIn("politics", [t.lower() for t in items[0].tags])
            self.assertEqual(items[0].data["eventCategory"], "Politics")

    def test_market_category_fields_are_used_when_event_tags_missing(self):
        source = PolymarketSource()
        event = {
            "id": "e1",
            "title": "Event",
            "markets": [
                {
                    "id": "m1",
                    "question": "Will candidate X win?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.55", "0.45"]',
                    "updatedAt": "2026-01-01T00:00:00Z",
                    "category": "Politics",
                    "groupItemTitle": "US Election",
                }
            ],
        }

        items = source._event_to_items(event, _utc_now(), PolymarketState(market_fingerprints={}))

        self.assertEqual(len(items), 1)
        normalized_topics = [t.lower() for t in items[0].tags]
        self.assertIn("politics", normalized_topics)
        self.assertIn("us election", normalized_topics)

    def test_coerce_market_rows_merges_tags_from_all_event_rows(self):
        source = PolymarketSource()

        rows = [
            {
                "id": "m1",
                "question": "Q1",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.5", "0.5"]',
                "events": [
                    {"id": "e1", "title": "Event 1", "tags": [{"label": "Politics"}]},
                    {"id": "e1", "title": "Event 1", "tags": [{"label": "US Election"}]},
                ],
            }
        ]

        events = source._coerce_market_rows_to_event_rows(rows)
        items = source._event_to_items(events[0], _utc_now(), PolymarketState(market_fingerprints={}))

        self.assertEqual(len(items), 1)
        normalized_topics = [t.lower() for t in items[0].tags]
        self.assertIn("politics", normalized_topics)
        self.assertIn("us election", normalized_topics)


    def test_event_to_items_excludes_markets_below_min_volume(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ.update(
                {
                    "POLYMARKET_STATE_PATH": str(Path(td) / "polymarket_state.json"),
                    "POLYMARKET_MIN_VOLUME_USD": "5000",
                }
            )
            source = PolymarketSource()

            event = {
                "id": "e1",
                "title": "Event",
                "markets": [
                    {
                        "id": "m-low",
                        "question": "Low volume",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.6", "0.4"]',
                        "volumeNum": "4999.99",
                        "updatedAt": "2026-01-01T00:00:00Z",
                    },
                    {
                        "id": "m-ok",
                        "question": "High volume",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.6", "0.4"]',
                        "volumeNum": "5000",
                        "updatedAt": "2026-01-01T00:00:00Z",
                    },
                ],
            }

            items = source._event_to_items(event, _utc_now(), PolymarketState(market_fingerprints={}))

            self.assertEqual([item.data["marketId"] for item in items], ["m-ok"])

    def test_fetch_re_emits_market_when_updated_timestamp_changes(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ.update(
                {
                    "POLYMARKET_STATE_PATH": str(Path(td) / "polymarket_state.json"),
                    "POLYMARKET_MAX_EVENTS": "10",
                    "POLYMARKET_MAX_PAGES_PER_CYCLE": "1",
                }
            )
            source = PolymarketSource()

            base_market = {
                "id": "m1",
                "question": "Will X happen?",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.51", "0.49"]',
                "events": [{"id": "e1", "title": "Event 1"}],
            }

            first = dict(base_market)
            first["updatedAt"] = "2026-01-01T00:00:00Z"
            second = dict(base_market)
            second["updatedAt"] = "2026-01-01T00:05:00Z"

            with patch.object(source, "_fetch_events_page", side_effect=[([source._coerce_market_rows_to_event_rows([first])[0]], 1), ([source._coerce_market_rows_to_event_rows([second])[0]], 1)]):
                first_items = source.fetch()
                second_items = source.fetch()

            self.assertEqual(len(first_items), 1)
            self.assertEqual(len(second_items), 1)
            self.assertEqual(first_items[0].data["updatedAt"], "2026-01-01T00:00:00Z")
            self.assertEqual(second_items[0].data["updatedAt"], "2026-01-01T00:05:00Z")

    def test_fetch_paginates_until_empty_page_and_collects_all_markets(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ.update(
                {
                    "POLYMARKET_STATE_PATH": str(Path(td) / "polymarket_state.json"),
                    "POLYMARKET_MAX_EVENTS": "2",
                    "POLYMARKET_MAX_PAGES_PER_CYCLE": "5",
                }
            )
            source = PolymarketSource()

            page1 = [
                {"id": "m1", "question": "Q1", "outcomes": '["Yes","No"]', "outcomePrices": '["0.9","0.1"]', "updatedAt": "2026-01-01T00:00:00Z", "events": [{"id": "e1", "title": "E1"}]},
                {"id": "m2", "question": "Q2", "outcomes": '["Yes","No"]', "outcomePrices": '["0.4","0.6"]', "updatedAt": "2026-01-01T00:00:00Z", "events": [{"id": "e2", "title": "E2"}]},
            ]
            page2 = [
                {"id": "m3", "question": "Q3", "outcomes": '["Yes","No"]', "outcomePrices": '["0.7","0.3"]', "updatedAt": "2026-01-01T00:00:00Z", "events": [{"id": "e3", "title": "E3"}]}
            ]

            pages = [page1, page2]
            offsets_seen = []

            def fake_fetch_page(_client, offset):
                offsets_seen.append(offset)
                if pages:
                    rows = pages.pop(0)
                    return source._coerce_market_rows_to_event_rows(rows), len(rows)
                return []

            with patch.object(source, "_fetch_events_page", side_effect=fake_fetch_page):
                items = source.fetch()

            self.assertEqual(offsets_seen, [0, 2, 3])
            self.assertEqual(len(items), 3)
            self.assertEqual({item.data["marketId"] for item in items}, {"m1", "m2", "m3"})


    def test_fetch_stops_when_max_items_per_cycle_is_reached(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ.update(
                {
                    "POLYMARKET_STATE_PATH": str(Path(td) / "polymarket_state.json"),
                    "POLYMARKET_MAX_EVENTS": "2",
                    "POLYMARKET_MAX_PAGES_PER_CYCLE": "5",
                    "POLYMARKET_MAX_ITEMS_PER_CYCLE": "2",
                }
            )
            source = PolymarketSource()

            page1 = source._coerce_market_rows_to_event_rows(
                [
                    {"id": "m1", "question": "Q1", "outcomes": '["Yes","No"]', "outcomePrices": '["0.9","0.1"]', "updatedAt": "2026-01-01T00:00:00Z", "events": [{"id": "e1", "title": "E1"}]},
                    {"id": "m2", "question": "Q2", "outcomes": '["Yes","No"]', "outcomePrices": '["0.4","0.6"]', "updatedAt": "2026-01-01T00:00:00Z", "events": [{"id": "e2", "title": "E2"}]},
                ]
            )
            page2 = source._coerce_market_rows_to_event_rows(
                [
                    {"id": "m3", "question": "Q3", "outcomes": '["Yes","No"]', "outcomePrices": '["0.7","0.3"]', "updatedAt": "2026-01-01T00:00:00Z", "events": [{"id": "e3", "title": "E3"}]}
                ]
            )

            with patch.object(source, "_fetch_events_page", side_effect=[(page1, 2), (page2, 1)]) as fetch_page_mock:
                items = source.fetch()

            self.assertEqual(len(items), 2)
            self.assertEqual({item.data["marketId"] for item in items}, {"m1", "m2"})
            self.assertEqual(fetch_page_mock.call_count, 1)


    def test_fetch_rotates_offsets_across_cycles_when_capped(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ.update(
                {
                    "POLYMARKET_STATE_PATH": str(Path(td) / "polymarket_state.json"),
                    "POLYMARKET_MAX_EVENTS": "2",
                    "POLYMARKET_MAX_PAGES_PER_CYCLE": "3",
                    "POLYMARKET_MAX_ITEMS_PER_CYCLE": "2",
                }
            )
            source = PolymarketSource()

            page1 = source._coerce_market_rows_to_event_rows(
                [
                    {"id": "m1", "question": "Q1", "outcomes": '["Yes","No"]', "outcomePrices": '["0.9","0.1"]', "updatedAt": "2026-01-01T00:00:00Z", "events": [{"id": "e1", "title": "E1"}]},
                    {"id": "m2", "question": "Q2", "outcomes": '["Yes","No"]', "outcomePrices": '["0.6","0.4"]', "updatedAt": "2026-01-01T00:00:00Z", "events": [{"id": "e2", "title": "E2"}]},
                ]
            )
            page2 = source._coerce_market_rows_to_event_rows(
                [
                    {"id": "m3", "question": "Q3", "outcomes": '["Yes","No"]', "outcomePrices": '["0.7","0.3"]', "updatedAt": "2026-01-01T00:00:00Z", "events": [{"id": "e3", "title": "E3"}]},
                    {"id": "m4", "question": "Q4", "outcomes": '["Yes","No"]', "outcomePrices": '["0.2","0.8"]', "updatedAt": "2026-01-01T00:00:00Z", "events": [{"id": "e4", "title": "E4"}]},
                ]
            )

            calls = []

            def fake_fetch_page(_client, offset):
                calls.append(offset)
                if offset == 0:
                    return page1, 2
                if offset == 2:
                    return page2, 2
                return [], 0

            with patch.object(source, "_fetch_events_page", side_effect=fake_fetch_page):
                first_items = source.fetch()
                second_items = source.fetch()

            self.assertEqual([item.data["marketId"] for item in first_items], ["m1", "m2"])
            self.assertEqual([item.data["marketId"] for item in second_items], ["m3", "m4"])
            self.assertEqual(calls, [0, 2])

    def test_fetch_events_page_sends_limit_param(self):
        source = PolymarketSource()
        source.request_limit = 77

        ok = MagicMock()
        ok.status_code = 200
        ok.headers = {}
        ok.raise_for_status.return_value = None
        ok.json.return_value = []

        client = MagicMock()
        client.get.return_value = ok

        source._fetch_events_page(client, 12)

        client.get.assert_called_once_with(source.api_url, params={"offset": 12, "limit": 77})

    def test_fetch_events_page_retries_on_429_with_retry_after(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ.update(
                {
                    "POLYMARKET_STATE_PATH": str(Path(td) / "polymarket_state.json"),
                    "POLYMARKET_MAX_RETRIES_PER_PAGE": "2",
                    "POLYMARKET_RETRY_BACKOFF_SECONDS": "1.5",
                }
            )
            source = PolymarketSource()

            rate_limited = MagicMock()
            rate_limited.status_code = 429
            rate_limited.headers = {"Retry-After": "0.25"}
            rate_limited.raise_for_status.side_effect = RuntimeError("should not raise on first 429")

            ok = MagicMock()
            ok.status_code = 200
            ok.headers = {}
            ok.raise_for_status.return_value = None
            ok.json.return_value = [{"id": "e1", "markets": []}]

            client = MagicMock()
            client.get.side_effect = [rate_limited, ok]

            with patch("scraper_bot.sources.polymarket_source.time.sleep") as sleep_mock:
                events, row_count = source._fetch_events_page(client, 0)

            self.assertEqual(len(events), 1)
            self.assertEqual(row_count, 1)
            self.assertEqual(client.get.call_count, 2)
            sleep_mock.assert_called_once_with(0.25)

    def test_fetch_applies_request_spacing_between_pages(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ.update(
                {
                    "POLYMARKET_STATE_PATH": str(Path(td) / "polymarket_state.json"),
                    "POLYMARKET_MAX_EVENTS": "2",
                    "POLYMARKET_MAX_PAGES_PER_CYCLE": "2",
                    "POLYMARKET_REQUEST_SPACING_SECONDS": "1.0",
                }
            )
            source = PolymarketSource()

            first_page = source._coerce_market_rows_to_event_rows(
                [
                    {
                        "id": "m1",
                        "question": "Q1",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.6", "0.4"]',
                        "updatedAt": "2026-01-01T00:00:00Z",
                        "events": [{"id": "e1", "title": "E1"}],
                    },
                    {
                        "id": "m2",
                        "question": "Q2",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.4", "0.6"]',
                        "updatedAt": "2026-01-01T00:00:00Z",
                        "events": [{"id": "e2", "title": "E2"}],
                    },
                ]
            )
            second_page = source._coerce_market_rows_to_event_rows(
                [
                    {
                        "id": "m3",
                        "question": "Q3",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.7", "0.3"]',
                        "updatedAt": "2026-01-01T00:00:00Z",
                        "events": [{"id": "e3", "title": "E3"}],
                    }
                ]
            )

            with (
                patch.object(source, "_fetch_events_page", side_effect=[(first_page, 2), (second_page, 1)]),
                patch("scraper_bot.sources.polymarket_source.time.monotonic", side_effect=[0.0, 0.0, 0.2, 0.2]),
                patch("scraper_bot.sources.polymarket_source.time.sleep") as sleep_mock,
            ):
                items = source.fetch()

            self.assertEqual(len(items), 3)
            sleep_mock.assert_called_once_with(0.8)

    def test_derive_topics_uses_api_tags(self):
        source = PolymarketSource()

        topics = source._derive_market_topics(["Politics", "US Election", "Politics", ""])

        self.assertEqual(topics, ["Politics", "US Election"])

    def test_keyword_inference_fallback_from_question_text(self):
        topics = _infer_topics_from_text("Will Democrats win the presidential election?")

        self.assertIn("Elections", topics)


    def test_keyword_inference_supports_polymarket_category_set(self):
        topics = _infer_topics_from_text(
            "Will Iran and Israel conflict escalate and impact crypto markets and climate science headlines?"
        )

        self.assertIn("Iran", topics)
        self.assertIn("Geopolitics", topics)
        self.assertIn("Crypto", topics)
        self.assertIn("Climate & Science", topics)
    def test_event_to_items_adds_keyword_category_when_tags_absent(self):
        source = PolymarketSource()
        event = {
            "id": "e1",
            "title": "US election special",
            "slug": "us-election-special",
            "markets": [
                {
                    "id": "m1",
                    "question": "Will a Republican win the 2028 presidential election?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.55", "0.45"]',
                    "volumeNum": "10000",
                    "updatedAt": "2026-01-01T00:00:00Z",
                }
            ],
        }

        items = source._event_to_items(event, _utc_now(), PolymarketState(market_fingerprints={}))

        self.assertEqual(len(items), 1)
        self.assertIn("elections", [t.lower() for t in items[0].tags])
        self.assertEqual(items[0].data["eventCategory"], "Elections")
        self.assertIn("Elections", items[0].data["inferredTopics"])

    def test_event_series_are_used_as_topics(self):
        source = PolymarketSource()
        event = {
            "id": "e1",
            "title": "US politics special",
            "series": [{"title": "US Election", "slug": "us-election"}],
            "markets": [
                {
                    "id": "m1",
                    "question": "Will candidate X win?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.55", "0.45"]',
                    "updatedAt": "2026-01-01T00:00:00Z",
                }
            ],
        }

        items = source._event_to_items(event, _utc_now(), PolymarketState(market_fingerprints={}))

        self.assertEqual(len(items), 1)
        normalized_topics = [t.lower() for t in items[0].tags]
        self.assertIn("us election", normalized_topics)
        self.assertEqual(items[0].data["eventCategory"], "Politics")
        self.assertIn("Politics", items[0].data["inferredTopics"])


    def test_fetch_refreshes_leaderboard_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ.update(
                {
                    "POLYMARKET_STATE_PATH": str(Path(td) / "polymarket_state.json"),
                    "POLYMARKET_LEADERBOARD_OUTPUT_PATH": str(Path(td) / "top_traders_leaderboard.json"),
                }
            )
            source = PolymarketSource()

            with patch.object(source, "_fetch_events_page", return_value=([], 0)), patch.object(source, "_refresh_leaderboard_snapshot") as refresh_mock:
                source.fetch()

            refresh_mock.assert_called_once()

    def test_refresh_leaderboard_snapshot_writes_json(self):
        with tempfile.TemporaryDirectory() as td:
            output_path = Path(td) / "top_traders_leaderboard.json"
            os.environ.update(
                {
                    "POLYMARKET_STATE_PATH": str(Path(td) / "polymarket_state.json"),
                    "POLYMARKET_LEADERBOARD_OUTPUT_PATH": str(output_path),
                    "POLYMARKET_LEADERBOARD_SORT": "profit",
                    "POLYMARKET_LEADERBOARD_TIME_WINDOW": "weekly",
                    "POLYMARKET_LEADERBOARD_TIME_WINDOWS": "daily,weekly",
                    "POLYMARKET_LEADERBOARD_TOP_N": "1",
                }
            )
            source = PolymarketSource()

            html = '<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"dehydratedState":{"queries":[{"queryKey":["/leaderboard","profit","30d"],"state":{"data":[{"rank":1,"proxyWallet":"0xabc","name":"Trader","pnl":"12.5","volume":"1000"}]}}]}}}}</script>'

            with patch.object(source, "_fetch_html", return_value=html):
                source._refresh_leaderboard_snapshot()

            payload = __import__("json").loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["sort"], "profit")
            self.assertEqual(payload["time"], "weekly")
            self.assertEqual(payload["rowCount"], 1)
            self.assertEqual(payload["rows"][0]["proxyWallet"], "0xabc")
            self.assertAlmostEqual(payload["rows"][0]["pnlPercent"], 1.25)
            self.assertEqual(payload["availableTimes"], ["daily", "weekly"])
            self.assertIn("snapshots", payload)
            self.assertIn("daily", payload["snapshots"])
            self.assertIn("weekly", payload["snapshots"])



if __name__ == "__main__":
    unittest.main()
