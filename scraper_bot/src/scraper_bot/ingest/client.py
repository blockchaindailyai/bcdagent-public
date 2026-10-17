import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from scraper_bot.schema.intel_v1 import IntelBatch

class IngestClient:
    def __init__(
        self,
        base,
        token,
        timeout,
        max_rate_limit_retries: int = 0,
        rate_limit_backoff_seconds: float = 1.0,
    ):
        self.url = base.rstrip("/") + "/api/ingest"
        self.token = token
        self.timeout = httpx.Timeout(timeout)
        self.max_rate_limit_retries = max(0, int(max_rate_limit_retries))
        self.rate_limit_backoff_seconds = max(0.0, float(rate_limit_backoff_seconds))

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

    @retry(stop=stop_after_attempt(5),
           wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
           retry=retry_if_exception_type((httpx.TimeoutException, httpx.TransportError)))
    def post_events(self, events, idempotency_key=None):
        headers = self._headers()
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        payload = IntelBatch(items=events).model_dump(mode="json")
        with httpx.Client(timeout=self.timeout) as client:
            return client.post(self.url, headers=headers, json=payload)
