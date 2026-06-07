#!/usr/bin/env python3
"""
Standalone IMAP fetch spike — NOT part of the app (no app imports, no third-party deps).

Purpose: validate FAST, COMPLETE, INCREMENTAL download of an entire mailbox (every folder,
every message, attachments included) to disk as raw .eml files — so we can SEE the fetch + sync
mechanism work against a real server before it graduates into the connector. The DB-backed
storage lands later (step 3 / parse); this only writes to disk.

Design (first principles, optimized for speed — not a port of any prior project):
  - PARALLEL: K IMAP connections, each draining folders off a shared queue, so several folders
    download at once (network I/O overlap is the main lever for a bandwidth-bound bulk pull).
  - INCREMENTAL: a per-folder cursor (uidvalidity + last_seen_uid) in a small JSON state file;
    a re-run fetches only NEW uids (UID last+1:*) and never re-downloads what's on disk.
  - IDEMPOTENT + RESUMABLE: a message is skipped if its .eml already exists; the cursor is
    persisted after every batch, so an interrupted run re-does only the in-flight batch.
  - BYTE-BOUNDED BATCHES: sizes are fetched first (RFC822.SIZE), then uids are packed into
    ~batch-mb chunks, so one FETCH amortizes many small emails without one big attachment
    ballooning memory.
  - READ-ONLY: BODY.PEEK[] never sets \\Seen; SELECT is readonly.
  - Modified-UTF-7 folder names (RFC 3501) are decoded so Cyrillic/non-ASCII folders are
    readable on disk.

Credentials: read from env vars IMAP_HOST / IMAP_PORT / IMAP_SSL / IMAP_USERNAME / IMAP_PASSWORD
(CONNECTOR_IMAP_* are accepted as a fallback). --env-file loads a KEY=VALUE file into the env
first. The password is never printed.

Usage:
  python imap_fetch.py --env-file path/to/.env --out ./imap_dump
  python imap_fetch.py --out ./imap_dump --concurrency 4 --batch-mb 10 --max-per-folder 0
  # --max-per-folder 0 = all messages; --max-folders 0 = all folders.
"""

from __future__ import annotations

import argparse
import base64
import imaplib
import json
import os
import queue
import re
import ssl
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# imaplib caps a single response line at 10 KB by default; raw emails with big headers/literals
# exceed that, so raise it (the literal payloads themselves are not subject to this limit, but
# long metadata lines can be).
imaplib._MAXLINE = max(imaplib._MAXLINE, 10_000_000)

_LIST_RE = re.compile(rb'^\((?P<flags>.*?)\) (?P<delim>"[^"]*"|NIL) (?P<name>.*)$')
_UID_RE = re.compile(rb'UID (\d+)')
_SIZE_RE = re.compile(rb'UID (\d+) RFC822\.SIZE (\d+)', re.IGNORECASE)
_UNSAFE_PATH = re.compile(r'[^\w.@+= -]', re.UNICODE)


# ───────────────────────── modified UTF-7 (RFC 3501 §5.1.3) ─────────────────────────
def _b64_to_text(chunk: str) -> str:
    data = chunk.replace(',', '/')
    data += '=' * (-len(data) % 4)
    return base64.b64decode(data).decode('utf-16-be')


def decode_mutf7(name: str) -> str:
    """Decode an IMAP modified-UTF-7 mailbox name to a readable Unicode string."""
    out: list[str] = []
    i = 0
    while i < len(name):
        char = name[i]
        if char == '&':
            end = name.find('-', i)
            if end == -1:
                end = len(name)
            chunk = name[i + 1 : end]
            out.append('&' if chunk == '' else _b64_to_text(chunk))
            i = end + 1
        else:
            out.append(char)
            i += 1
    return ''.join(out)


def safe_segment(value: str) -> str:
    """Make a string safe to use as a single path segment (no separators / control chars)."""
    cleaned = _UNSAFE_PATH.sub('_', value).strip('. ')
    return cleaned or '_'


