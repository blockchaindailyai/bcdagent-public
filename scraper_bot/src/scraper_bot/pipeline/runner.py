import logging
import threading
import time
from collections.abc import Callable
from collections.abc import Iterable

from scraper_bot.sources.registry import get_enabled_sources, get_supported_source_names
from scraper_bot.pipeline.batching import chunked

log = logging.getLogger(__name__)


class Runner:
    def __init__(
        self,
        ingest_client,
        adapter,
        batch_size,
        status_callback: Callable[[str, str, int, str, int, int], None] | None = None,
        enabled_source_names: Iterable[str] | None = None,
    ):
        self.ingest_client = ingest_client
        self.adapter = adapter
        self.batch_size = batch_size
        self.enabled_source_names = {str(x).strip().lower() for x in (enabled_source_names or []) if str(x).strip()} or None
        self._cycle_by_source: dict[str, int] = {}
        self._total_fetched_by_source: dict[str, int] = {}
        self._status_callback = status_callback
        self._cycle_lock = threading.Lock()

    def _set_status(
        self,
        source: str,
        state: str,
        cycle: int = 0,
        detail: str = "",
        fetched: int = 0,
        total_fetched: int = 0,
    ) -> None:
        if self._status_callback:
            try:
                self._status_callback(source, state, cycle, detail, fetched, total_fetched)
            except TypeError:
                self._status_callback(source, state, cycle, detail)

    def _available_sources(self):
        return get_enabled_sources(only=self.enabled_source_names)

    def _mark_not_enabled_sources(self, enabled_sources):
        available_sources = {s.name for s in enabled_sources}
        for source_name in get_supported_source_names():
            if source_name not in available_sources:
                self._set_status(source_name, "stopped", detail="not enabled")

    def run_once(self):
        sources = self._available_sources()
        self._mark_not_enabled_sources(sources)

        for s in sources:
            self._run_source_once(s)

    def run_continuously(self, interval_by_source: dict[str, int], stop_event: threading.Event | None = None) -> None:
        """
        Run each source on its own worker thread so modules poll independently.
        """
        local_stop_event = stop_event or threading.Event()
        sources = self._available_sources()
        self._mark_not_enabled_sources(sources)

        workers: list[threading.Thread] = []
        for source in sources:
            interval_seconds = max(1, int(interval_by_source.get(source.name, 60)))
            worker = threading.Thread(
                target=self._run_source_loop,
                name=f"scraper-source-{source.name}",
                args=(source, interval_seconds, local_stop_event),
                daemon=True,
            )
            worker.start()
            workers.append(worker)

        try:
            while not local_stop_event.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            log.info("runner.shutdown_requested")
            local_stop_event.set()
        finally:
            for worker in workers:
                worker.join(timeout=2)

    def _run_source_loop(self, source, interval_seconds: int, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            started_at = time.monotonic()
            self._run_source_once(source)
            elapsed = time.monotonic() - started_at
            wait_seconds = max(0.0, interval_seconds - elapsed)
            stop_event.wait(wait_seconds)

    def _run_source_once(self, source) -> None:
        cycle = self._next_cycle(source.name)
        items = []
        total_fetched = self._total_fetched_by_source.get(source.name, 0)
        self._set_status(source.name, "running", cycle=cycle, detail="fetching", total_fetched=total_fetched)
        try:
            fetched = source.fetch()
            total_fetched += len(fetched)
            self._total_fetched_by_source[source.name] = total_fetched
            self._set_status(
                source.name,
                "idle",
                cycle=cycle,
                fetched=len(fetched),
                total_fetched=total_fetched,
            )
        except Exception as e:
            log.exception("runner.source_failed cycle=%s source=%s", cycle, source.name)
            self._set_status(source.name, "error", cycle=cycle, detail=str(e), total_fetched=total_fetched)
            return

        items.extend(fetched)

        if not items:
            return

        events = self.adapter.to_intel_events(items)

        for batch in chunked(events, self.batch_size):
            self._ingest_batch(batch, cycle=cycle)


    def _rate_limit_wait_seconds(self, response, attempt: int) -> float:
        retry_after_value = response.headers.get("Retry-After") if hasattr(response, "headers") else None
        if retry_after_value:
            try:
                retry_after = float(retry_after_value)
                if retry_after >= 0:
                    return retry_after
            except (TypeError, ValueError):
                pass

        base_backoff = float(getattr(self.ingest_client, "rate_limit_backoff_seconds", 1.0))
        return max(0.0, base_backoff * (2**attempt))

    def _next_cycle(self, source_name: str) -> int:
        with self._cycle_lock:
            next_cycle = self._cycle_by_source.get(source_name, 0) + 1
            self._cycle_by_source[source_name] = next_cycle
            return next_cycle

    def _ingest_batch(self, events, *, cycle: int | None = None):
        max_rate_limit_retries = max(0, int(getattr(self.ingest_client, "max_rate_limit_retries", 0)))
        last_response = None

        for attempt in range(max_rate_limit_retries + 1):
            resp = self.ingest_client.post_events(events)
            last_response = resp
            if resp.status_code < 400:
                return

            if resp.status_code != 429:
                break

            if attempt >= max_rate_limit_retries:
                break

            wait_seconds = self._rate_limit_wait_seconds(resp, attempt)
            log.warning(
                "runner.ingest_rate_limited_retry cycle=%s status_code=%s batch_size=%s attempt=%s wait_seconds=%.2f",
                cycle if cycle is not None else "n/a",
                resp.status_code,
                len(events),
                attempt + 1,
                wait_seconds,
            )
            time.sleep(wait_seconds)

        if last_response is None:
            return

        if last_response.status_code == 429 and len(events) > 1:
            midpoint = len(events) // 2
            log.warning(
                "runner.ingest_rate_limited_split cycle=%s status_code=%s batch_size=%s retries=%s left_size=%s right_size=%s",
                cycle if cycle is not None else "n/a",
                last_response.status_code,
                len(events),
                max_rate_limit_retries,
                len(events[:midpoint]),
                len(events[midpoint:]),
            )
            self._ingest_batch(events[:midpoint], cycle=cycle)
            self._ingest_batch(events[midpoint:], cycle=cycle)
            return

        if last_response.status_code == 429:
            log.warning(
                "runner.ingest_rate_limited cycle=%s status_code=%s dropped_batch_size=%s retries=%s",
                cycle if cycle is not None else "n/a",
                last_response.status_code,
                len(events),
                max_rate_limit_retries,
            )
            return

        if last_response.status_code in {400, 413} and len(events) > 1:
            midpoint = len(events) // 2
            self._ingest_batch(events[:midpoint], cycle=cycle)
            self._ingest_batch(events[midpoint:], cycle=cycle)
            return

        if last_response.status_code == 400:
            log.warning(
                "runner.single_event_rejected cycle=%s status_code=%s",
                cycle if cycle is not None else "n/a",
                last_response.status_code,
            )
            return

        if last_response.status_code == 413:
            log.warning(
                "runner.single_event_payload_too_large cycle=%s status_code=%s",
                cycle if cycle is not None else "n/a",
                last_response.status_code,
            )
            return

        last_response.raise_for_status()
