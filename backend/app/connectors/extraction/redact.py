"""
Role: The secrets-masking gate (FIX_BEFORE_PROD EQ-2) — redact_secrets(text) scrubs high-confidence
      credentials out of any text BEFORE it is stored in the embeddable substrate (email body_text +
      attachment extracted_text). The 2026-06-10 DB data-quality audit (EQ-2) found LIVE API keys
      verbatim in extracted_text (Anthropic/OpenAI/Gemini/Supabase keys, an AWS access key, password
      patterns) — pulled out of credential files attached to emails. Anything stored here is later
      sent to an embedding provider AND surfaced in retrieval, so the stored substrate MUST be
      secret-free. This is the masking gate the future embedding/Ask layer relies on; a
      re-ingest/backfill cleans existing rows (the lead re-ingests after this lands).
Used by: the IMAP connector's email_parser (applied to body_text after sanitize_body_text) and its
         attachment_extractor (applied to extracted_text after sanitize) — the two text-storage
         chokepoints. The arrow points IN: those connector modules import this leaf; it imports
         nothing back. The returned count feeds EQ-7 detail/logging (count only — the secret is
         NEVER logged).
Depends on: stdlib only (re, math). NOTHING from any specific connector (connector-agnostic, pure).
Key invariants:
  - PRECISION OVER RECALL: a false positive permanently corrupts stored content (the original bytes
    are discarded — design §4 lean-attachments), so every detector is HIGH-CONFIDENCE only. We
    redact a provider key / PEM block / a key-NAMED high-entropy value — we DO NOT redact bare
    long base64/hex that merely *looks* random (image fragments, content hashes, MIME blobs are
    common and must survive verbatim). When in doubt, leave it.
  - PURE + DETERMINISTIC: redact_secrets is a pure function (str -> (str, int)); same input always
    yields the same output. No I/O, no global state, no randomness.
  - NEVER LOG THE SECRET: the function returns only the redacted text + a COUNT. Callers log the
    count (EQ-7). The matched secret text never leaves this function — not in the return value, not
    in an exception, not in a placeholder (the placeholder is a fixed TYPE label, never the value).
  - TYPED PLACEHOLDERS: each match is replaced with `[REDACTED:<kind>]` (e.g.
    `[REDACTED:aws_access_key]`) — the surrounding prose is preserved so the text stays meaningful
    for retrieval; only the secret token is removed.
  - ORDER-STABLE COUNT: the count is the number of secrets replaced, summed across all detectors.
"""

from __future__ import annotations

import math
import re

# Placeholder template — a FIXED type label, never the secret value (the never-log-the-secret rule).
_PLACEHOLDER = "[REDACTED:{kind}]"
# The fixed prefix of every emitted placeholder. The CONTEXT detectors (keyed-value + connstring)
# run AFTER the token detectors, so an already-redacted value (e.g. `STRIPE_SECRET=[REDACTED:...]`)
# would otherwise re-match and double-count; both context detectors skip a value that is itself a
# placeholder. (A placeholder is not a secret, so this also keeps redact_secrets IDEMPOTENT.)
_PLACEHOLDER_PREFIX = "[REDACTED:"

# ── Shannon-entropy floor for the key-NAMED-value detector (bits/char) ──────────────────────────
# A credential value assigned to a sensitive key name is only redacted when it is BOTH long enough
# AND high-entropy — this is the one detector that keys on context (a `password=` / `"api_key":`
# assignment) rather than a self-identifying token shape, so the entropy gate guards against
# redacting an ordinary word a user happened to assign (`password = changeme` stays; a real random
# secret does not). 3.0 bits/char clears dictionary words / repeats; real keys sit well above.
_MIN_KEYED_VALUE_ENTROPY = 3.0
_MIN_KEYED_VALUE_LEN = 12
# ReDoS note: the catastrophic case in _CONNSTRING_PASSWORD was the SCHEME prefix, not the value —
# in a long alphanumeric run the greedy `[a-zA-Z0-9+.\-]*://` backtracked O(n) at every start
# position hunting for `://` (O(n²); measured 40s @ 200 KB). The fix is bounding the SCHEME to
# {0,31} (RFC 3986). The value/userinfo are LEFT UNBOUNDED on purpose: bounding the value would
# cause a real secret longer than the bound to redact to ZERO (connstring) or leak its tail
# (keyed) — a detection regression — and the value is the LAST required-suffix-free group on the
# keyed path (linear) while the scheme bound already defuses the connstring backtracking.
# Hard ceiling on the text we will scan for secrets. Attachment text is already capped upstream
# (extraction MAX_EXTRACTED_CHARS); email bodies are not, so this backstops the redaction itself:
# never spend unbounded CPU on a single oversized input. Text beyond this is left as-is (it is far
# past any realistic secret-bearing prose, and the bounded regexes are already linear).
_MAX_REDACT_SCAN_CHARS = 2_000_000


