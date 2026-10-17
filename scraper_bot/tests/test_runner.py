import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scraper_bot.pipeline.runner import Runner


class DummySource:
    name = "dummy"

    def __init__(self, items):
        self._items = items

    def fetch(self):
        return self._items


class DummyAdapter:
    def to_intel_events(self, items):
        return items


class DummyResponse:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        raise RuntimeError(f"HTTP {self.status_code}")


class DummyClient:
    def __init__(self, statuses, *, max_rate_limit_retries=0, rate_limit_backoff_seconds=1.0):
        self.statuses = list(statuses)
        self.calls = []
        self.max_rate_limit_retries = max_rate_limit_retries
        self.rate_limit_backoff_seconds = rate_limit_backoff_seconds

    def post_events(self, events):
        self.calls.append(list(events))
        result = self.statuses.pop(0)
        if isinstance(result, tuple):
            code, headers = result
            return DummyResponse(status_code=code, text="bad payload", headers=headers)
        return DummyResponse(status_code=result, text="bad payload")


class RunnerTest(unittest.TestCase):
    def test_splits_bad_request_batches_and_preserves_valid_events(self):
        client = DummyClient([400, 200, 400, 200, 400])
        runner = Runner(ingest_client=client, adapter=DummyAdapter(), batch_size=4)

        # Simulate recursive split of [1,2,3,4]:
        # [1,2,3,4] -> 400
        # [1,2] -> 200
        # [3,4] -> 400
        # [3] -> 200
        # [4] -> 400 (rejected single event)
        runner._ingest_batch([1, 2, 3, 4])

        self.assertEqual(client.calls, [[1, 2, 3, 4], [1, 2], [3, 4], [3], [4]])


    def test_does_not_raise_for_rate_limit_errors(self):
        client = DummyClient([429, 429, 429, 429, 429])
        runner = Runner(ingest_client=client, adapter=DummyAdapter(), batch_size=4)

        runner._ingest_batch([1, 2, 3])


    def test_retries_rate_limited_ingest_before_success(self):
        client = DummyClient(
            [
                (429, {"Retry-After": "0.25"}),
                200,
            ],
            max_rate_limit_retries=2,
            rate_limit_backoff_seconds=1.0,
        )
        runner = Runner(ingest_client=client, adapter=DummyAdapter(), batch_size=4)

        with patch("scraper_bot.pipeline.runner.time.sleep") as sleep_mock:
            runner._ingest_batch([1, 2, 3])

        self.assertEqual(client.calls, [[1, 2, 3], [1, 2, 3]])
        sleep_mock.assert_called_once_with(0.25)


    def test_splits_rate_limited_batches_after_retries(self):
        client = DummyClient(
            [
                429,  # [1,2,3,4]
                429,  # [1,2]
                200,  # [1]
                200,  # [2]
                429,  # [3,4]
                200,  # [3]
                200,  # [4]
            ],
            max_rate_limit_retries=0,
        )
        runner = Runner(ingest_client=client, adapter=DummyAdapter(), batch_size=4)

        runner._ingest_batch([1, 2, 3, 4])

        self.assertEqual(client.calls, [[1, 2, 3, 4], [1, 2], [1], [2], [3, 4], [3], [4]])

    def test_raises_for_non_400_errors(self):
        client = DummyClient([500])
        runner = Runner(ingest_client=client, adapter=DummyAdapter(), batch_size=4)

        with self.assertRaises(RuntimeError):
            runner._ingest_batch([1])

    def test_splits_payload_too_large_batches(self):
        client = DummyClient([413, 200, 413, 200, 413])
        runner = Runner(ingest_client=client, adapter=DummyAdapter(), batch_size=4)

        runner._ingest_batch([1, 2, 3, 4])

        self.assertEqual(client.calls, [[1, 2, 3, 4], [1, 2], [3, 4], [3], [4]])

    def test_does_not_raise_for_single_event_payload_too_large(self):
        client = DummyClient([413])
        runner = Runner(ingest_client=client, adapter=DummyAdapter(), batch_size=4)

        runner._ingest_batch([1])