# ───────────────────────────────── config ─────────────────────────────────
@dataclass(frozen=True)
class Config:
    host: str
    port: int
    use_ssl: bool
    username: str
    password: str
    out_dir: Path
    concurrency: int
    batch_bytes: int
    max_per_folder: int
    max_folders: int
    only_folders: list[str]
    skip_folders: list[str]
    timeout: float


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines from `path` into os.environ (does not overwrite existing keys)."""
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, val = line.partition('=')
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def _env(*names: str, default: str = '') -> str:
    for name in names:
        if os.environ.get(name):
            return os.environ[name]
    return default


def build_config(args: argparse.Namespace) -> Config:
    if args.env_file:
        load_env_file(Path(args.env_file))
    ssl_raw = _env('IMAP_SSL', 'CONNECTOR_IMAP_USE_SSL', default='true').lower()
    return Config(
        host=_env('IMAP_HOST', 'CONNECTOR_IMAP_HOST', default='imap.gmail.com'),
        port=int(_env('IMAP_PORT', 'CONNECTOR_IMAP_PORT', default='993')),
        use_ssl=ssl_raw in {'1', 'true', 'yes'},
        username=_env('IMAP_USERNAME', 'CONNECTOR_IMAP_USERNAME'),
        password=_env('IMAP_PASSWORD', 'CONNECTOR_IMAP_PASSWORD'),
        out_dir=Path(args.out),
        concurrency=max(1, args.concurrency),
        batch_bytes=max(1, args.batch_mb) * 1024 * 1024,
        max_per_folder=args.max_per_folder,
        max_folders=args.max_folders,
        only_folders=[f for f in (args.folders or '').split(',') if f],
        skip_folders=[f for f in (args.skip_folders or '').split(',') if f],
        timeout=args.timeout,
    )


# ───────────────────────────── progress / counters ─────────────────────────────
@dataclass
class Stats:
    lock: threading.Lock = field(default_factory=threading.Lock)
    folders_done: int = 0
    messages: int = 0
    bytes_written: int = 0
    skipped_existing: int = 0
    errors: int = 0

    def add(self, *, messages: int = 0, bytes_written: int = 0, skipped: int = 0) -> None:
        with self.lock:
            self.messages += messages
            self.bytes_written += bytes_written
            self.skipped_existing += skipped

    def folder_done(self) -> None:
        with self.lock:
            self.folders_done += 1

    def error(self) -> None:
        with self.lock:
            self.errors += 1


_PRINT_LOCK = threading.Lock()


def log(message: str) -> None:
    with _PRINT_LOCK:
        print(message, flush=True)


# ───────────────────────────── IMAP helpers ─────────────────────────────
def connect(cfg: Config) -> imaplib.IMAP4:
    if cfg.use_ssl:
        client: imaplib.IMAP4 = imaplib.IMAP4_SSL(
            cfg.host, cfg.port, ssl_context=ssl.create_default_context(), timeout=cfg.timeout
        )
    else:
        client = imaplib.IMAP4(cfg.host, cfg.port, timeout=cfg.timeout)
    client.login(cfg.username, cfg.password)
    return client


def list_folders(client: imaplib.IMAP4) -> list[str]:
    """Return raw IMAP folder names (modified-UTF-7), skipping \\Noselect containers."""
    typ, data = client.list()
    if typ != 'OK':
        return []
    names: list[str] = []
    for item in data:
        line = item if isinstance(item, bytes) else item[0]
        match = _LIST_RE.match(line.strip())
        if not match:
            continue
        if b'\\noselect' in match.group('flags').lower():
            continue
        raw = match.group('name')
        name = raw.decode('latin-1')
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1].replace('\\"', '"').replace('\\\\', '\\')
        names.append(name)
    return names


def select_uidvalidity(client: imaplib.IMAP4, raw_folder: str) -> int | None:
    """SELECT a folder readonly and return its UIDVALIDITY, or None if it can't be selected."""
    typ, _ = client.select(f'"{raw_folder}"', readonly=True)
    if typ != 'OK':
        return None
    _, vals = client.response('UIDVALIDITY')
    if not vals or vals[0] is None:
        return None
    return int(vals[0])


def search_new_uids(client: imaplib.IMAP4, min_uid: int) -> list[int]:
    """Return uids >= min_uid in the selected folder (UID SEARCH min:*), filtered + sorted."""
    # Pass criterion as separate args (UID, range) so imaplib sends `UID SEARCH UID n:*`
    # without quoting a space-containing single arg.
    typ, data = client.uid('SEARCH', 'UID', f'{min_uid}:*')
    if typ != 'OK' or not data or not data[0]:
        return []
    uids = sorted(int(u) for u in data[0].split())
    # 'min:*' always returns at least the highest existing uid even when none are >= min.
    return [u for u in uids if u >= min_uid]


def fetch_sizes(client: imaplib.IMAP4, uids: list[int]) -> dict[int, int]:
    typ, data = client.uid('FETCH', ','.join(map(str, uids)), '(UID RFC822.SIZE)')
    sizes: dict[int, int] = {}
    if typ != 'OK':
        return sizes
    for item in data:
        line = item if isinstance(item, bytes) else item[0]
        match = _SIZE_RE.search(line)
        if match:
            sizes[int(match.group(1))] = int(match.group(2))
    return sizes


