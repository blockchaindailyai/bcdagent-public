from __future__ import annotations

from dataclasses import dataclass

from .config import TurnkeyConfig


@dataclass(frozen=True)
class TronTransportContext:
    api_base_url: str
    signer_base_url: str
    transport: str


def resolve_tron_transport_context(cfg: TurnkeyConfig) -> TronTransportContext:
    """Resolve which transport endpoint should handle TRON API calls."""

    use_mcp = bool(getattr(cfg, "use_mcp_server_tron", False))
    mcp_url = str(getattr(cfg, "mcp_server_tron_url", "") or "").strip()

    if use_mcp and mcp_url:
        base = mcp_url.rstrip("/")
        return TronTransportContext(
            api_base_url=base,
            signer_base_url=base,
            transport="mcp-server-tron",
        )

    return TronTransportContext(
        api_base_url=str(getattr(cfg, "rpc_url", "")).rstrip("/"),
        signer_base_url=str(getattr(cfg, "signer_rpc_url", getattr(cfg, "rpc_url", ""))).rstrip("/"),
        transport="tron-rpc",
    )
