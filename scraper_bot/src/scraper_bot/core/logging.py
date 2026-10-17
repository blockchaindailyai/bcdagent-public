import logging
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class SourceStatus:
    state: str = "stopped"
    cycle: int = 0
    fetched: int = 0
    total_fetched: int = 0
    detail: str = ""
    issue: str = ""
    updated_at: datetime | None = None


class TerminalStatus:
    STATUS_TOP_PADDING_LINES = 2

    def __init__(self, source_names: list[str]) -> None:
        self._source_names = source_names
        self._status_by_source = {name: SourceStatus() for name in source_names}
        self._stream = sys.stdout
        self._enabled = self._stream.isatty()
        self._rendered_once = False
        self._status_line_count = 0
        self._io_lock = threading.RLock()
        self._persistent_errors: list[str] = []

    def set_source(
        self,
        source: str,
        state: str,
        cycle: int,
        detail: str = "",
        fetched: int = 0,
        total_fetched: int = 0,
    ) -> None:
        with self._io_lock:
            if source not in self._status_by_source:
                return
            self._status_by_source[source] = SourceStatus(
                state=state,
                cycle=cycle,
                fetched=fetched,
                total_fetched=total_fetched,
                detail=detail,
                issue=self._status_by_source[source].issue,
                updated_at=datetime.now(timezone.utc),
            )
            if state == "error" and detail:
                self.write_message(f"[{source}] {detail}", persistent=True)
                return
            self.render()


    def set_source_issue(self, source: str, issue: str) -> None:
        with self._io_lock:
            if source not in self._status_by_source:
                return
            status = self._status_by_source[source]
            trimmed_issue = issue.strip()
            if len(trimmed_issue) > 140:
                trimmed_issue = f"{trimmed_issue[:137]}..."
            self._status_by_source[source] = SourceStatus(
                state=status.state,
                cycle=status.cycle,
                fetched=status.fetched,
                total_fetched=status.total_fetched,
                detail=status.detail,
                issue=trimmed_issue,
                updated_at=datetime.now(timezone.utc),
            )
            self.render()

    def write_message(self, message: str, persistent: bool = False) -> None:
        with self._io_lock:
            if persistent:
                did_append = self._append_persistent_error(message)
                if not did_append:
                    return

            if self._enabled and self._rendered_once:
                self._clear_status_area()

            self._stream.write(f"{message}\n")
            self._stream.flush()

            if self._enabled:
                self.render()

    def render(self) -> None:
        with self._io_lock:
            if not self._enabled:
                return

            if self._rendered_once:
                self._clear_status_area()

            lines = self._render_lines()
            self._stream.write("\n".join(lines) + "\n")
            self._stream.flush()
            self._status_line_count = len(lines)
            self._rendered_once = True

    def _render_lines(self) -> list[str]:
        return (
            [*self._persistent_errors]
            + ["" for _ in range(self.STATUS_TOP_PADDING_LINES)]
            + [self._format_line(name) for name in self._source_names]
        )

    def _append_persistent_error(self, message: str) -> bool:
        if not message:
            return False
        if self._persistent_errors and self._persistent_errors[-1] == message:
            return False
        self._persistent_errors.append(message)
        return True

    def _clear_status_area(self) -> None:
        if self._status_line_count <= 0:
            return

        self._stream.write(f"\x1b[{self._status_line_count}F")
        for _ in range(self._status_line_count):
            self._stream.write("\x1b[2K\x1b[1E")
        self._stream.write(f"\x1b[{self._status_line_count}F")

    def _format_line(self, source: str) -> str:
        status = self._status_by_source[source]
        detail = f" {status.detail}" if status.detail else ""
        issue = f" | last_issue={status.issue}" if status.issue else ""
        return (
            f"[{source}] state={status.state} (fetched={status.fetched} total={status.total_fetched})"
            f" cycle={status.cycle}{detail}{issue}"
        )


class StatusAwareHandler(logging.Handler):
    SOURCE_NAME_BY_LOGGER = {
        "scraper_bot.sources.rss_source": "rss",
        "scraper_bot.sources.tron_source": "tron",
        "scraper_bot.sources.eth_source": "ethereum",
        "scraper_bot.sources.solana_source": "solana",
        "scraper_bot.sources.bnb_source": "bnb",
        "scraper_bot.sources.xrp_source": "xrp",
        "scraper_bot.sources.macro_source": "macro",
        "scraper_bot.sources.sec_source": "sec",
    }

    def __init__(self, terminal_status: TerminalStatus) -> None:
        super().__init__()
        self.terminal_status = terminal_status

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            is_warning_or_error = record.levelno >= logging.WARNING
            self.terminal_status.write_message(msg, persistent=is_warning_or_error)
            if is_warning_or_error:
                source_name = self.SOURCE_NAME_BY_LOGGER.get(record.name)
                if source_name:
                    self.terminal_status.set_source_issue(source_name, msg)
        except Exception:
            self.handleError(record)


def configure_logging(level: str, *, log_httpx_requests: bool = False, terminal_status: TerminalStatus | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
    if terminal_status is None:
        handler: logging.Handler = logging.StreamHandler()
    else:
        handler = StatusAwareHandler(terminal_status)
    handler.setFormatter(formatter)
    root.addHandler(handler)

    if not log_httpx_requests:
        # Keep terminal output focused on scraper lifecycle and warnings/errors.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
