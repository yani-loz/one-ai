"""
Role: Unit tests for the EQ-2 secrets-masking gate (app.connectors.extraction.redact) — each
      high-confidence detector class redacts a realistic secret while leaving surrounding prose
      intact; NEGATIVE cases prove ordinary high-entropy-looking text (base64 image fragments,
      content hashes, low-entropy placeholder creds) survives verbatim (precision over recall — a
      false positive permanently corrupts stored content); the returned count surfaces; and the
      secret value never leaks into the placeholder.
Used by: pytest (tests/connectors/extraction). Pure — no DB, no network, no I/O.
Depends on: app.connectors.extraction.redact (redact_secrets). All secrets below are FAKE — random
            local strings shaped like the real thing, never live credentials.
"""

from __future__ import annotations

import pytest

from app.connectors.extraction.redact import redact_secrets

# Provider-token fixtures are built from PARTS so no contiguous secret literal sits in the source
# (GitHub push-protection blocks scannable provider tokens). The concatenated runtime values are the
# full real-shaped tokens the detectors match. All FAKE — never live credentials.
_OPENAI_KEY = "sk-ant-" + "api03-AbCdEf0123456789AbCdEf0123456789"
_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
_GITHUB_TOKEN = "ghp_" + "AbCdEf0123456789AbCdEf0123456789abcd"
_SLACK_TOKEN = "xoxb-" + "123456789012-abcdefABCDEF1234"

# ── Positive: self-identifying provider tokens (the shape IS the proof) ─────────────────────────


def test_redact_secrets_openai_key_in_prose_redacts_only_the_key() -> None:
    text = f"Here is the key {_OPENAI_KEY} use it carefully."

    redacted, count = redact_secrets(text)

    assert count == 1
    assert "sk-ant-api03" not in redacted
    assert redacted == "Here is the key [REDACTED:openai_key] use it carefully."


def test_redact_secrets_aws_access_key_redacts_with_typed_placeholder() -> None:
    text = f"aws_access_key_id = {_AWS_KEY} in the config"

    redacted, count = redact_secrets(text)

    assert count == 1
    assert "AKIA" not in redacted
    assert "[REDACTED:aws_access_key]" in redacted
    assert "in the config" in redacted  # surrounding prose preserved


def test_redact_secrets_google_api_key_redacts() -> None:
    # A correctly-shaped Google/Gemini key: AIza + exactly 35 url-safe chars (39 total).
    key = "AIza" + "SyD0123456789_AbCdEfGhIjKlMnOpQrStU"
    assert len(key) == 39  # guard the fixture itself: AIza (4) + 35
    text = f"GEMINI key {key} end"

    redacted, count = redact_secrets(text)

    assert count == 1
    assert "AIza" not in redacted
    assert "[REDACTED:google_api_key]" in redacted


def test_redact_secrets_github_token_redacts() -> None:
    text = f"token {_GITHUB_TOKEN} here"

    redacted, count = redact_secrets(text)

    assert count == 1
    assert "ghp_" not in redacted
    assert "[REDACTED:github_token]" in redacted


def test_redact_secrets_slack_token_redacts() -> None:
    text = f"Slack bot token {_SLACK_TOKEN} configured"

    redacted, count = redact_secrets(text)

    assert count == 1
    assert "xoxb-" not in redacted
    assert "[REDACTED:slack_token]" in redacted


def test_redact_secrets_stripe_live_key_redacts() -> None:
    # Built from parts so NO scannable secret literal sits in the source (GitHub push-protection
    # flags a contiguous sk_live_… literal); the runtime value is a full sk_live_ shape the
    # detector still matches.
    key = "sk_live_" + "AbCdEf0123456789AbCdEf01"
    text = f"STRIPE_SECRET={key} production"

    redacted, count = redact_secrets(text)

    assert count == 1
    assert "sk_live_" not in redacted
    assert "[REDACTED:stripe_key]" in redacted


def test_redact_secrets_jwt_service_key_redacts() -> None:
    # A Supabase service key is a JWT: header.payload.signature, each a base64url segment.
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ"
        ".AbCdEf0123456789_-XyZ"
    )
    text = f"SUPABASE_SERVICE_KEY {jwt} for the backend"

    redacted, count = redact_secrets(text)

    assert count == 1
    assert "eyJ" not in redacted
    assert "[REDACTED:jwt_token]" in redacted


# ── Positive: PEM private-key blocks (the whole armored block is the secret) ────────────────────


def test_redact_secrets_pem_private_key_block_redacts_whole_block() -> None:
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF0qBxJ1example\n"
        "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789base64body\n"
        "-----END RSA PRIVATE KEY-----"
    )
    text = f"My private key is:\n{pem}\nKeep it safe."

    redacted, count = redact_secrets(text)

    assert count == 1
    assert "BEGIN RSA PRIVATE KEY" not in redacted
    assert "MIIEpAIBAAKCAQEA" not in redacted  # the base64 body is gone
    assert "[REDACTED:private_key]" in redacted
    assert "Keep it safe." in redacted  # trailing prose preserved


def test_redact_secrets_two_pem_blocks_counted_and_redacted_separately() -> None:
    block = "-----BEGIN PRIVATE KEY-----\nAbCdEfGhIjKlMnOp0123456789\n-----END PRIVATE KEY-----"
    text = f"{block}\nmiddle prose\n{block}"

    redacted, count = redact_secrets(text)

    assert count == 2  # non-greedy: the two blocks do not merge into one match
    assert "BEGIN PRIVATE KEY" not in redacted
    assert "middle prose" in redacted


