import logging
import unittest

from scraper_bot.core.logging import StatusAwareHandler, TerminalStatus


class FakeTTY:
    def __init__(self):
        self.buffer = ""

    def isatty(self):
        return True

    def write(self, data):
        self.buffer += data

    def flush(self):
        return None


class TerminalStatusTest(unittest.TestCase):
    def test_persistent_errors_render_above_status_lines(self):
        status = TerminalStatus(["rss"])
        status._stream = FakeTTY()
        status._enabled = True

        status.render()
        status.write_message("boom", persistent=True)
        status.set_source("rss", "running", 1)

        self.assertEqual(status._persistent_errors, ["boom"])
        self.assertIn("boom\n", status._stream.buffer)
        self.assertIn("[rss] state=running (fetched=0 total=0) cycle=1\n", status._stream.buffer)

    def test_source_error_status_is_persisted_in_error_section(self):
        status = TerminalStatus(["rss"])
        status._stream = FakeTTY()
        status._enabled = True

        status.render()
        status.set_source("rss", "error", 1, "feed timeout")
        status.set_source("rss", "running", 2, "fetching")

        self.assertEqual(status._persistent_errors, ["[rss] feed timeout"])
        self.assertIn("[rss] feed timeout\n", status._stream.buffer)
        self.assertIn("[rss] state=running (fetched=0 total=0) cycle=2 fetching\n", status._stream.buffer)


    def test_status_block_has_two_blank_lines_before_bars(self):
        status = TerminalStatus(["rss", "tron", "ethereum"])
        status._stream = FakeTTY()
        status._enabled = True

        status.render()

        self.assertIn("\n\n[rss] state=stopped (fetched=0 total=0) cycle=0\n", status._stream.buffer)

    def test_duplicate_persistent_errors_are_deduplicated_when_consecutive(self):
        status = TerminalStatus(["rss"])
        status._stream = FakeTTY()
        status._enabled = True

        status.write_message("boom", persistent=True)
        status.write_message("boom", persistent=True)

        self.assertEqual(status._persistent_errors, ["boom"])

    def test_handler_persists_warning_and_error_logs(self):
        status = TerminalStatus(["rss"])
        status._stream = FakeTTY()
        status._enabled = True

        handler = StatusAwareHandler(status)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))

        handler.emit(logging.LogRecord("x", logging.INFO, __file__, 1, "hello", (), None))
        handler.emit(logging.LogRecord("x", logging.WARNING, __file__, 1, "careful", (), None))
        handler.emit(logging.LogRecord("x", logging.ERROR, __file__, 1, "bad", (), None))

        self.assertEqual(status._persistent_errors, ["WARNING careful", "ERROR bad"])


    def test_handler_updates_last_issue_on_source_status_line(self):
        status = TerminalStatus(["bnb"])
        status._stream = FakeTTY()
        status._enabled = True

        handler = StatusAwareHandler(status)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))

        status.set_source("bnb", "running", 9, "fetching")
        handler.emit(
            logging.LogRecord(
                "scraper_bot.sources.bnb_source",
                logging.WARNING,
                __file__,
                1,
                "bnb.rate_limited block=123 attempt=1/4 wait_seconds=0.50",
                (),
                None,
            )
        )

        self.assertIn("last_issue=WARNING bnb.rate_limited block=123 attempt=1/4 wait_seconds=0.50", status._stream.buffer)


    def test_handler_updates_last_issue_for_macro_source(self):
        status = TerminalStatus(["macro"])
        status._stream = FakeTTY()
        status._enabled = True

        handler = StatusAwareHandler(status)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))

        status.set_source("macro", "running", 2, "fetching")
        handler.emit(
            logging.LogRecord(
                "scraper_bot.sources.macro_source",
                logging.WARNING,
                __file__,
                1,
                "macro.bea_fetch_failed err=timeout",
                (),
                None,
            )
        )

        self.assertIn("last_issue=WARNING macro.bea_fetch_failed err=timeout", status._stream.buffer)


    def test_handler_updates_last_issue_for_sec_source(self):
        status = TerminalStatus(["sec"])
        status._stream = FakeTTY()
        status._enabled = True

        handler = StatusAwareHandler(status)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))

        status.set_source("sec", "running", 3, "fetching")
        handler.emit(
            logging.LogRecord(
                "scraper_bot.sources.sec_source",
                logging.WARNING,
                __file__,
                1,
                "sec.fetch_failed cik=0000320193 err=timeout",
                (),
                None,
            )
        )

        self.assertIn("last_issue=WARNING sec.fetch_failed cik=0000320193 err=timeout", status._stream.buffer)

    def test_nonpersistent_message_survives_status_rerender(self):
        status = TerminalStatus(["rss", "solana"])
        status._stream = FakeTTY()
        status._enabled = True

        status.render()
        status.write_message("starting scraper-bot")
        status.set_source("solana", "running", 1)

        self.assertIn("starting scraper-bot\n", status._stream.buffer)


if __name__ == "__main__":
    unittest.main()
