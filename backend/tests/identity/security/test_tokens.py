"""
Unit tests for app.identity.security.tokens — JWT encode/decode, audience binding,
expiry/tamper rejection, and opaque refresh-token hashing (no DB).
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import jwt
import pytest

from app.core.config import get_settings
from app.identity.exceptions import TokenExpiredError, TokenInvalidError
from app.identity.principal import Principal
from app.identity.security.tokens import (
    COMPANY_AUDIENCE,
    PLATFORM_AUDIENCE,
    decode_access_token,
    encode_access_token,
    new_refresh_token,
    sha256_hex,
)


def _company_principal() -> Principal:
    return Principal(
        subject_id=uuid4(), org_id=uuid4(), role="member", subject_type="user"
    )


def _platform_principal() -> Principal:
    return Principal(
        subject_id=uuid4(), org_id=None, role="platform_admin", subject_type="platform_admin"
    )


def test_encode_decode_round_trip_preserves_claims() -> None:
    principal = _company_principal()

    token = encode_access_token(principal, timedelta(minutes=15), COMPANY_AUDIENCE)
    claims = decode_access_token(token, COMPANY_AUDIENCE)

    assert claims["sub"] == str(principal.subject_id)
    assert claims["org_id"] == str(principal.org_id)
    assert claims["role"] == "member"
    assert claims["aud"] == COMPANY_AUDIENCE
    assert claims["type"] == "access"


def test_encode_platform_principal_has_null_org_id() -> None:
    principal = _platform_principal()

    token = encode_access_token(principal, timedelta(minutes=15), PLATFORM_AUDIENCE)
    claims = decode_access_token(token, PLATFORM_AUDIENCE)

    assert claims["org_id"] is None
    assert claims["aud"] == PLATFORM_AUDIENCE


def test_decode_wrong_audience_raises_token_invalid() -> None:
    # A company token decoded against the platform audience must be rejected — this is
    # the seam that keeps the two auth domains apart.
    token = encode_access_token(_company_principal(), timedelta(minutes=15), COMPANY_AUDIENCE)

    with pytest.raises(TokenInvalidError):
        decode_access_token(token, PLATFORM_AUDIENCE)


def test_decode_expired_token_raises_token_expired() -> None:
    expired_token = encode_access_token(
        _company_principal(), timedelta(minutes=-5), COMPANY_AUDIENCE
    )

    with pytest.raises(TokenExpiredError):
        decode_access_token(expired_token, COMPANY_AUDIENCE)


def test_decode_tampered_signature_raises_token_invalid() -> None:
    token = encode_access_token(_company_principal(), timedelta(minutes=15), COMPANY_AUDIENCE)
    # Corrupt the FIRST char of the signature segment — a fully meaningful 6-bit
    # position. (Flipping the LAST base64url char is flaky: its trailing padding bits
    # can decode byte-identically, leaving the signature still valid ~1/16 of the time.)
    header, payload, signature = token.split(".")
    tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    tampered = f"{header}.{payload}.{tampered_signature}"

    with pytest.raises(TokenInvalidError):
        decode_access_token(tampered, COMPANY_AUDIENCE)


def test_decode_garbage_string_raises_token_invalid() -> None:
    with pytest.raises(TokenInvalidError):
        decode_access_token("not.a.jwt", COMPANY_AUDIENCE)


def test_new_refresh_token_returns_raw_and_matching_hash() -> None:
    raw_token, token_hash = new_refresh_token()

    assert raw_token != token_hash
    assert token_hash == sha256_hex(raw_token)
    assert len(token_hash) == 64


def test_new_refresh_token_is_unique_per_call() -> None:
    first_raw, first_hash = new_refresh_token()
    second_raw, second_hash = new_refresh_token()

    assert first_raw != second_raw
    assert first_hash != second_hash


def test_sha256_hex_is_deterministic() -> None:
    assert sha256_hex("same-input") == sha256_hex("same-input")


def test_decode_token_missing_exp_claim_raises_token_invalid() -> None:
    # A signed token without an exp claim must be rejected, not treated as
    # non-expiring — decode requires exp/aud/sub to be present.
    settings = get_settings()
    token = jwt.encode(
        {"sub": str(uuid4()), "aud": COMPANY_AUDIENCE, "role": "member", "type": "access"},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(TokenInvalidError):
        decode_access_token(token, COMPANY_AUDIENCE)
