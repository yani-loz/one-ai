"""
Role: Shared refresh-token rotation + revocation logic (company + platform domains).
Used by: AuthService.refresh / PlatformAuthService.refresh, and the logout paths.
Depends on: app.identity.repositories.refresh_token_repository,
            app.identity.security.tokens (sha256_hex), app.identity.exceptions.
Key invariants:
  - Rotation is single-use: a presented refresh token is validated (exists, matches
    the calling auth domain via subject_type, not revoked, not expired) and REVOKED
    before a new pair is issued. Reuse of a revoked/unknown/expired/wrong-domain token
    raises RefreshTokenInvalidError (-> 401).
  - Validation is constant-shape: every failure mode raises the SAME generic error so
    a caller cannot distinguish "unknown" from "expired" from "revoked".
  - This layer only resolves+revokes the OLD token; issuing the new pair is the
    caller's job (it knows the principal). Caller commits the session.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.identity.exceptions import RefreshTokenInvalidError
from app.identity.models.refresh_token import RefreshToken
from app.identity.repositories.refresh_token_repository import RefreshTokenRepository
from app.identity.security.tokens import sha256_hex


class TokenRotator:
    """Validates and revokes presented refresh tokens (the rotation gate)."""

    def __init__(self, refresh_tokens: RefreshTokenRepository) -> None:
        """Bind to the refresh-token repository on the relevant session."""
        self._refresh_tokens = refresh_tokens

    async def consume(self, raw_refresh_token: str, expected_subject_type: str) -> RefreshToken:
        """Validate + revoke `raw_refresh_token`, returning the consumed row.

        Contract: looks the token up by its sha256 hash; requires it to exist, belong
        to `expected_subject_type` (the calling auth domain — 'user' or
        'platform_admin'), and be unexpired; then revokes it ATOMICALLY so it cannot be
        reused. Revocation is a conditional `UPDATE ... WHERE revoked_at IS NULL`: if it
        touches zero rows the token was already revoked (reuse) or a concurrent rotation
        won the race, and consumption is rejected. This makes single-use hold under
        concurrency (AUD-01). The returned row carries subject_id the caller needs to
        mint the next pair. A domain mismatch is rejected WITHOUT revoking — presenting a
        foreign token must not let a caller revoke (deny-of-service) someone else's
        session.

        Raises:
            RefreshTokenInvalidError: token unknown, wrong auth domain, expired, already
                revoked, or lost a rotation race (single generic error — no detail).
        """
        token_hash = sha256_hex(raw_refresh_token)
        stored = await self._refresh_tokens.get_by_hash(token_hash)
        if (
            stored is None
            or stored.subject_type != expected_subject_type
            or self._is_expired(stored)
        ):
            raise RefreshTokenInvalidError("Refresh token is invalid.")
        if await self._refresh_tokens.revoke_by_hash(token_hash) == 0:
            raise RefreshTokenInvalidError("Refresh token is invalid.")
        return stored

    async def revoke(self, raw_refresh_token: str) -> UUID | None:
        """Idempotently revoke `raw_refresh_token` (logout); return its subject_id.

        Unknown token -> no-op, returns None. The subject_id lets logout ALSO revoke that
        subject's outstanding ACCESS tokens via the denylist (the refresh-token revoke alone
        only stops NEW access tokens; the current one would otherwise live out its TTL).
        """
        token_hash = sha256_hex(raw_refresh_token)
        stored = await self._refresh_tokens.get_by_hash(token_hash)
        await self._refresh_tokens.revoke_by_hash(token_hash)
        return stored.subject_id if stored is not None else None

    @staticmethod
    def _is_expired(token: RefreshToken) -> bool:
        """Return True if the token's expiry is in the past (UTC-aware compare)."""
        return token.expires_at <= datetime.now(UTC)