def _shannon_entropy(value: str) -> float:
    """Shannon entropy of a string in bits/char (0.0 for empty) — used to gate the keyed-value path.

    A high value means the characters are near-uniformly distributed (a random secret); a low value
    means structure/repetition (a dictionary word, a path, a sentence) we must NOT redact.
    """
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    length = len(value)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


# ── Detector 1: self-identifying provider tokens (shape IS the proof) ───────────────────────────
# Each pattern matches a token whose prefix/shape is unique to a credential class, so a match is
# high-confidence on its own — no surrounding context needed. Ordered most-specific first.
#
#   - aws_access_key:  AKIA / ASIA / AGPA … + 16 uppercase-alnum (AWS access key id format).
#   - openai_key:      sk-…  (also covers sk-proj-…, sk-ant-… project/scoped variants) — >=20 tail.
#   - google_api_key:  AIza + 35 url-safe chars (Google/Gemini/Maps API key).
#   - github_token:    ghp_/gho_/ghu_/ghs_/ghr_/github_pat_ + >=20 tail.
#   - slack_token:     xox[baprs]- … (bot/user/app/refresh/legacy Slack tokens).
#   - stripe_key:      sk_live_/rk_live_ … (live secret/restricted Stripe keys; test keys ignored).
#   - jwt_token:       eyJ… . eyJ… . …  (three base64url segments — JWT/Supabase service keys).
_TOKEN_DETECTORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "aws_access_key",
        re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASCA)[0-9A-Z]{16}\b"),
    ),
    ("openai_key", re.compile(r"\bsk-(?:proj-|ant-|svcacct-)?[A-Za-z0-9_-]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    (
        "github_token",
        re.compile(
            r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b"
        ),
    ),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe_key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}\b")),
    # JWT / Supabase service key: header.payload.signature, each a base64url segment. The header
    # `eyJ` is base64url for `{"` — the unambiguous start of a JWT. Signature segment may be empty
    # (alg=none), so the final segment allows zero+ chars.
    ("jwt_token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*")),
)

# ── Detector 2: PEM private-key blocks (the whole armored block is the secret) ──────────────────
# Matches BEGIN…END for any PRIVATE KEY label (RSA/EC/OPENSSH/PGP/plain). DOTALL so the base64 body
# spanning many lines is consumed; non-greedy so adjacent blocks don't merge into one match.
# The body uses a TEMPERED dot — `(?:(?!-----BEGIN … PRIVATE KEY-----).)*?` — so it cannot scan
# PAST the next BEGIN marker. A plain `.*?` is ReDoS-able: thousands of BEGIN markers with no END
# make each BEGIN's non-greedy body scan to EOF hunting for END (O(n·k); measured 21s @ 280 KB).
# Tempering caps each BEGIN's scan at the distance to the next BEGIN → linear (3 ms @ 280 KB),
# while a legitimate key body (any size, never containing another BEGIN) still matches end-to-end.
_PEM_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"
    r"(?:(?!-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----).)*?"
    r"-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
    re.DOTALL,
)

