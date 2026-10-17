from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from scraper_bot.core.config import get_settings
from scraper_bot.schema.canonical import CanonicalItem
from scraper_bot.sources.base import SourceScraper


log = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def _as_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace(",", "")
    if not text:
        return 0.0

    if text.endswith("%"):
        text = text[:-1]

    try:
        return float(text)
    except ValueError:
        return 0.0


def _normalize_probability(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0

    if parsed > 1.0 and parsed <= 100.0:
        parsed = parsed / 100.0
    return max(0.0, min(1.0, parsed))


def _extract_label_values(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    if isinstance(value, dict):
        labels: list[str] = []
        for key in ("label", "slug", "name", "title"):
            normalized = str(value.get(key) or "").strip()
            if normalized:
                labels.append(normalized)
        return labels
    if isinstance(value, list):
        labels: list[str] = []
        for entry in value:
            labels.extend(_extract_label_values(entry))
        return labels
    return []


POLYMARKET_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Politics", ("politic", "government", "policy", "white house", "parliament", "senate", "congress")),
    ("Sports", ("sport", "nba", "nfl", "mlb", "nhl", "fifa", "ufc", "tennis", "soccer", "football", "baseball", "basketball")),
    ("Crypto", ("crypto", "bitcoin", "ethereum", "solana", "defi", "token", "airdrop", "blockchain")),
    ("Iran", ("iran", "tehran", "ayatollah", "irgc")),
    ("Finance", ("finance", "bank", "fed", "fomc", "interest rate", "treasury", "earnings")),
    ("Geopolitics", ("geopolitic", "war", "conflict", "sanction", "nato", "russia", "china", "israel", "ukraine", "middle east")),
    ("Tech", ("tech", "ai", "openai", "nvidia", "apple", "google", "microsoft", "meta")),
    ("Culture", ("culture", "celebrity", "movie", "film", "music", "grammy", "oscar", "tv", "entertainment")),
    ("Economy", ("economy", "inflation", "cpi", "gdp", "recession", "unemployment")),
    ("Climate & Science", ("climate", "science", "weather", "hurricane", "earthquake", "space", "nasa")),
    ("Mentions", ("mention", "mentions", "tweet", "x.com", "social media")),
    ("Elections", ("election", "vote", "ballot", "primary", "runoff", "electoral")),
)


def _infer_topics_from_text(*values: Any) -> list[str]:
    haystack = " ".join(str(v or "") for v in values).lower()
    if not haystack.strip():
        return []

    inferred: list[str] = []
    for topic, needles in POLYMARKET_CATEGORY_RULES:
        if any(needle in haystack for needle in needles):
            inferred.append(topic)
    return inferred


def _select_primary_category(tag_labels: list[str], inferred_topics: list[str]) -> str:
    normalized_tags = [str(t).strip() for t in tag_labels if str(t).strip()]

    for tag in normalized_tags:
        lower = tag.lower()
        for category, _ in POLYMARKET_CATEGORY_RULES:
            if lower == category.lower():
                return category

    if inferred_topics:
        return inferred_topics[0]

    for tag in normalized_tags:
        lower = tag.lower()
        for category, needles in POLYMARKET_CATEGORY_RULES:
            if any(n in lower for n in needles):
                return category

    return "Mentions"




@dataclass
class PolymarketState:
    market_fingerprints: dict[str, str]
    next_offset: int = 0

    @classmethod
    def load(cls, path: Path) -> "PolymarketState":
        if not path.exists():
            return cls(market_fingerprints={})
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            fingerprints = payload.get("market_fingerprints")
            if isinstance(fingerprints, dict):
                next_offset = payload.get("next_offset", 0)
                try:
                    normalized_offset = max(0, int(next_offset))
                except (TypeError, ValueError):
                    normalized_offset = 0
                return cls(
                    market_fingerprints={str(k): str(v) for k, v in fingerprints.items()},
                    next_offset=normalized_offset,
                )

            # backwards compatibility with old state shape
            keys = payload.get("seen_keys", [])
            return cls(market_fingerprints={str(k).split(":", 1)[0]: str(k) for k in keys if k}, next_offset=0)
        except Exception:
            return cls(market_fingerprints={}, next_offset=0)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "market_fingerprints": self.market_fingerprints,
                    "next_offset": max(0, int(self.next_offset)),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def has_fingerprint(self, market_id: str, fingerprint: str) -> bool:
        return self.market_fingerprints.get(market_id) == fingerprint

    def update_market(self, market_id: str, fingerprint: str, max_seen: int) -> None:
        self.market_fingerprints[market_id] = fingerprint
        if len(self.market_fingerprints) > max_seen:
            # Keep latest inserts, relying on insertion-order dict behavior.
            trimmed = list(self.market_fingerprints.items())[-max_seen:]
            self.market_fingerprints = dict(trimmed)


SCRIPT_ROOT = Path(__file__).resolve().parents[4]


class PolymarketSource(SourceScraper):
    @property
    def name(self) -> str:
        return "polymarket"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.api_url = self.settings.polymarket_events_api_url.strip()
        self.max_events = max(1, int(self.settings.polymarket_max_events))
        self.max_pages_per_cycle = max(1, int(self.settings.polymarket_max_pages_per_cycle))
        self.max_items_per_cycle = max(1, int(self.settings.polymarket_max_items_per_cycle))
        self.timeout = httpx.Timeout(int(self.settings.polymarket_http_timeout_seconds))
        self.state_path = Path(self.settings.polymarket_state_path)
        self.max_seen = max(1, int(self.settings.polymarket_max_seen_items))
        self.min_volume_usd = max(0.0, float(self.settings.polymarket_min_volume_usd))
        self.request_limit = self.max_events
        self.request_spacing_seconds = max(0.0, float(self.settings.polymarket_request_spacing_seconds))
        self.max_retries_per_page = max(0, int(self.settings.polymarket_max_retries_per_page))
        self.retry_backoff_seconds = max(0.0, float(self.settings.polymarket_retry_backoff_seconds))
        self.leaderboard_enabled = bool(self.settings.polymarket_leaderboard_enabled)
        self.leaderboard_output_path = SCRIPT_ROOT / Path(self.settings.polymarket_leaderboard_output_path)
        self.leaderboard_category = str(self.settings.polymarket_leaderboard_category or "overall")
        self.leaderboard_time_window = str(self.settings.polymarket_leaderboard_time_window or "monthly")
        configured_windows = str(self.settings.polymarket_leaderboard_time_windows or "").strip()
        self.leaderboard_time_windows = self._parse_time_windows(configured_windows, fallback=self.leaderboard_time_window)
        self.leaderboard_sort = str(self.settings.polymarket_leaderboard_sort or "profit")
        self.leaderboard_top_n = max(1, int(self.settings.polymarket_leaderboard_top_n))

    def _parse_time_windows(self, value: str, fallback: str) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in (value or "").split(","):
            window = str(raw or "").strip().lower()
            if not window or window in seen:
                continue
            out.append(window)
            seen.add(window)

        fallback_window = str(fallback or "monthly").strip().lower() or "monthly"
        if not out:
            return [fallback_window]
        if fallback_window not in seen:
            out.append(fallback_window)
        return out

    def fetch(self) -> list[CanonicalItem]:
        now = _utc_now()
        state = PolymarketState.load(self.state_path)

        out: list[CanonicalItem] = []
        next_allowed_request_at = 0.0
        offset = max(0, int(state.next_offset))
        start_offset = offset
        wrapped_to_start = False
        capped = False

        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers={"User-Agent": "scraper-bot/0.1"}) as client:
            for _ in range(self.max_pages_per_cycle):
                self._apply_request_spacing(next_allowed_request_at)
                page = self._fetch_events_page(client, offset)
                if isinstance(page, tuple):
                    events, row_count = page
                else:
                    events, row_count = page, len(page)
                next_allowed_request_at = time.monotonic() + self.request_spacing_seconds

                if not events:
                    if start_offset > 0 and offset > 0 and not wrapped_to_start:
                        offset = 0
                        wrapped_to_start = True
                        continue
                    break

                for ev in events:
                    if not isinstance(ev, dict):
                        continue
                    out.extend(self._event_to_items(ev, now, state))

                # Keep paginating until API returns an empty page. Some Polymarket endpoints can
                # return short pages before exhaustion, so short-page termination drops active markets.
                if row_count <= 0:
                    break

                offset += row_count

                if wrapped_to_start and start_offset > 0 and offset >= start_offset:
                    break

                if len(out) >= self.max_items_per_cycle:
                    capped = True
                    log.info(
                        "polymarket.max_items_per_cycle_reached limit=%s stopping_pagination=true",
                        self.max_items_per_cycle,
                    )
                    out = out[: self.max_items_per_cycle]
                    break

        state.next_offset = offset if capped else 0
        state.save(self.state_path)
        self._refresh_leaderboard_snapshot()
        return out


    def _fetch_events_page(self, client: httpx.Client, offset: int) -> tuple[list[dict[str, Any]], int]:
        for attempt in range(self.max_retries_per_page + 1):
            response = client.get(self.api_url, params={"offset": offset, "limit": self.request_limit})
            if response.status_code != 429:
                response.raise_for_status()
                events = response.json()

                if not isinstance(events, list):
                    return [], 0

                row_count = len(events)

                if events and isinstance(events[0], dict) and "markets" not in events[0]:
                    return self._coerce_market_rows_to_event_rows(events), row_count

                return events, row_count

            if attempt >= self.max_retries_per_page:
                response.raise_for_status()

            wait_seconds = self._retry_delay_seconds(response, attempt)
            log.warning("polymarket.rate_limited offset=%s attempt=%s wait_seconds=%.2f", offset, attempt + 1, wait_seconds)
            time.sleep(wait_seconds)

        return [], 0

    def _retry_delay_seconds(self, response: httpx.Response, attempt: int) -> float:
        retry_after_value = response.headers.get("Retry-After")
        if retry_after_value:
            try:
                retry_after = float(retry_after_value)
                if retry_after >= 0:
                    return retry_after
            except ValueError:
                pass

        return self.retry_backoff_seconds * (2**attempt)

    def _apply_request_spacing(self, next_allowed_request_at: float) -> None:
        if self.request_spacing_seconds <= 0:
            return

        now = time.monotonic()
        if now < next_allowed_request_at:
            time.sleep(next_allowed_request_at - now)

    def _coerce_market_rows_to_event_rows(self, market_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for market in market_rows:
            event_rows = market.get("events") if isinstance(market.get("events"), list) else []
            event = event_rows[0] if event_rows and isinstance(event_rows[0], dict) else {}

            event_id = str(event.get("id") or market.get("eventId") or f"market-{market.get('id')}")
            if event_id not in grouped:
                grouped[event_id] = {
                    "id": event.get("id") or market.get("eventId") or "",
                    "title": event.get("title") or market.get("question") or "Polymarket event",
                    "slug": event.get("slug") or market.get("eventSlug") or "",
                    "startDate": event.get("startDate") or market.get("startDate"),
                    "updatedAt": event.get("updatedAt") or market.get("updatedAt"),
                    "tags": event.get("tags") if isinstance(event.get("tags"), list) else [],
                    "series": event.get("series") if isinstance(event.get("series"), list) else [],
                    "markets": [],
                }

            grouped[event_id]["tags"].extend(
                [
                    entry
                    for source_event in event_rows
                    if isinstance(source_event, dict)
                    for entry in (source_event.get("tags") or [])
                ]
            )
            grouped[event_id]["markets"].append(market)

        return list(grouped.values())

    def _build_market_url(self, event: dict[str, Any], market: dict[str, Any]) -> str | None:
        for value in (market.get("url"), event.get("url")):
            if value:
                return str(value)

        event_slug = str(event.get("slug") or market.get("eventSlug") or "").strip()
        market_slug = str(market.get("slug") or "").strip()

        if event_slug and market_slug:
            return f"https://polymarket.com/event/{event_slug}/{market_slug}"
        if event_slug:
            return f"https://polymarket.com/event/{event_slug}"
        if market_slug:
            return f"https://polymarket.com/market/{market_slug}"
        return None

    def _read_market_probabilities(self, market: dict[str, Any]) -> list[float]:
        prices = [_normalize_probability(v) for v in _read_json_list(market.get("outcomePrices"))]

        if prices and any(p > 0 for p in prices):
            return prices

        if len(prices) == 2:
            fallback_yes = _normalize_probability(market.get("lastTradePrice"))
            if fallback_yes > 0:
                return [fallback_yes, max(0.0, min(1.0, 1.0 - fallback_yes))]

        return prices

    def _event_to_items(self, event: dict[str, Any], now: datetime, state: PolymarketState) -> list[CanonicalItem]:
        event_tag_labels = _extract_label_values(event.get("tags", []))


        series_rows = event.get("series") if isinstance(event.get("series"), list) else []
        for series in series_rows:
            if not isinstance(series, dict):
                continue
            for key in ("title", "slug"):
                value = str(series.get(key) or "").strip()
                if value:
                    event_tag_labels.append(value)

        markets = event.get("markets", [])
        if not isinstance(markets, list):
            return []

        items: list[CanonicalItem] = []
        for market in markets:
            if not isinstance(market, dict):
                continue

            tag_labels = list(event_tag_labels)
            for key in ("tags", "category", "categorySlug", "groupItemTitle", "groupItemSlug"):
                tag_labels.extend(_extract_label_values(market.get(key)))

            market_id = str(market.get("id") or "")
            updated_at = str(market.get("updatedAt") or event.get("updatedAt") or "")

            volume = 0.0
            try:
                volume = float(market.get("volumeNum") or market.get("volume") or 0.0)
            except (TypeError, ValueError):
                volume = 0.0

            if volume < self.min_volume_usd:
                continue

            fingerprint = self._market_fingerprint(event, market)
            if not market_id or state.has_fingerprint(market_id, fingerprint):
                continue

            outcomes = [str(x) for x in _read_json_list(market.get("outcomes"))]
            prices = self._read_market_probabilities(market)

            best_idx = 0
            for i in range(1, min(len(prices), len(outcomes))):
                if prices[i] > prices[best_idx]:
                    best_idx = i

            top_label = outcomes[best_idx] if outcomes else ""
            top_prob = prices[best_idx] if prices else 0.0

            market_slug = str(market.get("slug") or "")
            market_url = self._build_market_url(event, market)
            derived_topics = self._derive_market_topics(tag_labels)
            inferred_topics = _infer_topics_from_text(
                market.get("question"),
                market.get("slug"),
                event.get("title"),
                event.get("slug"),
                market.get("groupItemTitle"),
                market.get("category"),
            )
            if inferred_topics:
                derived_topics = self._derive_market_topics([*derived_topics, *inferred_topics])
            primary_category = _select_primary_category(tag_labels, inferred_topics)

            top_prob_pct = f"{top_prob * 100:.1f}%"

            item = CanonicalItem(
                item_type="polymarket_market",
                title=str(market.get("question") or event.get("title") or "Polymarket market"),
                summary=f"{event.get('title', '')} | Top outcome {top_label} ({top_prob_pct}) | Volume ${volume:,.0f}",
                url=market_url,
                published_at=_parse_dt(str(event.get("startDate") or "")),
                scraped_at=now,
                source="polymarket",
                tags=derived_topics,
                classification={"assetClass": "general", "topics": [t.lower() for t in derived_topics if t]},
                data={
                    "eventId": str(event.get("id") or ""),
                    "eventTitle": str(event.get("title") or ""),
                    "eventSlug": str(event.get("slug") or ""),
                    "eventTags": [str(t) for t in tag_labels if str(t).strip()],
                    "eventSeries": [str(series.get("title") or series.get("slug") or "").strip() for series in series_rows if isinstance(series, dict)],
                    "eventCategory": primary_category,
                    "inferredTopics": inferred_topics,
                    "marketId": market_id,
                    "marketSlug": market_slug,
                    "volume": volume,
                    "topOutcome": top_label,
                    "topOutcomeProbability": max(0.0, min(1.0, top_prob)),
                    "outcomes": outcomes,
                    "outcomePrices": prices,
                    "updatedAt": updated_at,
                },
            )
            items.append(item)
            state.update_market(market_id, fingerprint, self.max_seen)

        return items

    def _refresh_leaderboard_snapshot(self) -> None:
        if not self.leaderboard_enabled:
            return

        requested_sort = self.leaderboard_sort.strip().lower()
        if requested_sort not in {"profit", "volume"}:
            log.warning("polymarket.leaderboard_invalid_sort sort=%s", self.leaderboard_sort)
            return

        snapshots: dict[str, dict[str, Any]] = {}
        primary_snapshot: dict[str, Any] | None = None

        for time_window in self.leaderboard_time_windows:
            page_url = self._build_leaderboard_page_url(self.leaderboard_category, time_window, requested_sort)
            try:
                html = self._fetch_html(page_url)
                next_data = self._extract_next_data_json(html)
                rows = self._extract_leaderboard_rows(next_data, expected_sort=requested_sort)
                top_rows = self._format_leaderboard_rows(rows, self.leaderboard_top_n)
                snapshot = {
                    "source": "polymarket_leaderboard_page",
                    "pageUrl": page_url,
                    "category": self.leaderboard_category,
                    "time": time_window,
                    "sort": requested_sort,
                    "scrapedAt": _utc_now().isoformat(),
                    "rowCount": len(top_rows),
                    "rows": top_rows,
                }
                snapshots[time_window] = snapshot
                if primary_snapshot is None or time_window == self.leaderboard_time_window.strip().lower():
                    primary_snapshot = snapshot
            except Exception as exc:
                log.warning("polymarket.leaderboard_refresh_failed time_window=%s error=%s", time_window, exc)

        if not snapshots or not primary_snapshot:
            return

        payload = dict(primary_snapshot)
        payload["availableTimes"] = list(snapshots.keys())
        payload["snapshots"] = snapshots
        self._write_json(self.leaderboard_output_path, payload)

    def _fetch_html(self, url: str) -> str:
        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers={"User-Agent": "scraper-bot/0.1"}) as client:
            response = client.get(url)
        response.raise_for_status()
        return response.text

    def _extract_next_data_json(self, html: str) -> dict[str, Any]:
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>',
            html,
            flags=re.DOTALL,
        )
        if not match:
            raise ValueError("Unable to locate __NEXT_DATA__ script payload in Polymarket page HTML.")
        return json.loads(match.group(1))

    def _extract_query_sort(self, query: dict[str, Any]) -> str:
        key = query.get("queryKey")
        if not isinstance(key, list):
            return ""

        for value in key:
            normalized = str(value or "").strip().lower()
            if normalized in {"profit", "volume"}:
                return normalized

        return ""

    def _extract_leaderboard_rows(self, next_data: dict[str, Any], expected_sort: str | None = None) -> list[dict[str, Any]]:
        normalized_expected_sort = str(expected_sort or "").strip().lower()
        queries = (((next_data.get("props") or {}).get("pageProps") or {}).get("dehydratedState") or {}).get("queries") or []

        fallback_rows: list[dict[str, Any]] = []
        for query in queries:
            query_sort = self._extract_query_sort(query)
            data = ((query.get("state") or {}).get("data"))
            if isinstance(data, list) and data and isinstance(data[0], dict) and "rank" in data[0] and "proxyWallet" in data[0]:
                if normalized_expected_sort and query_sort == normalized_expected_sort:
                    return data
                if not fallback_rows:
                    fallback_rows = data

        if fallback_rows:
            return fallback_rows

        raise ValueError("Unable to find leaderboard rows in __NEXT_DATA__ dehydrated state.")

    def _format_leaderboard_rows(self, rows: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
        selected = rows[: max(1, int(top_n))]
        out: list[dict[str, Any]] = []
        for row in selected:
            pnl = _as_float(row.get("pnl") or row.get("profit"))
            volume = _as_float(row.get("volume") or row.get("amount") or row.get("notional"))
            pnl_percent = _as_float(
                row.get("pnlPercent")
                or row.get("pnlPct")
                or row.get("profitPercent")
                or row.get("profitPct")
                or row.get("roi")
            )
            if pnl_percent == 0.0 and volume > 0:
                pnl_percent = (pnl / volume) * 100.0

            out.append(
                {
                    "rank": int(row.get("rank") or 0),
                    "name": str(row.get("name") or row.get("pseudonym") or ""),
                    "xUsername": str(row.get("xUsername") or ""),
                    "proxyWallet": str(row.get("proxyWallet") or ""),
                    "pnl": pnl,
                    "pnlPercent": pnl_percent,
                    "volume": volume,
                    "realized": _as_float(row.get("realized")),
                    "unrealized": _as_float(row.get("unrealized")),
                    "profileImage": str(row.get("profileImage") or ""),
                }
            )
        return out

    def _build_leaderboard_page_url(self, category: str, time_window: str, sort: str) -> str:
        base = "https://polymarket.com/leaderboard"
        return f"{base}/{category.strip().lower()}/{time_window.strip().lower()}/{sort.strip().lower()}"

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _market_fingerprint(self, event: dict[str, Any], market: dict[str, Any]) -> str:
        payload = {
            "eventUpdatedAt": event.get("updatedAt"),
            "marketUpdatedAt": market.get("updatedAt"),
            "question": market.get("question"),
            "active": market.get("active"),
            "closed": market.get("closed"),
            "archived": market.get("archived"),
            "resolved": market.get("resolved"),
            "acceptingOrders": market.get("acceptingOrders"),
            "outcomePrices": _read_json_list(market.get("outcomePrices")),
            "lastTradePrice": market.get("lastTradePrice"),
            "volume": market.get("volumeNum") or market.get("volume"),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _derive_market_topics(self, tag_labels: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for topic in tag_labels:
            key = str(topic).strip().lower()
            if not key or key in seen:
                continue
            deduped.append(str(topic).strip())
            seen.add(key)
        return deduped
