"""
Role: Shared token-pair issuance used by both auth services (company + platform).
Used by: AuthService and PlatformAuthService.
Depends on: app.identity.security.tokens, app.identity.models.refresh_token,
            app.identity.repositories.refresh_token_repository, app.identity.principal.
Key invariants:
  - Issuing a pair persists ONLY the sha256 hash of the refresh token (the raw token
    is returned to the caller, never stored). Caller commits the surrounding session.
  - Audience and subject_type are passed by the caller so this stays domain-agnostic:
    company users get aud='company'/subject_type='user', platform admins get
    aud='platform'/subject_type='platform_admin'.
"""

from __future__ import annotations

from app.identity.models.refresh_token import RefreshToken
from app.identity.principal import Principal
from app.identity.repositories.refresh_token_repository import RefreshTokenRepository
from app.identity.security.tokens import (
    access_token_ttl,
    encode_access_token,
    new_refresh_token,
    refresh_token_expiry,
    refresh_token_ttl,
)


class TokenIssuer:
    """Issues access+refresh pairs and records the refresh hash for rotation."""

    def __init__(self, refresh_tokens: RefreshTokenRepository) -> None:
        """Bind to the refresh-token repository on the relevant session."""
        self._refresh_tokens = refresh_tokens

    async def issue_pair(
        self, principal: Principal, audience: str, subject_type: str
    ) -> tuple[str, str]:
        """Issue a new (access_token, refresh_token) pair for `principal`.

        Contract: encodes a fresh access JWT for `audience`, generates an opaque
        refresh token, and persists only its hash with the configured expiry. Returns
        the two raw token strings. The caller is responsible for committing.
        """
        access_token = encode_access_token(principal, access_token_ttl(), audience)
        raw_refresh, refresh_hash = new_refresh_token()
        await self._refresh_tokens.add(
            RefreshToken(
                subject_id=principal.subject_id,
                subject_type=subject_type,
                token_hash=refresh_hash,
                expires_at=refresh_token_expiry(refresh_token_ttl()),
            )
        )
        return access_token, raw_refresh