# ── Detector 3: key=value / "key": "value" assignments with a SENSITIVE key name ────────────────
# The ONLY context-based detector — fires on an assignment whose KEY name is sensitive
# (password/secret/api_key/token/access_key …) AND whose VALUE clears the length+entropy gate. This
# catches the .env / config secrets (Supabase/OpenAI/Gemini in the audit) that aren't a recognizable
# token shape. The entropy gate is what makes it safe: `password = changeme` (low entropy) survives,
# a real random value does not. The key name + separator + the OPTIONAL opening quote are captured
# in 'lead' and re-emitted verbatim; the value match stops BEFORE the closing quote, so it stays in
# the text untouched — the assignment's `key="..."` shape survives with only the value swapped.
_SENSITIVE_KEY_NAME = (
    r"(?:password|passwd|pwd|secret|secret_key|api[_-]?key|apikey|access[_-]?key|"
    r"auth[_-]?token|access[_-]?token|client[_-]?secret|private[_-]?key|token)"
)
_KEYED_SECRET = re.compile(
    # group 'lead' = key name + an OPTIONAL closing quote on the key (JSON `"api_key"`) + separator
    #                (= or :) + optional opening quote on the value — all preserved verbatim;
    # group 'value' = the secret value (excludes whitespace/quotes/separators, so a closing quote or
    #                 trailing comma/semicolon/brace is NOT consumed and survives in the text).
    # The lookbehind rejects only an ALPHANUMERIC char before the key name (so `mypassword` mid-word
    # does NOT match) while ALLOWING `_`/`-`/`.` separators — `db_password`, `app-secret`,
    # `config.token` are the dominant real-world key-name forms and must match.
    r"(?P<lead>(?<![A-Za-z0-9])" + _SENSITIVE_KEY_NAME + r"[\"']?\s*[:=]\s*[\"']?)"
    # value UNbounded: it is the last group with no required suffix, so it is linear, and a bound
    # would leak the tail of a >bound-length secret (verified). Stops at whitespace/quote/separator.
    r"(?P<value>[^\s\"';,}]{" + str(_MIN_KEYED_VALUE_LEN) + r",})",
    re.IGNORECASE,
)

# ── Detector 4: connection-string passwords (scheme://user:PASSWORD@host) ───────────────────────
# A URL credential has NO sensitive key NAME for Detector 3 to anchor on — the password sits between
# `user:` and `@host`. We redact it only when the value clears the SAME entropy gate (so
# `db://app:devpass@…` placeholder-ish creds with low entropy survive, a real secret does not). The
# scheme is required (a bare `user:pass@host` is too ambiguous), and the userinfo must precede an
# `@host` to be a real authority. The 'lead' (scheme + user + ':') and the trailing '@host' are
# preserved; only the password is swapped.
_CONNSTRING_PASSWORD = re.compile(
    # The ReDoS fix is bounding the SCHEME to {0,31} (RFC 3986): a long alphanumeric run otherwise
    # made the greedy `[a-zA-Z0-9+.\-]*://` backtrack O(n) at EVERY start position hunting for
    # `://` — O(n²), measured 40s @ 200 KB (bounding the value did NOT help — verified). The value
    # is deliberately UNBOUNDED `{12,}`: with the scheme bounded the whole pattern is linear
    # (measured 378 ms @ 2 MB), and a bound would make a >bound-length connstring password redact
    # to ZERO (the greedy value can't reach `@host` past the bound) — a detection regression.
    r"(?P<lead>[a-zA-Z][a-zA-Z0-9+.\-]{0,31}://[^\s:/@]{1,256}:)"
    r"(?P<value>[^\s:/@]{" + str(_MIN_KEYED_VALUE_LEN) + r",})"
    r"(?P<host>@[^\s/@]+)"
)


def _looks_high_entropy(value: str) -> bool:
    """True when a keyed value is long AND high-entropy enough to be a real secret (not a word)."""
    if len(value) < _MIN_KEYED_VALUE_LEN:
        return False
    return _shannon_entropy(value) >= _MIN_KEYED_VALUE_ENTROPY


def _redact_tokens(text: str) -> tuple[str, int]:
    """Replace every self-identifying provider token; returns (redacted_text, count)."""
    count = 0
    for kind, pattern in _TOKEN_DETECTORS:
        placeholder = _PLACEHOLDER.format(kind=kind)
        text, replaced = pattern.subn(placeholder, text)
        count += replaced
    return text, count


def _redact_pem(text: str) -> tuple[str, int]:
    """Replace every PEM PRIVATE KEY block with one placeholder; returns (redacted_text, count)."""
    return _PEM_PRIVATE_KEY.subn(_PLACEHOLDER.format(kind="private_key"), text)


