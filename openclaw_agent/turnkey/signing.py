from __future__ import annotations

import os
import hashlib
from dataclasses import dataclass


SIGNER_ROLE_EXECUTION = "execution"
SIGNER_ROLE_EMERGENCY = "emergency"
SIGNER_ROLE_ANALYTICS = "analytics"

_ROLE_ENV_PRIORITY: dict[str, tuple[str, ...]] = {
    SIGNER_ROLE_EXECUTION: (
        "OPENCLAW_EXECUTION_PRIVATE_KEY",
        "OPENCLAW_OPERATOR_PRIVATE_KEY",
    ),
    SIGNER_ROLE_EMERGENCY: (
        "OPENCLAW_EMERGENCY_PRIVATE_KEY",
    ),
    SIGNER_ROLE_ANALYTICS: (
        "OPENCLAW_ANALYTICS_PRIVATE_KEY",
    ),
}


@dataclass(frozen=True)
class SigningContext:
    private_key_hex: str
    signer_label: str
    signer_role: str
    signer_source: str
    signer_key_id: str


def _normalize_private_key(private_key_hex: str) -> str:
    return private_key_hex.strip().lower().replace("0x", "")


def _build_key_id(private_key_hex: str) -> str:
    normalized = _normalize_private_key(private_key_hex)
    if not normalized:
        return ""
    return f"pkh:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:16]}"


def _from_key(private_key_hex: str, *, signer_label: str, signer_role: str, signer_source: str) -> SigningContext:
    return SigningContext(
        private_key_hex=private_key_hex,
        signer_label=signer_label,
        signer_role=signer_role,
        signer_source=signer_source,
        signer_key_id=_build_key_id(private_key_hex),
    )


def resolve_signing_context(
    *,
    signer_role: str = SIGNER_ROLE_EXECUTION,
    explicit_private_key: str = "",
) -> SigningContext:
    role = (signer_role or SIGNER_ROLE_EXECUTION).strip().lower()
    if role not in _ROLE_ENV_PRIORITY:
        role = SIGNER_ROLE_EXECUTION

    provided = (explicit_private_key or "").strip()
    if provided:
        return _from_key(
            provided,
            signer_label=f"cli:manual-{role}",
            signer_role=role,
            signer_source="cli",
        )

    for env_var in _ROLE_ENV_PRIORITY[role]:
        key = os.environ.get(env_var, "").strip()
        if key:
            source_suffix = "operator-fallback" if env_var == "OPENCLAW_OPERATOR_PRIVATE_KEY" else role
            return _from_key(
                key,
                signer_label=f"env:{source_suffix}",
                signer_role=role,
                signer_source=f"env:{env_var}",
            )

    return _from_key(
        "",
        signer_label="none",
        signer_role=role,
        signer_source="none",
    )


def resolve_execution_signing_context(explicit_private_key: str) -> SigningContext:
    """Resolve execution signer with a secure precedence order.

    Priority:
    1) Explicit CLI-supplied key (least preferred operationally but most explicit for manual operations)
    2) OPENCLAW_EXECUTION_PRIVATE_KEY
    3) OPENCLAW_OPERATOR_PRIVATE_KEY (backward compatible fallback)
    """

    return resolve_signing_context(
        signer_role=SIGNER_ROLE_EXECUTION,
        explicit_private_key=explicit_private_key,
    )
