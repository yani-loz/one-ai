"""Unit tests for app.identity.security.password — bcrypt hashing (no DB)."""

from __future__ import annotations

from app.identity.security.password import hash_password, verify_password


def test_hash_password_does_not_return_plaintext() -> None:
    raw_password = "Adm1n-Dev-Only-2026!"

    password_hash = hash_password(raw_password)

    assert password_hash != raw_password
    assert raw_password not in password_hash


def test_hash_password_uses_bcrypt_prefix() -> None:
    password_hash = hash_password("some-password")

    assert password_hash.startswith("$2b$")


def test_hash_password_is_salted_so_two_hashes_differ() -> None:
    first_hash = hash_password("identical-password")
    second_hash = hash_password("identical-password")

    assert first_hash != second_hash


def test_verify_password_correct_password_returns_true() -> None:
    raw_password = "Memb3r-Dev-Only-2026!"
    password_hash = hash_password(raw_password)

    assert verify_password(raw_password, password_hash) is True


def test_verify_password_wrong_password_returns_false() -> None:
    password_hash = hash_password("correct-password")

    assert verify_password("wrong-password", password_hash) is False


def test_verify_password_malformed_hash_returns_false_not_raises() -> None:
    # A corrupt/empty stored hash must surface as a generic auth failure, not a 500.
    assert verify_password("any-password", "not-a-bcrypt-hash") is False