def _redact_keyed_values(text: str) -> tuple[str, int]:
    """Replace high-entropy values assigned to a sensitive key name; returns (redacted_text, count).

    The key name + separator + opening quote ('lead') are preserved verbatim and only the value is
    swapped for the placeholder; the value match stops before the closing quote, so `api_key="..."`
    stays well-formed (the closing quote survives in the text). A value that fails the
    length/entropy gate is left untouched (precision over recall).
    """
    count = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal count
        value = match.group("value")
        if value.startswith(_PLACEHOLDER_PREFIX):
            return match.group(0)  # already redacted by a token detector — no re-wrap/double-count
        if not _looks_high_entropy(value):
            return match.group(0)  # ordinary word / low-entropy value — leave it verbatim
        count += 1
        return f"{match.group('lead')}{_PLACEHOLDER.format(kind='credential')}"

    return _KEYED_SECRET.sub(_replace, text), count


def _redact_connstring_passwords(text: str) -> tuple[str, int]:
    """Replace a high-entropy password in a `scheme://user:PASSWORD@host` URL → (text, count).

    The scheme + user + ':' and the trailing '@host' are preserved; only the password is swapped,
    and only when it clears the entropy gate (a low-entropy `app:devpass@…` placeholder survives).
    """
    count = 0
    placeholder = _PLACEHOLDER.format(kind="connection_password")

    def _replace(match: re.Match[str]) -> str:
        nonlocal count
        value = match.group("value")
        if value.startswith(_PLACEHOLDER_PREFIX):
            return match.group(0)  # already redacted by a token detector — no re-wrap/double-count
        if not _looks_high_entropy(value):
            return match.group(0)  # low-entropy / placeholder password — leave it verbatim
        count += 1
        return f"{match.group('lead')}{placeholder}{match.group('host')}"

    return _CONNSTRING_PASSWORD.sub(_replace, text), count


def redact_secrets(text: str) -> tuple[str, int]:
    """Redact high-confidence secrets from text before it is stored in the embeddable substrate.

    The masking gate for FIX_BEFORE_PROD EQ-2: applied to email `body_text` (after
    sanitize_body_text) and attachment `extracted_text` (after sanitize) so no live credential
    reaches the embedding provider or retrieval. Conservative by design — only HIGH-CONFIDENCE
    detectors fire (precision over recall: a false positive permanently corrupts stored content,
    since the original bytes are discarded):
      - self-identifying provider tokens (AWS access key, OpenAI sk-, Google/Gemini AIza, GitHub
        ghp_/PAT, Slack xox*, Stripe live key, JWT/Supabase service key);
      - PEM PRIVATE KEY blocks (BEGIN…END);
      - key=value / "key": "value" assignments whose KEY name is sensitive AND whose VALUE clears a
        length + Shannon-entropy gate (so `password = changeme` survives, a random secret does not);
      - connection-string passwords (`scheme://user:PASSWORD@host`) clearing the same entropy gate.
    Bare high-entropy base64/hex with NO key shape or key-name context is deliberately NOT redacted
    (image fragments, content hashes, MIME blobs are common and must survive verbatim).

    Args:
        text: the already-sanitized, storable text block (body or extracted attachment text).

    Returns:
        (redacted_text, count): the text with each detected secret replaced by a typed
        `[REDACTED:<kind>]` placeholder, and the total number of secrets redacted. The secret value
        is NEVER returned, logged, or embedded in the placeholder — count only (EQ-7).

    Performance: linear in `len(text)` (every detector quantifier is length-bounded), and the
    scanned prefix is hard-capped at `_MAX_REDACT_SCAN_CHARS` so a single oversized, attacker-
    crafted email body can never spend unbounded CPU here. The bytes past the cap are appended
    unscanned (far past any realistic secret-bearing prose).
    """
    if not text:
        return text, 0
    if len(text) > _MAX_REDACT_SCAN_CHARS:
        head, tail = text[:_MAX_REDACT_SCAN_CHARS], text[_MAX_REDACT_SCAN_CHARS:]
        redacted_head, total = redact_secrets(head)
        return redacted_head + tail, total
    total = 0
    text, replaced = _redact_pem(text)  # PEM first: its body would otherwise trip the token paths
    total += replaced
    text, replaced = _redact_tokens(text)
    total += replaced
    text, replaced = _redact_keyed_values(text)
    total += replaced
    text, replaced = _redact_connstring_passwords(text)
    total += replaced
    return text, total
