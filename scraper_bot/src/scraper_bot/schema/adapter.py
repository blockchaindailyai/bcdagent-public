from datetime import timezone
from scraper_bot.schema.canonical import CanonicalItem
from scraper_bot.schema.intel_v1 import IntelEventV1, IntelClassificationV1


VALID_PRIORITY = {"breaking", "normal"}
VALID_ASSET_CLASS = {"general", "crypto", "stocks", "bonds", "rates", "commodities", "fx", "credit"}


def _normalize_priority(value):
    if value in VALID_PRIORITY:
        return value
    if value in {"high", "urgent"}:
        return "breaking"
    if value in {"low", "info"}:
        return "normal"
    return None


def _normalize_asset_class(value, *, fallback=None):
    if value in VALID_ASSET_CLASS:
        return value
    if value in {"onchain_transfer", "onchain", "defi", "nft"}:
        return "crypto"
    return fallback if fallback in VALID_ASSET_CLASS else None


def _to_epoch_ms(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _clean_ticker(t: str):
    t = t.strip().lstrip("$").upper()
    return t if t else None


class SchemaAdapter:
    """
    Global event normalization rules:

    - ts (top-level) = scraped_at (arrival time)
    - data.publishedTs = original article publish time (if present)
    - data.publishedDeltaSec = scraped - published
    """

    def to_intel_event(self, item: CanonicalItem) -> IntelEventV1:
        # Always use scrape time for primary timestamp
        scraped_ms = _to_epoch_ms(item.scraped_at)

        cls = item.classification or {}

        tickers = []
        for t in cls.get("tickers", []):
            ct = _clean_ticker(str(t))
            if ct:
                tickers.append(ct)

        classification = (
            IntelClassificationV1(
                priority=_normalize_priority(cls.get("priority")),
                assetClass=_normalize_asset_class(cls.get("assetClass")),
                subcategory=_normalize_asset_class(
                    cls.get("subcategory"),
                    fallback=_normalize_asset_class(cls.get("assetClass")),
                ),
                tickers=tickers,
                topics=list(cls.get("topics", []) or []),
                regions=list(cls.get("regions", []) or []),
            )
            if cls
            else None
        )

        # Preserve original publish time inside data
        data_payload = dict(item.data or {})

        if item.published_at:
            published_ms = _to_epoch_ms(item.published_at)
            delta_sec = int((item.scraped_at - item.published_at).total_seconds())

            data_payload["publishedTs"] = published_ms
            data_payload["publishedAt"] = item.published_at.isoformat()
            data_payload["publishedDeltaSec"] = delta_sec
        else:
            data_payload["publishedTs"] = None
            data_payload["publishedAt"] = None
            data_payload["publishedDeltaSec"] = None

        return IntelEventV1(
            type=item.item_type,
            title=item.title,
            summary=item.summary,
            url=item.url,
            ts=scraped_ms,  # arrival time
            source=item.source,
            tags=item.tags,
            severity=item.severity,
            classification=classification,
            data=data_payload,
        )

    def to_intel_events(self, items):
        return [self.to_intel_event(i) for i in items]
