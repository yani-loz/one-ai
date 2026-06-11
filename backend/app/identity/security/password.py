"""
Role: Password hashing + verification (bcrypt) for the identity domain.
Used by: AuthService / PlatformAuthService (login verify), UserService and
         PlatformAuthService (hash on user/org creation), ErasureService (sudo re-auth).
Depends on: bcrypt (external). Leaf module otherwise.
Key invariants:
  - Hashing is bcrypt only (security.md mandate). A stored hash is opaque; the raw
    password is never persisted, logged, or returned.
  - verify_password is constant-time via bcrypt.checkpw and never raises on a bad
    hash — it returns False so callers emit a generic auth failure.
  - DUMMY_PASSWORD_HASH lets a caller pay the bcrypt cost even when no account
    matched, keeping login constant-time (no user enumeration via response time).
  - bcrypt 5.x RAISES ValueError on inputs >72 bytes (it does NOT truncate). Code that
    HASHES must bound length first — the request schemas enforce <=72 bytes via
    BcryptPassword; verify_password swallows that ValueError as False, so login is safe.
  - ASYNC CALLERS MUST use verify_password_async / hash_password_async: bcrypt at
    rounds=12 costs ~300ms of pure CPU, and the sync forms block the event loop for
    every tenant on the instance. The sync forms exist for scripts/tests only.
"""

from __future__ import annotations

import asyncio

import bcrypt

# bcrypt operates on bytes; we use UTF-8 throughout and a standard work factor.
_BCRYPT_ROUNDS = 12


def hash_password(raw_password: str) -> str:
    """Return a bcrypt hash of `raw_password`.

    Contract: input is a plaintext password; output is a self-describing bcrypt
    hash string (algorithm + cost + salt + digest) safe to store in password_hash.
    The plaintext is never retained.

    Precondition: `raw_password` must be <=72 UTF-8 bytes — bcrypt 5.x raises above
    that. The request schemas enforce this via BcryptPassword, so callers never pass an
    over-long value; passing one here is a programming error, not unvalidated input.
    """
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    digest = bcrypt.hashpw(raw_password.encode("utf-8"), salt)
    return digest.decode("utf-8")


def verify_password(raw_password: str, password_hash: str) -> bool:
    """Return True iff `raw_password` matches the stored bcrypt `password_hash`.

    Contract: constant-time comparison via bcrypt.checkpw.

    Edge cases: a malformed/empty stored hash, or a raw password over bcrypt's 72-byte
    limit, raises ValueError inside bcrypt; we catch it and return False so the caller
    surfaces a generic auth failure rather than a 500 or a leak of which side was wrong.
    """
    try:
        return bcrypt.checkpw(raw_password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


async def hash_password_async(raw_password: str) -> str:
    """Hash `raw_password` on a worker thread so the event loop stays free.

    Contract: identical to hash_password (same precondition: <=72 UTF-8 bytes);
    the ~300ms bcrypt cost runs via asyncio.to_thread instead of blocking the loop.
    """
    return await asyncio.to_thread(hash_password, raw_password)


async def verify_password_async(raw_password: str, password_hash: str) -> bool:
    """Verify `raw_password` on a worker thread so the event loop stays free.

    Contract: identical to verify_password (False on mismatch or malformed input);
    the ~300ms bcrypt cost runs via asyncio.to_thread instead of blocking the loop.
    """
    return await asyncio.to_thread(verify_password, raw_password, password_hash)


# A fixed bcrypt hash (standard work factor), computed once at import. Callers verify a
# submitted password against THIS when no account matched, so an unknown/inactive email
# costs the same bcrypt work as a real login and cannot be told apart by response time.
DUMMY_PASSWORD_HASH = hash_password("timing-equalizer-not-a-real-credential")
