from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import feedparser
import httpx

from scraper_bot.core.config import get_settings
from scraper_bot.schema.canonical import CanonicalItem
from scraper_bot.sources.base import SourceScraper

log = logging.getLogger(__name__)


FRED_SERIES_LABELS: dict[str, str] = {
    "FEDFUNDS": "Federal Funds Effective Rate",
    "CPIAUCSL": "Consumer Price Index (All Urban Consumers)",
    "ICSA": "Initial Jobless Claims",
    "UNRATE": "Unemployment Rate",
}

BLS_SERIES_LABELS: dict[str, str] = {
    "CUUR0000SA0": "Consumer Price Index (CPI-U, U.S. city average)",
    "LNS14000000": "Unemployment Rate",
}

BEA_TABLE_LABELS: dict[str, str] = {
    "T10105": "Gross Domestic Product",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_number(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = _clean_text(value).replace(",", "")
    if not cleaned or cleaned in {"(NA)", "NA"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _hashable_id(*parts: str) -> str:
    safe_parts = [re.sub(r"\s+", " ", p.strip()) for p in parts if p and p.strip()]
    return "|".join(safe_parts)


def _format_numeric_value(raw_value: str) -> str:
    numeric_value = _normalize_number(raw_value)
    if numeric_value is None:
        return raw_value
    if numeric_value.is_integer():
        return f"{int(numeric_value):,}"
    return f"{numeric_value:,.3f}".rstrip("0").rstrip(".")


def _label_with_code(label_map: dict[str, str], code: str) -> str:
    label = label_map.get(code, "")
    if not label:
        return code
    return f"{label} ({code})"


@dataclass
class MacroState:
    seen_ids: list[str]

    @classmethod
    def load(cls, path: Path) -> "MacroState":
        if not path.exists():
            return cls(seen_ids=[])
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return cls(seen_ids=list(payload.get("seen_ids", [])))
        except Exception:
            return cls(seen_ids=[])

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"seen_ids": self.seen_ids}, ensure_ascii=False, indent=2), encoding="utf-8")

    def has_seen(self, item_id: str) -> bool:
        return item_id in set(self.seen_ids)

    def mark_seen(self, item_id: str, max_seen: int) -> None:
        self.seen_ids.append(item_id)
        if len(self.seen_ids) > max_seen:
            self.seen_ids = self.seen_ids[-max_seen:]


