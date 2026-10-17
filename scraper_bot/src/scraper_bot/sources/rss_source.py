from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Optional

import feedparser
import httpx

from scraper_bot.core.config import get_settings
from scraper_bot.schema.canonical import CanonicalItem
from scraper_bot.sources.base import SourceScraper

log = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_dt(entry) -> Optional[datetime]:
    """
    feedparser may provide:
      - entry.published_parsed / updated_parsed (time.struct_time)
      - entry.published / updated (string)
    Prefer *_parsed for reliability.
    """
    tp = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if tp:
        return datetime(*tp[:6], tzinfo=timezone.utc)
    return None


def _entry_id(entry) -> str:
    # Prefer explicit guid/id; else link; else title hash fallback
    for k in ("id", "guid", "link"):
        v = getattr(entry, k, None)
        if v:
            return str(v).strip()
    title = getattr(entry, "title", "") or ""
    return f"noid:{hash(title)}"


def _clean_text(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s2 = str(s).strip()
    return s2 or None


def _strip_html(html: Optional[str]) -> Optional[str]:
    """
    Convert RSS summary/description HTML into readable plaintext.
    Removes <style>, <script>, all tags, decodes entities, collapses whitespace.
    """
    if not html:
        return None
    s = str(html)

    # Remove script/style blocks
    s = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", s)

    # Convert common line breaks
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</p\s*>", "\n", s)
    s = re.sub(r"(?i)<p\s*>", "", s)

    # Remove all remaining tags
    s = re.sub(r"(?s)<.*?>", " ", s)

    # Decode HTML entities
    s = unescape(s)

    # Collapse whitespace
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    s = s.strip()

    return s or None


def _clean_tickers_basic(text: str) -> list[str]:
    """
    Conservative ticker extraction for early MVP.
    - Extracts $BTC style reliably
    - Extracts a very limited set of ALLCAPS 2-5 letter tokens as candidates
    """
    if not text:
        return []
    candidates = set()

    # $BTC style
    for m in re.findall(r"\$([A-Za-z][A-Za-z0-9]{1,9})", text):
        candidates.add(m.upper())

    # Conservative bare tickers: ALLCAPS 2-5 letters surrounded by non-alnum
    for m in re.findall(r"(?<![A-Z0-9])([A-Z]{2,5})(?![A-Z0-9])", text):
        if m in {"THE", "AND", "WITH", "FROM", "THIS", "THAT", "WILL", "COIN", "NEWS"}:
            continue
        candidates.add(m)

    return sorted(candidates)


@dataclass
class RssState:
    seen_by_feed: dict[str, list[str]]

    @classmethod
    def load(cls, path: Path) -> "RssState":
        if not path.exists():
            return cls(seen_by_feed={})
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(seen_by_feed=dict(data.get("seen_by_feed", {})))
        except Exception:
            # If corrupted, start fresh
            return cls(seen_by_feed={})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"seen_by_feed": self.seen_by_feed}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def has_seen(self, feed_url: str, entry_id: str) -> bool:
        return entry_id in set(self.seen_by_feed.get(feed_url, []))

    def mark_seen(self, feed_url: str, entry_id: str, max_seen: int) -> None:
        lst = self.seen_by_feed.get(feed_url, [])
        lst.append(entry_id)
        if len(lst) > max_seen:
            lst = lst[-max_seen:]
        self.seen_by_feed[feed_url] = lst


class RssSource(SourceScraper):
    @property
    def name(self) -> str:
        return "rss"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.feed_urls = [u.strip() for u in (self.settings.rss_feeds or "").split(",") if u.strip()]
        self.state_path = Path(self.settings.rss_state_path)
        self.max_seen = int(self.settings.rss_max_seen_per_feed)
        self.timeout = httpx.Timeout(int(self.settings.rss_http_timeout_seconds))

    def fetch(self) -> list[CanonicalItem]:
        log.debug(
            "rss.cycle_started feeds=%s state_path=%s",
            len(self.feed_urls),
            self.state_path,
        )

        if not self.feed_urls:
            log.debug("rss.no_feeds_configured")
            return []

        state = RssState.load(self.state_path)
        out: list[CanonicalItem] = []

        with httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,  # FIX: coindesk 308
            headers={"User-Agent": "scraper-bot/0.1"},
        ) as client:
            for feed_url in self.feed_urls:
                log.debug("rss.feed_poll_started feed=%s", feed_url)
                try:
                    resp = client.get(feed_url)
                    resp.raise_for_status()
                    parsed = feedparser.parse(resp.text)
                except Exception as e:
                    log.warning(f"rss.fetch_failed feed={feed_url} err={e}")
                    continue

                entries = getattr(parsed, "entries", []) or []
                new_count = 0

                for entry in entries:
                    eid = _entry_id(entry)
                    if state.has_seen(feed_url, eid):
                        continue

                    published_at = _to_dt(entry)
                    now = _utc_now()

                    title = _clean_text(getattr(entry, "title", None)) or "Untitled"
                    link = _clean_text(getattr(entry, "link", None))

                    raw_summary = getattr(entry, "summary", None) or getattr(entry, "description", None)
                    summary = _strip_html(_clean_text(raw_summary))

                    tickers = _clean_tickers_basic(f"{title} {summary or ''}")

                    classification = {
                        "priority": "normal",
                        "assetClass": "crypto",      # Bundle A default
                        "subcategory": "general",
                        "tickers": tickers,
                        "topics": [],
                        "regions": [],
                    }

                    item = CanonicalItem(
                        item_type="news",
                        title=title,
                        summary=summary,
                        url=link,
                        # IMPORTANT: for live feed ordering, use scrape time for ts by forcing published_at=None
                        published_at=published_at,
                        scraped_at=now,
                        source="rss",
                        tags=["rss"],
                        severity="normal",
                        classification=classification,
                        data={
                            "feedUrl": feed_url,
                            "entryId": eid,
                            "raw": {
                                "title": getattr(entry, "title", None),
                                "link": getattr(entry, "link", None),
                                "published": getattr(entry, "published", None),
                                "updated": getattr(entry, "updated", None),
                            },
                        }
                    )

                    out.append(item)
                    state.mark_seen(feed_url, eid, self.max_seen)
                    new_count += 1

                log.debug(f"rss.feed_done feed={feed_url} entries={len(entries)} new={new_count}")

        state.save(self.state_path)
        log.debug(f"rss.total_new={len(out)}")
        return out
