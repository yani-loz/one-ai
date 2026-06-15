"""Unit tests for app.connectors.imap.folder_policy — the ingest folder blocklist.

Pure, no I/O: proves Trash/Junk/Spam/Drafts (and their sub-folders) are excluded by name AND by
RFC 6154 SPECIAL-USE flag, while Sent/Archive/Inbox and substring-lookalikes are KEPT. This gate
is what stops the 28%-corpus Trash/Spam/Drafts pollution the 2026-06-14 data-quality audit found.
"""

from __future__ import annotations

import pytest

from app.connectors.imap.folder_policy import is_blocklist_special_use, is_excluded_folder


@pytest.mark.parametrize(
    "name",
    [
        "INBOX.Trash",
        "INBOX.Trash.Ciela",  # a sub-folder OF Trash is still trashed mail
        "INBOX.Trash.SelMatic",
        "INBOX.Junk",
        "INBOX.spam",  # lower-case, as the real corpus has it
        "INBOX.Drafts",
        "Trash",
        "Deleted Items",
        "INBOX/Junk",  # '/'-delimited server
    ],
)
def test_blocklist_folders_excluded(name: str) -> None:
    assert is_excluded_folder(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "INBOX",
        "INBOX.Sent",  # sent mail is real knowledge — KEEP
        "INBOX.Archive",  # archived mail is real knowledge — KEEP
        "INBOX.Clients.Denied.Alcomet",
        "INBOX.Bulbank.Bank Statements",
        "Spam Reports",  # whole-segment != 'spam' — substring false-positive guard
        "Draft Contracts",  # whole-segment != 'drafts'
        "INBOX.Trashcan Designs",  # 'Trashcan Designs' != 'trash'
        "INBOX.bin",  # 'bin' deliberately NOT blocklisted (a project folder must not be dropped)
        "Draft",  # singular 'Draft' kept — only plural 'Drafts' / the \\Drafts flag is blocklisted
    ],
)
def test_real_folders_kept(name: str) -> None:
    assert is_excluded_folder(name) is False


def test_special_use_flag_catches_localized_trash() -> None:
    # A non-English Trash folder (German 'Papierkorb') is caught by its RFC 6154 \\Trash flag even
    # though the name heuristic can't know every locale.
    assert is_excluded_folder("Papierkorb", ["\\HasNoChildren", "\\Trash"]) is True
    assert is_excluded_folder("Posteingang", ["\\HasNoChildren"]) is False


def test_is_blocklist_special_use() -> None:
    assert is_blocklist_special_use(["\\Trash"]) is True
    assert is_blocklist_special_use(["\\Junk"]) is True
    assert is_blocklist_special_use(["\\Drafts"]) is True
    assert is_blocklist_special_use(["\\Sent", "\\HasNoChildren"]) is False
    assert is_blocklist_special_use([]) is False