def fetch_raw(client: imaplib.IMAP4, uids: list[int]) -> list[tuple[int, bytes]]:
    """FETCH BODY.PEEK[] for uids; return (uid, raw_rfc822_bytes) pairs."""
    typ, data = client.uid('FETCH', ','.join(map(str, uids)), '(UID BODY.PEEK[])')
    out: list[tuple[int, bytes]] = []
    if typ != 'OK':
        return out
    for item in data:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        meta, raw = item
        match = _UID_RE.search(meta)
        if match and raw is not None:
            out.append((int(match.group(1)), raw))
    return out


# ───────────────────────────── per-folder sync ─────────────────────────────
def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _load_state(state_path: Path) -> dict[str, int]:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            return {}
    return {}


def batch_by_bytes(uids: list[int], sizes: dict[int, int], batch_bytes: int) -> list[list[int]]:
    """Pack uids into batches whose total RFC822 size stays near batch_bytes (>=1 uid each)."""
    batches: list[list[int]] = []
    current: list[int] = []
    running = 0
    for uid in uids:
        size = sizes.get(uid, 0)
        if current and running + size > batch_bytes:
            batches.append(current)
            current, running = [], 0
        current.append(uid)
        running += size
    if current:
        batches.append(current)
    return batches


def highest_contiguous_uid(candidates: list[int], msg_dir: Path, floor: int) -> int:
    """Highest uid whose entire ascending prefix of `candidates` is on disk (stops before any gap).

    The cursor must only advance over messages that were DURABLY written: a partial/empty FETCH (or
    a per-folder cap) leaves a gap, and advancing past it would skip those uids forever (the next
    incremental run searches only uids > last_seen). Disk presence is the source of truth (writes
    are idempotent), so a re-run retries exactly the missing uids.
    """
    advanced = floor
    for uid in candidates:
        if uid <= floor:
            continue  # below the cursor already — never regress the high-water mark
        if not (msg_dir / f'{uid}.eml').exists():
            break
        advanced = uid
    return advanced


def sync_folder(client: imaplib.IMAP4, cfg: Config, raw_folder: str, stats: Stats) -> None:
    """Incrementally fetch one folder's new messages to disk (cursor-resumed, idempotent)."""
    display = decode_mutf7(raw_folder)
    uidvalidity = select_uidvalidity(client, raw_folder)
    if uidvalidity is None:
        log(f'  [skip] {display}: not selectable')
        return

    folder_dir = cfg.out_dir / safe_segment(cfg.username) / safe_segment(display)
    folder_dir.mkdir(parents=True, exist_ok=True)
    state_path = folder_dir / '_state.json'
    state = _load_state(state_path)
    # A UIDVALIDITY change invalidates stored uids → re-fetch from scratch (files dedupe).
    last_seen = state.get('last_seen_uid', 0) if state.get('uidvalidity') == uidvalidity else 0
    msg_dir = folder_dir / str(uidvalidity)
    msg_dir.mkdir(parents=True, exist_ok=True)

    candidates = search_new_uids(client, last_seen + 1)
    # Skip uids already on disk (idempotent re-run without re-fetching).
    pending = [u for u in candidates if not (msg_dir / f'{u}.eml').exists()]
    already = len(candidates) - len(pending)
    if cfg.max_per_folder > 0:
        pending = pending[: cfg.max_per_folder]
    if not pending:
        log(f'  [ok]   {display}: up to date ({already} already on disk)')
        advanced = highest_contiguous_uid(candidates, msg_dir, last_seen)
        if advanced > last_seen:
            state.update(uidvalidity=uidvalidity, last_seen_uid=advanced)
            _atomic_write(state_path, json.dumps(state).encode('utf-8'))
        stats.add(skipped=already)
        return

    sizes = fetch_sizes(client, pending)
    written = 0
    for batch in batch_by_bytes(pending, sizes, cfg.batch_bytes):
        for uid, raw in fetch_raw(client, batch):
            _atomic_write(msg_dir / f'{uid}.eml', raw)
            written += 1
            stats.add(messages=1, bytes_written=len(raw))
        # Advance only through uids durably on disk (stop before any gap from a partial/empty FETCH
        # or a per-folder cap) so missing uids are retried next run, never silently skipped.
        advanced = highest_contiguous_uid(candidates, msg_dir, last_seen)
        if advanced > last_seen:
            state.update(uidvalidity=uidvalidity, last_seen_uid=advanced)
            last_seen = advanced
            _atomic_write(state_path, json.dumps(state).encode('utf-8'))
    stats.add(skipped=already)
    log(f'  [done] {display}: +{written} new ({already} already on disk)')


