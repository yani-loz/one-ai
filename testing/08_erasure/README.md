# Target 08 — GDPR erasure + compliance export (PC-06) — Adversarial Validation

> Dynamic, adversarial validation of **PC-06 erasure** against the **live stack**: `POST
> /platform/orgs/{id}/erase` (slug-confirmed, **sudo-password-gated**, legal-hold-gated, atomic) + `GET
> …/compliance-export`. Companion to `docs/audits/2026-06-01_erasure-dynamic-adversarial.md` + the static
> review `docs/audits/2026-06-01_platform-erasure-pr6-review.md` (4 fixed). Case code **`ER`** (`TC-ER-NNN`).

## Environment

- Live stack `:8000`; harness inside the backend container, self-contained over stdin:
  `cat testing/08_erasure/harness/_common.py testing/08_erasure/harness/<script>.py | docker compose exec -T backend python -`
- The live `/erase` body requires `{reason, confirm_slug, password}` (sudo re-auth, commit `13da7fe`); the
  `erase_org` helper sends `password=PLATFORM_PW` by default. psql ground-truth on the **db** container.
- **HARD RULE — IRREVERSIBLE:** erasure deletes users/tokens + offboards. **Only ever erase your own fresh
  run-stamped orgs.** Never demo/globex.

## Status dashboard

> Result: ✅ pass · ❌ fail (a defect/the win) · ⚠️ pass-with-concern. Tag: 🆕 NEW · ✔ CONFIRMS-FIXED ·
> ✖ REFUTES-FIX · 📋 CONFIRMS-DOCUMENTED · — n/a.
> **Run 2026-06-01.** **16 cases · 15 ✅ · 1 ⚠️ · 0 ❌ · 1 🆕.** PC-06 sound on every contract. **The 🆕 is the
> flipped headline: the forged-token erase is now BLOCKED (403) by a new sudo-reauth (`13da7fe`) — see the
> audit.**

| Suite | Cases | Result spread | NEW | Notes |
|---|---|---|---|---|
| HOLD — legal-hold + slug guard | TC-ER-001..004 | 4 ✅ | 0 | legal-hold → 409 nothing-touched (psql); slug-mismatch → 400; order slug-before-hold; row-lock TOCTOU corroborated (32 iters self-consistent) |
| ERASE — completeness + PII sweep | TC-ER-010..013 | 4 ✅ | 0 | honest certificate; psql sweep: 0 users/tokens, decider-email scrubbed, requester-email kept, offboarded; erased admin → 401; idempotent re-erase |
| RETAIN — audit retained + export | TC-ER-020..023 | 4 ✅ | 0 | append-only survives + `org.erased` logged (Art. 17(3)); export = metadata + trail, content-blind; export-after-erase builds; unknown → 404; wrong-pw → 403 |
| AUTHZ — audience + forged-token | TC-ER-030..033 | 3 ✅ · 1 ⚠️ | 1 | company-aud + real-company token → 401; **🆕 TC-032 forged erase BLOCKED (403, sudo-reauth)**; ⚠️ TC-033 forged export 200 |

## Coverage → PC-06 acceptance criteria

| AC | Criterion | Dynamic proof |
|---|---|---|
| ⭐ PC-06-AC1 | legal-hold → 409, nothing deleted | ✅ TC-ER-001 (psql all facets) |
| ⭐ PC-06-AC1b | race-safe guard (FOR UPDATE) | ✅ TC-ER-004 (corroboration) |
| PC-06-AC2 | deletes users + tokens, offboards, certifies | ✅ TC-ER-010/012 |
| ⭐ PC-06-AC3 | scrubs decider email, keeps requester email | ✅ TC-ER-011 (psql) |
| ⭐ PC-06-AC4 | append-only audit retained; erasure logged | ✅ TC-ER-020 |
| PC-06-AC5 | slug mismatch → 400, nothing deleted | ✅ TC-ER-002/003 |
| ⭐ PC-06-AC5b | sudo password re-auth (403 wrong / 422 absent); forged token → 403 | 🆕 TC-ER-032, ✅ TC-ER-023 |
| ⭐ PC-06-AC6 | both endpoints reject a company token (401) | ✅ TC-ER-030/031 |
| PC-06-AC7 | compliance export = metadata + trail | ✅ TC-ER-021/022 |

> **Provenance note:** the ERASE-suite per-case files (010–013) were rebuilt from the workflow output (lost
> to mid-run filesystem churn + repo cleanup); the other suites' files are the agents' originals. The
> consolidated narrative — incl. the flipped forged-erase headline — is the audit doc.
