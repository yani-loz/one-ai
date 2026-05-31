"""
Role: JWT access-token encode/decode + opaque refresh-token generation/hashing.
Used by: AuthService / PlatformAuthService (issue pairs), identity.dependencies
         (decode + verify access tokens), RefreshTokenRepository (lookup by hash).
Depends on: app.core.config (jwt_secret/algorithm/TTLs), app.identity.principal,
            app.identity.exceptions. PyJWT + stdlib secrets/hashlib (external).
Key invariants:
  - Access tokens are HS256-signed JWTs. decode_access_token verifies signature,
    audience, AND expiry; it raises TokenExpiredError / TokenInvalidError on failure
    (both map to 401). Expiry is checked BEFORE generic invalidity because PyJWT's
    ExpiredSignatureError subclasses InvalidTokenError.
  - audience binds the two auth domains apart: aud='company' for users, aud='platform'
    for platform admins. A wrong-audience token fails decode (no manual check).
  - Refresh tokens are OPAQUE random strings; only their sha256 hex is ever stored.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt

from app.core.config import get_settings
from app.identity.exceptions import TokenExpiredError, TokenInvalidError
from app.identity.principal import Principal

# JWT audience values — the two separate auth domains.
COMPANY_AUDIENCE = "company"
PLATFORM_AUDIENCE = "platform"

# Number of random bytes for the opaque refresh token (token_urlsafe arg).
_REFRESH_TOKEN_BYTES = 48


def encode_access_token(principal: Principal, ttl: timedelta, audience: str) -> str:
    """Encode a signed HS256 access JWT for `principal`.

    Contract:
        Claims: sub (subject_id), type='access', aud (=audience), role, org_id
        (only for org-scoped users — omitted/None for platform admins), iat, exp, jti.
        TTL is supplied by the caller (settings.access_token_ttl_minutes).

    Returns the compact-serialized token string.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": str(principal.subject_id),
        "type": "access",
        "aud": audience,
        "role": principal.role,
        "org_id": str(principal.org_id) if principal.org_id is not None else None,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": str(uuid4()),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, audience: str) -> dict[str, object]:
    """Verify + decode an access token, returning its claims.

    Contract: verifies signature, the expected `audience`, and expiry, and REQUIRES
        the exp/aud/sub claims to be present — a token missing exp is rejected, not
        silently treated as non-expiring.

    Raises:
        TokenExpiredError: signature valid but the token has expired (-> 401).
        TokenInvalidError: bad signature, wrong audience, malformed, missing a
            required claim, or otherwise invalid (-> 401).
    """
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=audience,
            options={"require": ["exp", "aud", "sub"]},
        )
    # ExpiredSignatureError subclasses InvalidTokenError — order matters.
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError("Access token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenInvalidError("Access token is invalid.") from exc


def new_refresh_token() -> tuple[str, str]:
    """Generate a fresh opaque refresh token.

    Returns (raw_token, token_hash): the raw URL-safe token to hand to the client
    ONCE, and its sha256 hex digest to persist. The raw token is never stored.
    """
    raw_token = secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)
    return raw_token, sha256_hex(raw_token)


def sha256_hex(raw_token: str) -> str:
    """Return the lowercase sha256 hex digest of `raw_token` (64 chars)."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def refresh_token_expiry(ttl: timedelta) -> datetime:
    """Return the absolute UTC expiry for a refresh token issued now."""
    return datetime.now(UTC) + ttl


def access_token_ttl() -> timedelta:
    """Return the configured access-token lifetime."""
    return timedelta(minutes=get_settings().access_token_ttl_minutes)


def refresh_token_ttl() -> timedelta:
    """Return the configured refresh-token lifetime."""
    return timedelta(days=get_settings().refresh_token_ttl_days)