if __name__ == "__main__":
    unittest.main()


class DummyLoopSource:
    def __init__(self, name, items):
        self.name = name
        self.items = items

    def fetch(self):
        return self.items


class RunnerConcurrencyTest(unittest.TestCase):
    def test_run_once_tracks_independent_cycles_per_source(self):
        statuses = []
        client = DummyClient([200, 200, 200, 200])
        runner = Runner(
            ingest_client=client,
            adapter=DummyAdapter(),
            batch_size=10,
            status_callback=lambda source, state, cycle, detail: statuses.append((source, state, cycle, detail)),
        )

        sources = [DummyLoopSource("rss", [1]), DummyLoopSource("tron", [2])]
        with patch("scraper_bot.pipeline.runner.get_enabled_sources", return_value=sources):
            runner.run_once()
            runner.run_once()

        idle_cycles = [(source, cycle) for source, state, cycle, _ in statuses if state == "idle"]
        self.assertEqual(idle_cycles, [("rss", 1), ("tron", 1), ("rss", 2), ("tron", 2)])



    def test_status_callback_reports_cycle_and_total_fetched(self):
        statuses = []
        client = DummyClient([200, 200])
        runner = Runner(
            ingest_client=client,
            adapter=DummyAdapter(),
            batch_size=10,
            status_callback=lambda source, state, cycle, detail, fetched, total: statuses.append((source, state, cycle, fetched, total)),
        )

        source = DummyLoopSource("rss", [1])
        with patch("scraper_bot.pipeline.runner.get_enabled_sources", return_value=[source]):
            runner.run_once()
            runner.run_once()

        idle_updates = [item for item in statuses if item[1] == "idle"]
        self.assertEqual(idle_updates, [("rss", "idle", 1, 1, 1), ("rss", "idle", 2, 1, 2)])

    def test_run_continuously_stops_with_event(self):
        client = DummyClient([200])
        runner = Runner(ingest_client=client, adapter=DummyAdapter(), batch_size=10)

        sources = [DummyLoopSource("rss", [1])]
        stop_event = __import__("threading").Event()
        stop_event.set()

        with patch("scraper_bot.pipeline.runner.get_enabled_sources", return_value=sources):
            runner.run_continuously({"rss": 1}, stop_event=stop_event)

        self.assertEqual(client.calls, [])

    def test_run_source_loop_waits_only_remaining_interval(self):
        class DummyStopEvent:
            def __init__(self):
                self._checks = 0
                self.wait_calls = []

            def is_set(self):
                self._checks += 1
                return self._checks > 1

            def wait(self, seconds):
                self.wait_calls.append(seconds)
                return False

        runner = Runner(ingest_client=DummyClient([200]), adapter=DummyAdapter(), batch_size=10)
        stop_event = DummyStopEvent()
        source = SimpleNamespace(name="rss")

        with (
            patch.object(runner, "_run_source_once") as run_once,
            patch("scraper_bot.pipeline.runner.time.monotonic", side_effect=[100.0, 106.0]),
        ):
            runner._run_source_loop(source, interval_seconds=10, stop_event=stop_event)

        run_once.assert_called_once_with(source)
        self.assertEqual(stop_event.wait_calls, [4.0])

    def test_run_source_loop_skips_wait_when_cycle_exceeds_interval(self):
        class DummyStopEvent:
            def __init__(self):
                self._checks = 0
                self.wait_calls = []

            def is_set(self):
                self._checks += 1
                return self._checks > 1

            def wait(self, seconds):
                self.wait_calls.append(seconds)
                return False

        runner = Runner(ingest_client=DummyClient([200]), adapter=DummyAdapter(), batch_size=10)
        stop_event = DummyStopEvent()
        source = SimpleNamespace(name="solana")

        with (
            patch.object(runner, "_run_source_once") as run_once,
            patch("scraper_bot.pipeline.runner.time.monotonic", side_effect=[200.0, 216.5]),
        ):
            runner._run_source_loop(source, interval_seconds=10, stop_event=stop_event)

        run_once.assert_called_once_with(source)
        self.assertEqual(stop_event.wait_calls, [0.0])
