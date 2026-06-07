#!/usr/bin/env python3
"""
Dev validation harness (NOT shipped, NOT a CI test) — runs the real email parser over the on-disk
spike corpus to measure the parse-failed rate and spot-check decomposition quality on REAL emails,
which hand-built fixtures can't surface (lying charsets, nested multiparts, calendar invites,
winmail.dat, RFC2231 filename mangling). Mirrors the design's "measure first" discipline.

Run it against the gitignored dump by mounting it into a throwaway container:
  docker compose run --rm -v "${PWD}/spikes:/spikes:ro" -e PYTHONPATH=/app backend \
      uv run python /spikes/validate_parse_corpus.py /spikes/imap_dump [--limit N]

The mailbox address is taken from the <user> path segment under the dump root
(imap_dump/<user>/<folder>/<uidvalidity>/<uid>.eml).
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from app.connectors.imap.parsing import extract_text, parse_email


def _mailbox_for(path: Path, root: Path) -> str:
    """The <user> path segment directly under the dump root is the synced mailbox address."""
    try:
        return path.relative_to(root).parts[0]
    except (ValueError, IndexError):
        return "unknown@local"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the email parser over a .eml corpus.")
    parser.add_argument("root", help="dump root (imap_dump)")
    parser.add_argument("--limit", type=int, default=0, help="cap files parsed (0 = all)")
    args = parser.parse_args(argv)

    root = Path(args.root)
    files = sorted(root.rglob("*.eml"))
    if args.limit > 0:
        files = files[:: max(1, len(files) // args.limit)][: args.limit]
    print(f"parsing {len(files)} .eml files under {root}")

    totals = Counter()
    word_counts: list[int] = []
    failures: list[tuple[Path, str]] = []
    attachment_types: Counter = Counter()

    for path in files:
        mailbox = _mailbox_for(path, root)
        try:
            parsed = parse_email(path.read_bytes(), mailbox)
        except Exception as exc:  # the never-raise contract: any raise here is a real defect
            failures.append((path, f"{type(exc).__name__}: {exc}"))
            totals["raised"] += 1
            continue

        totals["ok"] += 1
        totals["empty_body"] += int(not parsed.body_text)
        totals["has_attachments"] += int(parsed.has_attachments)
        totals["automated"] += int(parsed.is_automated)
        totals["reply"] += int(parsed.is_reply)
        totals["outbound"] += int(parsed.direction == "outbound")
        totals["no_message_id"] += int(parsed.message_id is None)
        word_counts.append(parsed.word_count)
        for attachment in parsed.attachments:
            attachment_types[attachment.content_type] += 1
            if extract_text(attachment) is not None:
                totals["attachments_text_extracted"] += 1
            totals["attachments_total"] += 1

    _report(totals, word_counts, attachment_types, failures)
    return 1 if totals["raised"] else 0


def _report(
    totals: Counter,
    word_counts: list[int],
    attachment_types: Counter,
    failures: list[tuple[Path, str]],
) -> None:
    ok = totals["ok"]
    print("=" * 60)
    print(f"  parsed ok        : {ok}")
    print(f"  RAISED (defect)  : {totals['raised']}")
    if ok:
        avg_words = sum(word_counts) / len(word_counts) if word_counts else 0
        print(f"  empty body       : {totals['empty_body']} ({totals['empty_body'] / ok:.1%})")
        print(f"  has attachments  : {totals['has_attachments']} ({totals['has_attachments'] / ok:.1%})")
        print(f"  automated sender : {totals['automated']} ({totals['automated'] / ok:.1%})")
        print(f"  replies          : {totals['reply']} ({totals['reply'] / ok:.1%})")
        print(f"  outbound         : {totals['outbound']} ({totals['outbound'] / ok:.1%})")
        print(f"  no Message-ID    : {totals['no_message_id']} ({totals['no_message_id'] / ok:.1%})")
        print(f"  avg word_count   : {avg_words:.0f}")
    print(f"  attachments      : {totals['attachments_total']} "
          f"(text extracted: {totals['attachments_text_extracted']})")
    print("  top attachment content-types:")
    for content_type, count in attachment_types.most_common(12):
        print(f"      {count:6d}  {content_type}")
    if failures:
        print("  FAILURE SAMPLES (up to 15):")
        for path, error in failures[:15]:
            print(f"      {error}  @ {path.name}")
    print("=" * 60)


if __name__ == "__main__":
    raise SystemExit(main())
