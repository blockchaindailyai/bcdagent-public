from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol


_TRC8004_SUBJECT_RE = re.compile(r"^did:trc8004:[A-Za-z0-9._:-]{3,128}$")


@dataclass(frozen=True)
class Trc8004IdentityRecord:
    actor: str
    subject: str
    role: str
    issued_at: int
    expires_at: int
    revoked: bool

    @property
    def is_active(self) -> bool:
        if not self.subject or self.revoked:
            return False
        if self.expires_at and self.expires_at <= self.issued_at:
            return False
        return True


class Trc8004Provider(Protocol):
    def get_identity(self, actor: str) -> Dict[str, Any]:
        ...


class Trc8004IdentityClient:
    """SDK facade for querying TRC-8004 identity records from BCDAgent flows."""

    def __init__(self, provider: Trc8004Provider):
        self._provider = provider

    @staticmethod
    def is_valid_subject(subject: str) -> bool:
        return bool(_TRC8004_SUBJECT_RE.match(subject.strip()))

    def resolve(self, actor: str) -> Optional[Trc8004IdentityRecord]:
        payload = self._provider.get_identity(actor)
        if not payload:
            return None

        record = Trc8004IdentityRecord(
            actor=actor,
            subject=str(payload.get("subject", "")),
            role=str(payload.get("role", "")),
            issued_at=int(payload.get("issuedAt", 0) or 0),
            expires_at=int(payload.get("expiresAt", 0) or 0),
            revoked=bool(payload.get("revoked", False)),
        )
        if not record.subject:
            return None
        return record


class StaticTrc8004Provider:
    """Simple provider for local testing and dry-runs."""

    def __init__(self, records: Dict[str, Dict[str, Any]]):
        self._records = records

    def get_identity(self, actor: str) -> Dict[str, Any]:
        return self._records.get(actor, {})
