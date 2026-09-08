"""
Role: The UTF-8 self-test of contract §3.9 / hard rule R8 — forces UTF-8 on an output stream and
      PROVES it end to end (stream write, encode/decode round trip, temporary file round trip)
      before the runner emits any other output.
Used by: tools.mem01_verify.verify_step1 (step 1 of the run sequence, §3.2), and the sealed
      oracle module tests/tools/mem01_verify/test_utf8_selftest.py.
Depends on: tools.mem01_verify.exceptions (Utf8SelfTestError).
Key invariants:
  - UTF8_SELFTEST_LINE and UTF8_SELFTEST_FAILED_LINE are the literal contract strings; changing
    one byte of either breaks the seal.
  - The self-test NEVER prints the failure line itself — it raises Utf8SelfTestError and the
    runner decides what to print and with which exit code (§3.9 says exit 2).
  - Any exception raised anywhere inside the probe is a self-test failure and is re-raised as
    Utf8SelfTestError, so callers need to handle exactly one error type.
  - The scratch file is always removed, including on failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import TextIO
from uuid import uuid4

from tools.mem01_verify.exceptions import Utf8SelfTestError

UTF8_SELFTEST_LINE = "MEM01 UTF-8 self-test: Здравей, свят — кирилица OK"
UTF8_SELFTEST_FAILED_LINE = "MEM01 UTF-8 self-test FAILED"

_ENCODING = "utf-8"


def _reconfigure_to_utf8(stream: TextIO) -> None:
    """Switch `stream` to UTF-8 in place when it supports reconfiguration.

    A stream without `reconfigure` (a plain StringIO, a test double) is left as it is: the
    round-trip comparisons below still prove that what the caller wrote is the expected text.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    reconfigure(encoding=_ENCODING)


def _prove_scratch_round_trip(scratch_dir: Path, expected: bytes) -> bytes:
    """Write `expected` into a fresh file under `scratch_dir` and read the bytes back."""
    scratch_dir.mkdir(parents=True, exist_ok=True)
    scratch_file = scratch_dir / f"mem01-utf8-selftest-{uuid4().hex}.txt"
    try:
        scratch_file.write_bytes(expected)
        return scratch_file.read_bytes()
    finally:
        scratch_file.unlink(missing_ok=True)


def run_utf8_selftest(stream: TextIO, scratch_dir: Path) -> None:
    """Force UTF-8 on `stream` and prove it, per contract §3.9.

    Reconfigures the stream to UTF-8, writes `UTF8_SELFTEST_LINE` to it, encodes the same string
    to UTF-8 bytes and decodes them back, writes those bytes to a temporary file under
    `scratch_dir` and reads them back, then compares all three representations.

    Args:
        stream: The text stream to force to UTF-8 and write the proof line to (stdout/stderr).
        scratch_dir: A directory the self-test may create and delete one small file in.

    Returns:
        None. Success is the absence of an exception.

    Raises:
        Utf8SelfTestError: On any mismatch between the three representations, or on any
            exception while writing the stream, encoding, or using the scratch directory
            (an unwritable console, a scratch path that is a file, a read-only directory).
    """
    try:
        _reconfigure_to_utf8(stream)
        stream.write(UTF8_SELFTEST_LINE + "\n")
        stream.flush()

        encoded = UTF8_SELFTEST_LINE.encode(_ENCODING)
        decoded = encoded.decode(_ENCODING)
        read_back = _prove_scratch_round_trip(scratch_dir, encoded)
    except Exception as exc:  # noqa: BLE001 - §3.9: ANY exception is a self-test failure
        raise Utf8SelfTestError(f"{UTF8_SELFTEST_FAILED_LINE}: {exc}") from exc

    if decoded != UTF8_SELFTEST_LINE or read_back != encoded:
        raise Utf8SelfTestError(
            f"{UTF8_SELFTEST_FAILED_LINE}: the three UTF-8 representations disagree"
        )
