"""
Role: IMAP folder INGEST POLICY — decides which mailbox folders are excluded from ingest
      (Trash / Junk / Spam / Drafts). Shared by the production fetch path and the dev disk-ingest
      driver so both apply the SAME blocklist.
Used by: app.connectors.imap.fetch_session (_parse_list_item / _blocking_list_folders),
         scripts.ingest_imap_dump (disk folder grouping).
Depends on: re (stdlib). Leaf module — imports nothing from the connector.
Key invariants:
  - EXCLUDE deleted/spam/draft folders only. Trash/Junk/Spam are NOISE (the user already rejected
    them); Drafts are UNSENT (not correspondence). Ingesting them pollutes the knowledge base — the
    data-quality audit measured 28% of the corpus as blocklist-only mail. SENT, ARCHIVE, INBOX and
    everything else are KEPT (sent + archived mail is real, high-value knowledge).
  - TWO signals, OR'd: (1) the RFC 6154 SPECIAL-USE attribute the server advertises on LIST
    (`\Trash` / `\Junk` / `\Drafts`) — locale-independent, so a non-English "Papierkorb"/"Кошче"
    trash folder is still caught; (2) a NAME heuristic that EXACT-matches (case-insensitive) any
    path segment against a small word set — so `INBOX.Trash`, `INBOX.Trash.Ciela` (a sub-folder of
    Trash), `INBOX.spam`, `INBOX.Drafts` are excluded, while `Spam Reports` or `Draft Contracts`
    (whole-segment ≠ the word) are KEPT (no substring false-positives).
  - Exclusion is POLICY, not mail loss (cf. CA-CONN-07's loud skip of an UNSELECTABLE folder) — it
    is deliberate and may be silent at the seam; callers log a count for transparency.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# RFC 6154 SPECIAL-USE attributes that mark a folder as deleted/spam/draft (lower-cased for match).
_BLOCKLIST_SPECIAL_USE = frozenset({"\\trash", "\\junk", "\\drafts"})

# Folder-name segments (EXACT, case-insensitive) that mark a deleted/spam/draft folder. Kept small,
# exact, and UNAMBIGUOUS: substring matches are avoided ("Spam Reports", "Draft Contracts" stay),
# and the ambiguous short words `bin` and singular `draft` are deliberately EXCLUDED from the list —
# a real user folder named "bin" (a project/binary folder) or "Draft" would otherwise be silently
# dropped (mail loss > letting a rare noise folder through). A real Trash/Drafts folder is still
# caught by its RFC 6154 SPECIAL-USE flag and by the unambiguous `trash` / `drafts` names.
_BLOCKLIST_SEGMENT_NAMES = frozenset(
    {"trash", "deleted", "deleted items", "deleted messages", "junk", "spam", "drafts"}
)

# Folder hierarchy delimiters across servers (Courier/dovecot `.`, others `/`). Splitting on both is
# safe: a `/`-delimited server has no `.`-segments to mis-split, and vice-versa.
_SEGMENT_SPLIT = re.compile(r"[./]")


def is_blocklist_special_use(flags: Iterable[str]) -> bool:
    """True if any LIST flag is an RFC 6154 deleted/spam/draft SPECIAL-USE attribute."""
    return any(flag.strip().lower() in _BLOCKLIST_SPECIAL_USE for flag in flags)


def is_excluded_folder(name: str, special_use_flags: Iterable[str] = ()) -> bool:
    """Return True if `name` is a Trash/Junk/Spam/Drafts folder that must NOT be ingested.

    Excludes by SPECIAL-USE flag (locale-independent) OR by an exact case-insensitive match of any
    path segment against the blocklist word set. Sent / Archive / INBOX / all other folders → False.

    Args:
        name: the folder's full hierarchical name (e.g. "INBOX.Trash.Ciela", "INBOX/Junk").
        special_use_flags: the RFC 6154 SPECIAL-USE attributes the server advertised for it, if any.
    """
    if is_blocklist_special_use(special_use_flags):
        return True
    segments = _SEGMENT_SPLIT.split(name)
    return any(segment.strip().lower() in _BLOCKLIST_SEGMENT_NAMES for segment in segments)