# ── Positive: key=value / "key": "value" assignments with a sensitive key name ──────────────────


def test_redact_secrets_keyed_password_high_entropy_redacts_value_only() -> None:
    text = 'db_password="Xk9zmQ2vLp7zRt4wByN3" host=db.internal'

    redacted, count = redact_secrets(text)

    assert count == 1
    assert "Xk9zmQ2vLp7zRt4wByN3" not in redacted
    assert redacted == 'db_password="[REDACTED:credential]" host=db.internal'  # quotes well-formed


def test_redact_secrets_json_api_key_assignment_redacts() -> None:
    text = '{"api_key": "AbCdEf0123456789ZyXwVu", "timeout": 30}'

    redacted, count = redact_secrets(text)

    assert count == 1
    assert "AbCdEf0123456789ZyXwVu" not in redacted
    assert '"api_key": "[REDACTED:credential]"' in redacted
    assert '"timeout": 30' in redacted  # the non-secret field is untouched


def test_redact_secrets_keyed_secret_unquoted_equals_redacts() -> None:
    text = "CLIENT_SECRET=aB3xY7zQ9mL2pK5vR8wT1nD running"

    redacted, count = redact_secrets(text)

    assert count == 1
    assert "aB3xY7zQ9mL2pK5vR8wT1nD" not in redacted
    assert "CLIENT_SECRET=[REDACTED:credential]" in redacted


# ── Positive: connection-string passwords (scheme://user:PASSWORD@host) ─────────────────────────


def test_redact_secrets_connstring_password_redacts_only_password() -> None:
    text = "DATABASE_URL postgres://app_user:Xk9zmQ2vLp7zRt4wByN3@db.internal:5432/prod here"

    redacted, count = redact_secrets(text)

    assert count == 1
    assert "Xk9zmQ2vLp7zRt4wByN3" not in redacted
    assert "postgres://app_user:[REDACTED:connection_password]@db.internal:5432/prod" in redacted


# ── Negative: ordinary content that must survive verbatim (precision over recall) ───────────────


def test_redact_secrets_base64_image_fragment_not_redacted() -> None:
    # A long base64 run with NO key shape / key-name context (a common inline image fragment) must
    # survive — redacting bare base64 would permanently corrupt legitimate content.
    text = "inline image iVBORw0KGgoAAAANSUhEUgAAAAUA1234567890abcdefABCDEFghij data"

    redacted, count = redact_secrets(text)

    assert count == 0
    assert redacted == text  # byte-for-byte verbatim


def test_redact_secrets_content_hash_not_redacted() -> None:
    text = "content_hash 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08 stored"

    redacted, count = redact_secrets(text)

    assert count == 0
    assert redacted == text


def test_redact_secrets_low_entropy_placeholder_password_not_redacted() -> None:
    # A sensitive key NAME but a low-entropy dictionary value: this is the false-positive trap the
    # entropy gate exists to avoid. `changeme` must survive.
    text = "password = changeme please update it"

    redacted, count = redact_secrets(text)

    assert count == 0
    assert redacted == text


def test_redact_secrets_prose_with_sensitive_words_not_redacted() -> None:
    text = "The secret to success is hard work; the token of our appreciation is sincere."

    redacted, count = redact_secrets(text)

    assert count == 0
    assert redacted == text


def test_redact_secrets_email_address_in_prose_not_redacted() -> None:
    # A `user:word@host` shape in prose must NOT trip the connection-string detector (the word is
    # low-entropy and there is no scheme://).
    text = "ping me at john:hello@example.com about the report"

    redacted, count = redact_secrets(text)

    assert count == 0
    assert redacted == text


# ── Contract: count surfaces, secret never leaks, empty/None-safe, idempotent ───────────────────


def test_redact_secrets_multiple_distinct_secrets_count_sums() -> None:
    text = f'key {_OPENAI_KEY} and aws {_AWS_KEY} and pwd="Xk9zmQ2vLp7zRt4wByN3"'

    redacted, count = redact_secrets(text)

    assert count == 3
    assert "sk-ant" not in redacted and "AKIA" not in redacted
    assert "Xk9zmQ2vLp7zRt4wByN3" not in redacted


def test_redact_secrets_placeholder_never_contains_the_secret() -> None:
    secret = _OPENAI_KEY
    text = f"key {secret}"

    redacted, _count = redact_secrets(text)

    # The never-log-the-secret invariant: no fragment of the secret survives in the output.
    assert secret not in redacted
    assert "AbCdEf0123456789" not in redacted


def test_redact_secrets_empty_string_returns_empty_zero() -> None:
    redacted, count = redact_secrets("")

    assert redacted == ""
    assert count == 0


def test_redact_secrets_secret_free_text_returns_verbatim_zero() -> None:
    text = "Quarterly report attached. Revenue grew 12% over the prior period."

    redacted, count = redact_secrets(text)

    assert count == 0
    assert redacted == text


def test_redact_secrets_is_idempotent_on_already_redacted_text() -> None:
    once, first_count = redact_secrets(f"key {_OPENAI_KEY}")
    twice, second_count = redact_secrets(once)

    assert first_count == 1
    assert second_count == 0  # placeholders are not themselves secrets
    assert twice == once


@pytest.mark.parametrize(
    "scanned",
    [
        "Just a normal sentence with no credentials at all.",
        "Meeting notes: discuss Q3 roadmap and hiring plan.",
        "filename invoice_2026_03.pdf size 482kb",
    ],
)
def test_redact_secrets_benign_text_never_redacted(scanned: str) -> None:
    redacted, count = redact_secrets(scanned)

    assert count == 0
    assert redacted == scanned
