from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any

import httpx

from scraper_bot.core.config import get_settings
from scraper_bot.schema.canonical import CanonicalItem
from scraper_bot.sources.base import SourceScraper

log = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _to_filing_timestamp(value: str) -> datetime | None:
    cleaned = _clean(value)
    if len(cleaned) != 8 or not cleaned.isdigit():
        return None
    try:
        return datetime.strptime(cleaned, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _strip_document_markup(raw_text: str, max_chars: int) -> str:
    sample = raw_text[: max(1, max_chars)]
    sample = re.sub(r"<script[\\s\\S]*?</script>", " ", sample, flags=re.IGNORECASE)
    sample = re.sub(r"<style[\\s\\S]*?</style>", " ", sample, flags=re.IGNORECASE)
    sample = re.sub(r"<[^>]+>", " ", sample)
    sample = unescape(sample)
    sample = re.sub(r"\\s+", " ", sample)
    return sample.strip()


def _rank_sentences_for_summary(form_type: str, sentences: list[str]) -> list[str]:
    if not sentences:
        return []

    keywords = {
        "8-K": ("item ", "agreement", "acquisition", "results", "guidance", "officer"),
        "10-Q": ("revenue", "income", "cash", "guidance", "risk", "segment"),
        "10-K": ("revenue", "income", "strategy", "risk", "segment", "liquidity"),
        "6-K": ("results", "update", "guidance", "risk", "operations"),
        "S-1": ("offering", "proceeds", "risk", "business", "use of proceeds"),
    }
    boosted_terms = keywords.get(form_type.upper(), ())

    scored: list[tuple[int, int, str]] = []
    for idx, sentence in enumerate(sentences):
        lowered = sentence.lower()
        score = 0
        if len(sentence) >= 40:
            score += 1
        if len(sentence) <= 400:
            score += 1
        for term in boosted_terms:
            if term in lowered:
                score += 2
        if re.search(r"\\$\\s?\\d|\\d+%", sentence):
            score += 1
        scored.append((score, idx, sentence))

    scored.sort(key=lambda item: (-item[0], item[1]))
    top = sorted(scored[:8], key=lambda item: item[1])
    return [s for _, _, s in top]


def _build_content_summary(form_type: str, raw_text: str, max_chars: int, max_sentences: int) -> str:
    plain = _strip_document_markup(raw_text, max_chars=max_chars)
    if not plain:
        return ""

    candidates = [s.strip() for s in re.split(r"(?<=[.!?])\\s+", plain) if s.strip()]
    ranked = _rank_sentences_for_summary(form_type, candidates)
    if not ranked:
        ranked = candidates

    selected: list[str] = []
    for sentence in ranked:
        if len(sentence) < 24:
            continue
        selected.append(sentence)
        if len(selected) >= max(1, max_sentences):
            break

    if not selected:
        return plain[:500]

    summary = " ".join(selected)
    return summary[:1200]


@dataclass
class SecState:
    seen_ids: list[str]

    @classmethod
    def load(cls, path: Path) -> "SecState":
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


class SecSource(SourceScraper):
    @property
    def name(self) -> str:
        return "sec"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.timeout = httpx.Timeout(int(self.settings.sec_http_timeout_seconds))
        self.state_path = Path(self.settings.sec_state_path)
        self.max_seen = int(self.settings.sec_max_seen_items)
        self.ciks = [_clean(cik) for cik in self.settings.sec_ciks.split(",") if _clean(cik)]
        forms = {_clean(form).upper() for form in self.settings.sec_forms.split(",") if _clean(form)}
        self.allowed_forms = forms
        self.max_filings_per_cik = max(1, int(self.settings.sec_max_filings_per_cik))
        self.user_agent = _clean(self.settings.sec_user_agent)
        self.fetch_primary_doc_content = bool(self.settings.sec_fetch_primary_doc_content)
        self.primary_doc_max_chars = max(10_000, int(self.settings.sec_primary_doc_max_chars))
        self.content_summary_max_sentences = max(1, int(self.settings.sec_content_summary_max_sentences))

    def fetch(self) -> list[CanonicalItem]:
        if not self.ciks:
            return []

        if not self.user_agent:
            log.warning("sec.user_agent_missing set SEC_USER_AGENT with email/contact")
            return []

        state = SecState.load(self.state_path)
        now = _utc_now()
        out: list[CanonicalItem] = []

        headers = {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov",
        }

        with httpx.Client(timeout=self.timeout, headers=headers) as client:
            for raw_cik in self.ciks:
                cik_num = _safe_int(raw_cik)
                if cik_num is None:
                    log.warning("sec.invalid_cik cik=%s", raw_cik)
                    continue
                cik_padded = f"{cik_num:010d}"
                try:
                    out.extend(self._fetch_company_filings(client, state, now, cik_padded))
                except Exception as exc:
                    log.warning("sec.fetch_failed cik=%s err=%s", cik_padded, exc)

        if out:
            state.save(self.state_path)
        return out

    def _fetch_company_filings(
        self,
        client: httpx.Client,
        state: SecState,
        now: datetime,
        cik_padded: str,
    ) -> list[CanonicalItem]:
        url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
        payload = client.get(url).json()
        filings = payload.get("filings", {}).get("recent", {})

        accessions: list[str] = filings.get("accessionNumber", [])
        forms: list[str] = filings.get("form", [])
        filing_dates: list[str] = filings.get("filingDate", [])
        acceptance_dates: list[str] = filings.get("acceptanceDateTime", [])
        primary_docs: list[str] = filings.get("primaryDocument", [])
        primary_descs: list[str] = filings.get("primaryDocDescription", [])

        limit = min(self.max_filings_per_cik, len(accessions), len(forms))
        items: list[CanonicalItem] = []

        for idx in range(limit):
            form_type = _clean(forms[idx]).upper()
            if self.allowed_forms and form_type not in self.allowed_forms:
                continue

            accession = _clean(accessions[idx])
            if not accession:
                continue
            filing_date = _clean(filing_dates[idx] if idx < len(filing_dates) else "")
            acceptance_dt = _clean(acceptance_dates[idx] if idx < len(acceptance_dates) else "")
            primary_doc = _clean(primary_docs[idx] if idx < len(primary_docs) else "")
            primary_desc = _clean(primary_descs[idx] if idx < len(primary_descs) else "")

            item_id = f"sec|{cik_padded}|{accession}|{form_type}"
            if state.has_seen(item_id):
                continue

            accession_no_dashes = accession.replace("-", "")
            company_name = _clean(payload.get("name")) or f"CIK {cik_padded}"
            filing_ts = _to_filing_timestamp(filing_date)
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik_padded)}/{accession_no_dashes}/{primary_doc}"
                if primary_doc
                else f"https://www.sec.gov/edgar/browse/?CIK={int(cik_padded)}"
            )
            summary = f"{company_name} filed {form_type} on {filing_date or 'unknown date'}"
            if primary_desc:
                summary = f"{summary}: {primary_desc}"

            content_summary = ""
            content_extracted = False
            if self.fetch_primary_doc_content and primary_doc:
                try:
                    doc_response = client.get(filing_url)
                    raw_doc = _clean(getattr(doc_response, "text", ""))
                    if raw_doc:
                        content_summary = _build_content_summary(
                            form_type=form_type,
                            raw_text=raw_doc,
                            max_chars=self.primary_doc_max_chars,
                            max_sentences=self.content_summary_max_sentences,
                        )
                        content_extracted = bool(content_summary)
                        if content_summary:
                            summary = f"{summary} | Summary: {content_summary}"
                except Exception as exc:
                    log.warning("sec.primary_doc_fetch_failed cik=%s accession=%s err=%s", cik_padded, accession, exc)

            item = CanonicalItem(
                item_type="sec-filing",
                title=f"SEC {form_type} filing: {company_name}",
                summary=summary,
                url=filing_url,
                published_at=filing_ts,
                scraped_at=now,
                source="sec",
                tags=["sec", "edgar", "filing", form_type.lower()],
                severity="normal",
                classification={
                    "priority": "normal",
                    "assetClass": "macro",
                    "subcategory": "regulatory-filing",
                    "tickers": [],
                    "topics": ["sec", "edgar", "filings"],
                    "regions": ["US"],
                },
                data={
                    "provider": "SEC EDGAR",
                    "id": item_id,
                    "cik": cik_padded,
                    "companyName": company_name,
                    "formType": form_type,
                    "accessionNumber": accession,
                    "filingDate": filing_date,
                    "acceptanceDateTime": acceptance_dt,
                    "primaryDocument": primary_doc,
                    "primaryDescription": primary_desc,
                    "url": filing_url,
                    "contentExtracted": content_extracted,
                    "contentSummary": content_summary,
                },
            )
            items.append(item)
            state.mark_seen(item_id, self.max_seen)

        return items