class MacroSource(SourceScraper):
    @property
    def name(self) -> str:
        return "macro"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.timeout = httpx.Timeout(int(self.settings.macro_http_timeout_seconds))
        self.state_path = Path(self.settings.macro_state_path)
        self.max_seen = int(self.settings.macro_max_seen_items)
        self.fred_series_ids = [x.strip() for x in self.settings.macro_fred_series_ids.split(",") if x.strip()]
        self.bls_series_ids = [x.strip() for x in self.settings.macro_bls_series_ids.split(",") if x.strip()]
        self.bea_table_name = self.settings.macro_bea_table_name.strip()
        self.bea_line_number = str(self.settings.macro_bea_line_number).strip()
        self.fed_releases_feed_url = self.settings.macro_fed_releases_feed_url.strip()
        self.dol_claims_url = self.settings.macro_dol_claims_url.strip()
        self.treasury_yield_curve_url = self.settings.macro_treasury_yield_curve_url.strip()
        self.bls_min_interval = timedelta(seconds=max(0, int(self.settings.macro_bls_min_interval_seconds)))
        self.bea_min_interval = timedelta(seconds=max(0, int(self.settings.macro_bea_min_interval_seconds)))
        self._last_bls_attempt_at: datetime | None = None
        self._last_bea_attempt_at: datetime | None = None

    def _request_json_with_retry(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        *,
        attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return client.request(method, url, **kwargs).json()
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if attempt >= attempts:
                    break
                time.sleep(retry_backoff_seconds * attempt)
        if last_exc is None:
            raise RuntimeError("macro.retry_failed_without_exception")
        raise last_exc

    def fetch(self) -> list[CanonicalItem]:
        state = MacroState.load(self.state_path)
        out: list[CanonicalItem] = []
        now = _utc_now()

        headers = {"User-Agent": "scraper-bot/0.1"}
        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers=headers) as client:
            out.extend(self._fetch_fred(client, state, now))
            out.extend(self._fetch_bls(client, state, now))
            out.extend(self._fetch_bea(client, state, now))
            out.extend(self._fetch_dol_eta(client, state, now))
            out.extend(self._fetch_treasury(client, state, now))
            out.extend(self._fetch_fed_releases(client, state, now))

        if out:
            state.save(self.state_path)
        return out

    def _base_item(self, *, title: str, summary: str, url: str, published_at: datetime | None, now: datetime, tags: list[str], data: dict[str, Any]) -> CanonicalItem:
        return CanonicalItem(
            item_type="macro",
            title=title,
            summary=summary,
            url=url,
            published_at=published_at,
            scraped_at=now,
            source="macro",
            tags=tags,
            severity="normal",
            classification={
                "priority": "normal",
                "assetClass": "macro",
                "subcategory": "government-data",
                "tickers": [],
                "topics": ["macro", "rates", "inflation", "labor"],
                "regions": ["US"],
            },
            data=data,
        )

    def _fetch_fred(self, client: httpx.Client, state: MacroState, now: datetime) -> list[CanonicalItem]:
        if not self.settings.fred_api_key or not self.fred_series_ids:
            return []
        items: list[CanonicalItem] = []
        for series_id in self.fred_series_ids:
            url = (
                "https://api.stlouisfed.org/fred/series/observations"
                f"?series_id={quote_plus(series_id)}"
                f"&api_key={quote_plus(self.settings.fred_api_key)}"
                "&sort_order=desc&limit=1&file_type=json"
            )
            try:
                payload = client.get(url).json()
                observations = payload.get("observations", [])
                if not observations:
                    continue
                obs = observations[0]
                observed_value = _clean_text(obs.get("value"))
                observed_date = _clean_text(obs.get("date"))
                item_id = _hashable_id("fred", series_id, observed_date, observed_value)
                if state.has_seen(item_id):
                    continue

                item = self._base_item(
                    title=f"FRED — {_label_with_code(FRED_SERIES_LABELS, series_id)}: {_format_numeric_value(observed_value)}",
                    summary=(
                        f"Latest {_label_with_code(FRED_SERIES_LABELS, series_id)} reading: "
                        f"{_format_numeric_value(observed_value)} on {observed_date}."
                    ),
                    url="https://fred.stlouisfed.org/",
                    published_at=None,
                    now=now,
                    tags=["macro", "fred"],
                    data={
                        "provider": "FRED",
                        "seriesId": series_id,
                        "observedDate": observed_date,
                        "value": _normalize_number(observed_value),
                        "rawValue": observed_value,
                        "id": item_id,
                    },
                )
                items.append(item)
                state.mark_seen(item_id, self.max_seen)
            except Exception as exc:
                log.warning("macro.fred_fetch_failed series=%s err=%s", series_id, exc)
        return items

    def _fetch_bls(self, client: httpx.Client, state: MacroState, now: datetime) -> list[CanonicalItem]:
        if not self.bls_series_ids:
            return []
        if self._last_bls_attempt_at and now - self._last_bls_attempt_at < self.bls_min_interval:
            return []
        self._last_bls_attempt_at = now
        body: dict[str, Any] = {
            "seriesid": self.bls_series_ids,
            "startyear": str(max(2000, now.year - 1)),
            "endyear": str(now.year),
        }
        if self.settings.bls_api_key:
            body["registrationkey"] = self.settings.bls_api_key

        try:
            payload = self._request_json_with_retry(
                client,
                "POST",
                "https://api.bls.gov/publicAPI/v2/timeseries/data/",
                json=body,
            )
        except Exception as exc:
            log.warning("macro.bls_fetch_failed err=%s", exc)
            return []

        items: list[CanonicalItem] = []
        for series in payload.get("Results", {}).get("series", []):
            series_id = _clean_text(series.get("seriesID"))
            metric_name = _label_with_code(BLS_SERIES_LABELS, series_id)
            datapoints = series.get("data", [])
            if not datapoints:
                continue
            point = datapoints[0]
            year = _clean_text(point.get("year"))
            period = _clean_text(point.get("periodName"))
            raw_value = _clean_text(point.get("value"))
            item_id = _hashable_id("bls", series_id, year, period, raw_value)
            if state.has_seen(item_id):
                continue

            item = self._base_item(
                title=f"BLS — {metric_name}: {_format_numeric_value(raw_value)}",
                summary=f"Latest BLS reading for {metric_name}: {_format_numeric_value(raw_value)} ({period} {year}).",
                url="https://www.bls.gov/data/",
                published_at=None,
                now=now,
                tags=["macro", "bls"],
                data={
                    "provider": "BLS",
                    "seriesId": series_id,
                    "period": period,
                    "year": year,
                    "value": _normalize_number(raw_value),
                    "rawValue": raw_value,
                    "id": item_id,
                },
            )
            items.append(item)
            state.mark_seen(item_id, self.max_seen)
        return items

    def _fetch_bea(self, client: httpx.Client, state: MacroState, now: datetime) -> list[CanonicalItem]:
        if not self.settings.bea_api_key:
            return []
        if self._last_bea_attempt_at and now - self._last_bea_attempt_at < self.bea_min_interval:
            return []
        self._last_bea_attempt_at = now
        url = (
            "https://apps.bea.gov/api/data/"
            f"?UserID={quote_plus(self.settings.bea_api_key)}"
            "&method=GETDATA&datasetname=NIPA"
            f"&TableName={quote_plus(self.bea_table_name)}"
            "&Frequency=Q&Year=X&ResultFormat=JSON"
        )
        try:
            payload = self._request_json_with_retry(client, "GET", url)
            rows = payload.get("BEAAPI", {}).get("Results", {}).get("Data", [])
            filtered = [r for r in rows if _clean_text(r.get("LineNumber")) == self.bea_line_number]
            if not filtered:
                return []
            latest = sorted(filtered, key=lambda r: _clean_text(r.get("TimePeriod")))[-1]
            period = _clean_text(latest.get("TimePeriod"))
            raw_value = _clean_text(latest.get("DataValue"))
            line_desc = _clean_text(latest.get("LineDescription"))
            table_name = _clean_text(latest.get("TableName")) or self.bea_table_name
            table_display_name = _label_with_code(BEA_TABLE_LABELS, table_name)
            item_id = _hashable_id("bea", table_name, self.bea_line_number, period, raw_value)
            if state.has_seen(item_id):
                return []

            item = self._base_item(
                title=(
                    f"BEA — {table_display_name}, line {self.bea_line_number}"
                    f" ({line_desc or 'NIPA detail'}): {_format_numeric_value(raw_value)}"
                ),
                summary=(
                    f"Latest BEA NIPA reading for {table_display_name}, "
                    f"line {self.bea_line_number} ({line_desc or 'NIPA detail'}): "
                    f"{_format_numeric_value(raw_value)} in {period}."
                ),
                url="https://apps.bea.gov/api/data/",
                published_at=None,
                now=now,
                tags=["macro", "bea"],
                data={
                    "provider": "BEA",
                    "tableName": table_name,
                    "lineNumber": self.bea_line_number,
                    "lineDescription": line_desc,
                    "period": period,
                    "value": _normalize_number(raw_value),
                    "rawValue": raw_value,
                    "id": item_id,
                },
            )
            state.mark_seen(item_id, self.max_seen)
            return [item]
        except Exception as exc:
            log.warning("macro.bea_fetch_failed err=%s", exc)
            return []

    def _fetch_dol_eta(self, client: httpx.Client, state: MacroState, now: datetime) -> list[CanonicalItem]:
        try:
            html = client.get(self.dol_claims_url).text
        except Exception as exc:
            log.warning("macro.dol_fetch_failed err=%s", exc)
            return []

        date_match = re.search(r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+[A-Za-z]+\s+\d{1,2},\s+\d{4}", html)
        time_match = re.search(r"\b\d{1,2}:\d{2}\s*(AM|PM)\s*EST\b", html, flags=re.IGNORECASE)
        release_date = _clean_text(date_match.group(0) if date_match else "")
        release_time = _clean_text(time_match.group(0) if time_match else "")
        if not release_date:
            return []

        item_id = _hashable_id("dol_eta", release_date, release_time)
        if state.has_seen(item_id):
            return []

        item = self._base_item(
            title=f"DOL/ETA weekly claims release: {release_date}",
            summary=f"Weekly claims release scheduled at {release_time or 'N/A'} on {release_date}.",
            url=self.dol_claims_url,
            published_at=None,
            now=now,
            tags=["macro", "dol", "jobless-claims"],
            data={
                "provider": "DOL/ETA",
                "releaseDate": release_date,
                "releaseTime": release_time,
                "id": item_id,
            },
        )
        state.mark_seen(item_id, self.max_seen)
        return [item]

    def _fetch_treasury(self, client: httpx.Client, state: MacroState, now: datetime) -> list[CanonicalItem]:
        try:
            html = client.get(self.treasury_yield_curve_url).text
        except Exception as exc:
            log.warning("macro.treasury_fetch_failed err=%s", exc)
            return []

        row_match = re.search(
            r"<tr[^>]*>\s*<td[^>]*>\s*(\d{2}/\d{2}/\d{4})\s*</td>\s*<td[^>]*>\s*([0-9.]+)",
            html,
            flags=re.IGNORECASE | re.S,
        )
        if not row_match:
            return []

        record_date = _clean_text(row_match.group(1))
        month_1_rate = _clean_text(row_match.group(2))
        item_id = _hashable_id("treasury", record_date, month_1_rate)
        if state.has_seen(item_id):
            return []

        item = self._base_item(
            title=f"Treasury yield curve latest date {record_date}",
            summary=f"Latest Daily Treasury Yield Curve row: 1 Mo = {month_1_rate}%.",
            url=self.treasury_yield_curve_url,
            published_at=None,
            now=now,
            tags=["macro", "treasury", "rates"],
            data={
                "provider": "U.S. Treasury",
                "recordDate": record_date,
                "month1Rate": _normalize_number(month_1_rate),
                "month1RateRaw": month_1_rate,
                "id": item_id,
            },
        )
        state.mark_seen(item_id, self.max_seen)
        return [item]

    def _fetch_fed_releases(self, client: httpx.Client, state: MacroState, now: datetime) -> list[CanonicalItem]:
        try:
            xml_text = client.get(self.fed_releases_feed_url).text
            parsed = feedparser.parse(xml_text)
            entries = parsed.entries or []
        except Exception as exc:
            log.warning("macro.fed_releases_fetch_failed err=%s", exc)
            return []

        items: list[CanonicalItem] = []
        for entry in entries[:5]:
            title = _clean_text(getattr(entry, "title", "Federal Reserve release"))
            link = _clean_text(getattr(entry, "link", "https://www.federalreserve.gov/newsevents/pressreleases.htm"))
            published = _clean_text(getattr(entry, "published", ""))
            item_id = _hashable_id("fed_release", title, link, published)
            if state.has_seen(item_id):
                continue

            item = self._base_item(
                title=f"Fed release: {title}",
                summary=published or "New Federal Reserve press release entry.",
                url=link,
                published_at=None,
                now=now,
                tags=["macro", "federal-reserve", "fed-release"],
                data={
                    "provider": "Federal Reserve",
                    "title": title,
                    "link": link,
                    "published": published,
                    "id": item_id,
                },
            )
            items.append(item)
            state.mark_seen(item_id, self.max_seen)
        return items
