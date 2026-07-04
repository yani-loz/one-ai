"""
Role: Per-run, transaction-aware memo for the entity resolver (perf lane #2) — remembers what this
      run has already resolved/recorded so the same ~800 participants are not fully re-resolved
      ~20x each per ingest run (2 SELECTs + 2 UPDATEs + 1-3 deliberately-failing savepoint INSERTs
      per participant per email, measured 2026-07-03). The race-safe get-or-create stays untouched
      underneath — the cache only skips REPEAT work within one run, never first-time work.
Used by: app.entities.services.entity_resolver (one cache per resolver = per sync run).
Depends on: SQLAlchemy session events (after_commit / after_rollback on the sync Session under the
            AsyncSession).
Key invariants:
  - TRANSACTION-AWARE, fail-safe on rollback: entries are STAGED during a transaction and only
    PROMOTED to the confirmed tier by the session's after_commit event; after_rollback discards
    staged entries (all of them on a transaction rollback; exactly the ones staged inside the
    unwound savepoint on a ROLLBACK TO SAVEPOINT — see __init__). A person minted inside a
    rolled-back scope can therefore never poison later emails with a dangling person_id.
  - Lookups see staged-then-confirmed (a participant repeated WITHIN one email hits the staged
    tier), so intra-transaction reuse works before any commit.
  - The cache stores only PLAIN values (UUIDs, strings, datetimes) — never ORM rows, which expire
    on commit/rollback and would lazy-load on a closed greenlet.
  - The seen-window tier is monotone-widening: `seen_window_covers` may only report True for a
    window this run has actually pushed (or committed) to the DB, so skipping the UPDATE is always
    conservative — the DB window is at least as wide as the cache claims.
  - Org id is part of EVERY key — one resolver run serves one org today, but the cache must never
    be the seam that breaks tenant isolation if that changes.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

# Cache values: UUIDs (person/company ids), True (existence markers), or (min, max) datetime
# windows. Keys are namespaced tuples — the first element names the tier.
_CacheKey = tuple
_CacheValue = UUID | bool | tuple[datetime, datetime]


class ResolutionCache:
    """Two-tier (staged/confirmed) per-run memo keyed by org-scoped resolution facts."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to the run's session: commit promotes staged entries, rollback discards them.

        Listeners attach to the instance-level sync Session, so they live and die with the
        session (no global listener leakage across runs or tests).

        SAVEPOINT DISCRIMINATION (measured 2026-07-04, SQLAlchemy 2.x + asyncpg): `after_commit`
        fires on RELEASE SAVEPOINT and `after_rollback` on ROLLBACK TO SAVEPOINT — not only on
        real transaction boundaries as the docs suggest. A savepoint release mid-email must NOT
        promote staged entries (the enclosing transaction can still roll back), so the cache
        tracks open-savepoint depth via after_transaction_create/end and promotes ONLY at
        depth 0. Verified ordering: the savepoint's commit/rollback event always fires while
        its nested transaction is still open (depth >= 1), the real ones at depth 0.

        Every staged entry is TAGGED with the savepoint depth it was staged at: a ROLLBACK TO
        SAVEPOINT (after_rollback at depth d >= 1) discards exactly the entries staged inside
        the savepoint being unwound (tag >= d), while entries staged before it stand — so a
        caller that wraps resolver work in its own begin_nested() can never poison the cache
        with ids from a rolled-back savepoint (2026-07-04 review, P1 hardening).
        """
        self._confirmed: dict[_CacheKey, _CacheValue] = {}
        # key → (value, savepoint depth at staging time) — see the depth-tag contract above.
        self._staged: dict[_CacheKey, tuple[_CacheValue, int]] = {}
        self._savepoint_depth = 0
        sync_session = session.sync_session
        event.listen(sync_session, "after_transaction_create", self._track_savepoint_open)
        event.listen(sync_session, "after_transaction_end", self._track_savepoint_close)
        event.listen(sync_session, "after_commit", self._promote_staged)
        event.listen(sync_session, "after_rollback", self._discard_staged)

    # ── person: normalized email → person_id ──────────────────────────────────────────────

    def person_id(self, org_id: UUID, normalized_email: str) -> UUID | None:
        """The cached person owning `normalized_email` in this org, or None if unseen this run."""
        value = self._lookup(("person", org_id, normalized_email))
        return value if isinstance(value, UUID) else None

    def stage_person(self, org_id: UUID, normalized_email: str, person_id: UUID) -> None:
        """Remember a resolved person (confirmed at the next commit)."""
        self._stage(("person", org_id, normalized_email), person_id)

    # ── company: eTLD+1 key → company_id ──────────────────────────────────────────────────

    def company_id(self, org_id: UUID, company_key: str) -> UUID | None:
        """The cached company keyed by `company_key` in this org, or None if unseen this run."""
        value = self._lookup(("company", org_id, company_key))
        return value if isinstance(value, UUID) else None

    def stage_company(self, org_id: UUID, company_key: str, company_id: UUID) -> None:
        """Remember a resolved company (confirmed at the next commit)."""
        self._stage(("company", org_id, company_key), company_id)

    # ── existence markers: name backfilled / alias recorded / host evidence / person-company ──

    def has_person_name(self, org_id: UUID, person_id: UUID) -> bool:
        """True once this run has ensured the person carries a non-empty display_name."""
        return self._lookup(("named", org_id, person_id)) is True

    def stage_person_name(self, org_id: UUID, person_id: UUID) -> None:
        """Mark the person as definitely named (a backfill with a non-empty name just ran)."""
        self._stage(("named", org_id, person_id), True)

    def has_alias(self, org_id: UUID, person_id: UUID, alias: str) -> bool:
        """True once this run has ensured this exact alias row exists."""
        return self._lookup(("alias", org_id, person_id, alias)) is True

    def stage_alias(self, org_id: UUID, person_id: UUID, alias: str) -> None:
        """Mark the alias as recorded (insert succeeded or the UNIQUE proved it already exists)."""
        self._stage(("alias", org_id, person_id, alias), True)

    def has_company_host(self, org_id: UUID, company_id: UUID, host: str) -> bool:
        """True once this run has ensured this observed-host evidence row exists."""
        return self._lookup(("host", org_id, company_id, host)) is True

    def stage_company_host(self, org_id: UUID, company_id: UUID, host: str) -> None:
        """Mark the observed host as recorded for the company."""
        self._stage(("host", org_id, company_id, host), True)

    def has_person_company_link(self, org_id: UUID, person_id: UUID, company_id: UUID) -> bool:
        """True once this run has ensured the person↔company link row exists."""
        return self._lookup(("link", org_id, person_id, company_id)) is True

    def stage_person_company_link(self, org_id: UUID, person_id: UUID, company_id: UUID) -> None:
        """Mark the person↔company link as recorded."""
        self._stage(("link", org_id, person_id, company_id), True)

    # ── seen window: person → the [min, max] this run has pushed to the DB ────────────────

    def seen_window_covers(self, org_id: UUID, person_id: UUID, seen_at: datetime) -> bool:
        """True iff a window this run already pushed to the DB contains `seen_at`.

        Conservative by construction: the DB window can only be WIDER than the cached one
        (LEAST/GREATEST widening), so a True here means the UPDATE would be a guaranteed no-op.
        """
        window = self._lookup(("seen", org_id, person_id))
        if not isinstance(window, tuple):
            return False
        low, high = window
        try:
            return low <= seen_at <= high
        except TypeError:  # naive/aware mix across emails — treat as uncovered (the DB decides)
            return False

    def stage_seen_window(self, org_id: UUID, person_id: UUID, seen_at: datetime) -> None:
        """Widen the cached window to include `seen_at` (after the DB UPDATE was issued)."""
        key = ("seen", org_id, person_id)
        window = self._lookup(key)
        if isinstance(window, tuple):
            low, high = window
            try:
                self._stage(key, (min(low, seen_at), max(high, seen_at)))
            except TypeError:  # naive/aware mix — narrow to the latest push (conservative)
                self._stage(key, (seen_at, seen_at))
        else:
            self._stage(key, (seen_at, seen_at))

    # ── transaction wiring ─────────────────────────────────────────────────────────────────

    def _stage(self, key: _CacheKey, value: _CacheValue) -> None:
        """Stage `value` tagged with the CURRENT savepoint depth (see the __init__ contract)."""
        self._staged[key] = (value, self._savepoint_depth)

    def _lookup(self, key: _CacheKey) -> _CacheValue | None:
        if key in self._staged:
            return self._staged[key][0]
        return self._confirmed.get(key)

    def _track_savepoint_open(self, _session: object, transaction: object) -> None:
        if getattr(transaction, "nested", False):
            self._savepoint_depth += 1

    def _track_savepoint_close(self, _session: object, transaction: object) -> None:
        if getattr(transaction, "nested", False) and self._savepoint_depth > 0:
            self._savepoint_depth -= 1

    def _promote_staged(self, _session: object) -> None:
        """after_commit at depth 0: the just-committed transaction's staged facts are durable.

        At depth >= 1 this event is a RELEASE SAVEPOINT (see __init__) — the enclosing
        transaction can still roll everything back, so nothing is promoted.
        """
        if self._savepoint_depth:
            return
        self._confirmed.update({key: value for key, (value, _) in self._staged.items()})
        self._staged.clear()

    def _discard_staged(self, _session: object) -> None:
        """after_rollback: forget exactly what the rolled-back scope staged.

        Depth 0 = a REAL transaction rollback — every staged entry dies with it. Depth >= 1 =
        ROLLBACK TO SAVEPOINT (fires while the unwound savepoint is still open): only entries
        staged AT or BELOW that savepoint (tag >= depth) are discarded — facts staged before
        the savepoint still commit or roll back with the enclosing transaction.
        """
        if not self._savepoint_depth:
            self._staged.clear()
            return
        depth = self._savepoint_depth
        for key in [k for k, (_, tag) in self._staged.items() if tag >= depth]:
            del self._staged[key]