# ───────────────────────────── workers ─────────────────────────────
def worker(cfg: Config, folders: queue.Queue[str], stats: Stats) -> None:
    """One IMAP connection draining folders off the shared queue (own connection per thread)."""
    try:
        client = connect(cfg)
    except Exception as exc:  # a worker that can't connect just bows out
        log(f'  [error] worker could not connect: {type(exc).__name__}')
        stats.error()
        return
    try:
        while True:
            try:
                raw_folder = folders.get_nowait()
            except queue.Empty:
                return
            try:
                sync_folder(client, cfg, raw_folder, stats)
            except Exception as exc:  # one bad folder must not kill the worker
                log(f'  [error] {decode_mutf7(raw_folder)}: {type(exc).__name__}: {exc}')
                stats.error()
            finally:
                stats.folder_done()
                folders.task_done()
    finally:
        try:
            client.logout()
        except Exception:
            pass


def select_target_folders(cfg: Config, discovered: list[str]) -> list[str]:
    if cfg.only_folders:
        wanted = {f.lower() for f in cfg.only_folders}
        chosen = [f for f in discovered if decode_mutf7(f).lower() in wanted or f.lower() in wanted]
    else:
        skip = {f.lower() for f in cfg.skip_folders}
        chosen = [f for f in discovered if decode_mutf7(f).lower() not in skip]
    if cfg.max_folders > 0:
        chosen = chosen[: cfg.max_folders]
    return chosen


def run(cfg: Config) -> int:
    masked = (cfg.username[:1] + '***@' + cfg.username.partition('@')[2]) if cfg.username else '?'
    log('=' * 70)
    log(f'IMAP fetch spike → {cfg.out_dir}')
    log(f'  server={cfg.host}:{cfg.port} ssl={cfg.use_ssl} account={masked}')
    log(f'  concurrency={cfg.concurrency} batch={cfg.batch_bytes // (1024 * 1024)}MB '
        f'max_per_folder={cfg.max_per_folder or "all"} max_folders={cfg.max_folders or "all"}')
    log('=' * 70)
    if not cfg.username or not cfg.password:
        log('ERROR: missing IMAP_USERNAME / IMAP_PASSWORD (set env or --env-file).')
        return 2

    try:
        probe = connect(cfg)
    except Exception as exc:
        log(f'ERROR: could not connect/authenticate: {type(exc).__name__}: {exc}')
        return 1
    discovered = list_folders(probe)
    try:
        probe.logout()
    except Exception:
        pass
    targets = select_target_folders(cfg, discovered)
    log(f'discovered {len(discovered)} folders, syncing {len(targets)} '
        f'with {cfg.concurrency} parallel connections...')

    work: queue.Queue[str] = queue.Queue()
    for folder in targets:
        work.put(folder)

    stats = Stats()
    started = time.monotonic()
    threads = [
        threading.Thread(target=worker, args=(cfg, work, stats), daemon=True)
        for _ in range(min(cfg.concurrency, max(1, len(targets))))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    elapsed = time.monotonic() - started
    log('=' * 70)
    log(f'DONE in {elapsed:.1f}s — folders={stats.folders_done}/{len(targets)} '
        f'new_messages={stats.messages} written={stats.bytes_written / (1024 * 1024):.1f}MB '
        f'already_on_disk={stats.skipped_existing} errors={stats.errors}')
    log('=' * 70)
    return 0 if stats.errors == 0 else 1


def main(argv: list[str] | None = None) -> int:
    # Folder names can be Cyrillic/non-ASCII; force UTF-8 stdio so logging doesn't crash on a
    # Windows cp1252 console or a redirected file.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description='Standalone IMAP → disk fetch spike.')
    parser.add_argument('--env-file', help='KEY=VALUE file with IMAP_* / CONNECTOR_IMAP_* creds')
    parser.add_argument('--out', default='./imap_dump', help='output folder for .eml files')
    parser.add_argument('--concurrency', type=int, default=4, help='parallel IMAP connections')
    parser.add_argument('--batch-mb', type=int, default=10, help='target FETCH batch size (MB)')
    parser.add_argument('--max-per-folder', type=int, default=0, help='cap messages/folder (0=all)')
    parser.add_argument('--max-folders', type=int, default=0, help='cap folders (0=all)')
    parser.add_argument('--folders', help='comma-separated folders to sync (default: all)')
    parser.add_argument('--skip-folders', help='comma-separated folders to skip')
    parser.add_argument('--timeout', type=float, default=30.0, help='socket timeout seconds')
    args = parser.parse_args(argv)
    return run(build_config(args))


if __name__ == '__main__':
    raise SystemExit(main())
