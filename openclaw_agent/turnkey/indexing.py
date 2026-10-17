from __future__ import annotations

import json
import logging
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Dict

from .config import TurnkeyConfig


logger = logging.getLogger(__name__)


class SubgraphIndexer:
    """Best-effort event emitter for execution observability."""

    def __init__(self, endpoint: str):
        self.endpoint = endpoint.rstrip("/")

    def emit_execution_event(self, payload: Dict[str, object]) -> bool:
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return bool(body.get("ok", True))


class NoopIndexer:
    def emit_execution_event(self, payload: Dict[str, object]) -> bool:  # pragma: no cover - trivial
        _ = payload
        return True


class LocalIndexer:
    """Local JSONL indexer fallback for bankofai-style deterministic audit traces."""

    def __init__(self, events_file: str):
        self.events_file = Path(events_file)
        self.events_file.parent.mkdir(parents=True, exist_ok=True)

    def emit_execution_event(self, payload: Dict[str, object]) -> bool:
        event = {
            "event_id": f"evt_local_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
            "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event_source": "openclaw-local-indexer",
            "event_payload": payload,
        }
        with self.events_file.open("a", encoding="utf-8") as f:
            f.write(f"{json.dumps(event)}\n")
        return True


def resolve_indexer_endpoint(cfg: TurnkeyConfig) -> str:
    explicit_endpoint = cfg.subgraph_indexer_url.strip().rstrip("/")
    if explicit_endpoint:
        return explicit_endpoint

    if cfg.use_mcp_server_tron and cfg.mcp_server_tron_url.strip():
        return f"{cfg.mcp_server_tron_url.strip().rstrip('/')}/ops/indexer/events"

    return ""


def build_indexer(cfg: TurnkeyConfig) -> SubgraphIndexer | LocalIndexer | NoopIndexer:
    endpoint = resolve_indexer_endpoint(cfg)
    if not endpoint:
        local_path = cfg.subgraph_indexer_local_path.strip()
        if local_path:
            logger.info("subgraph_indexer_fallback_local:%s", local_path)
            return LocalIndexer(local_path)
        return NoopIndexer()
    return SubgraphIndexer(endpoint)


def emit_execution_event_safe(indexer: SubgraphIndexer | LocalIndexer | NoopIndexer, payload: Dict[str, object]) -> None:
    try:
        ok = indexer.emit_execution_event(payload)
        if not ok:
            logger.warning("subgraph_indexer_returned_not_ok", extra={"payload": payload})
    except Exception as exc:  # pragma: no cover - network variability
        logger.warning("subgraph_indexer_emit_failed:%s", exc)
