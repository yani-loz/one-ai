# MEM-01 PROTOCOL v1 — labeling rulebook (Stage A stub, non-PII)

| | |
|---|---|
| **Document** | The labeling rulebook of the MEM-01 step-1 gold release. It is the LAW the labelers
follow; a disagreement amends this file first and the affected records are relabeled under the
corrected rule (gold-standard §5 item 2). |
| **Status** | v1 STUB, Stage A (2026-09-06). Stage A writes NO labels: this file fixes the shape,
the vocabulary and the review states so the manifest can hash a stable rulebook and the schemas
under `schemas/` have a normative prose counterpart. Every section marked `STAGE B` is filled by
the labeler session before the freeze. |
| **Copies** | Canonical copy in the repo at `backend/tools/mem01_verify/release/PROTOCOL.v1.md`;
copied verbatim into every release at cut time and hashed as `protocol_sha256` in
`dataset.manifest.json`. The two copies are byte-identical by construction. |
| **PII** | This file contains NO personal data and NO case content. It may live in the repo. |

---

## 1. Scope and non-goals

This rulebook governs how a human or model labeler assigns labels to records of the 17 sets
(`QS, CH, NF, LANG, IDEM, VIS, ERASE, RET, COV, FID, THR, TIME, IDENT, RED, ATTR, SNAP, EMB`).

It does NOT define criteria, thresholds or acceptance: those live in `criteria.step1.v1.yaml`,
the normative formula sheet. A labeler never reads a threshold; a criterion never redefines a
label.

## 2. The record standard (gold-standard §2.1)

Every record of every set carries the shared core, whatever its set:

- `gold_id` — the record identity. Unique within a SET, stable for the life of the release, never
  reused after a correction; the roster in `dataset.manifest.json` lists exactly these ids.
- `set_id`, `set_version`, `split` (`optimization` | `test` | `validation`).
- `input` — the exact system-visible snapshot (verbatim), its source reference, its
  `snapshot_sha256`, and the renderer / parser / quote-stripper / chunker versions that produced
  it.
- `label` — the per-set typed schema of §4 below (one union label for all sets is FORBIDDEN).
- `strata` — language, format tags, length (numeric + bucket), attachment types and count,
  adversarial tags.
- `leakage_groups` — thread id, near-duplicate / template cluster, attachment content hash.
- `provenance` — `labeled_by` (model + config hash for pre-labels), `review_status`,
  `protocol_version`, `origin`.
- `note` — free text, non-normative.

**Offsets are UTF-8 byte offsets into the exact snapshot** — never character indexes, never any
model's token offsets. The snapshot emitter records both `byte_len` and `scalar_len` per artifact
so either can be validated.

## 3. Review states and origins

`review_status` is one of `unreviewed`, `reviewed_agree`, `corrected`, `adjudicated`, `excluded`.
A boolean "audited" is insufficient: corrections and exclusions must stay visible.

`origin` is one of `initial`, `bug-YYYY-MM-DD`, `posthoc-qrel`.

Where truth is genuinely non-unique the label lists acceptable alternatives instead of picking
one arbitrarily.

## 4. Per-set label shapes (gold-standard §2.2 — essentials)

| Set | Label shape |
|---|---|
| QS | Spans (UTF-8 byte offsets) for new content, redundant quote, signature, forwarded content; observed-structure tags; client attribution `verified` / `unknown`, never inferred from a text template. |
| CH | None — invariants checked mechanically over the corpus and boundary fixtures. |
| NF | `document` \| `noise(type)`, plus a duplicate-group id. OCR-required and unsupported-format files are `document`, never noise. |
| LANG | `bg` \| `en` \| `mixed` \| `und`, assessed on quote/signature-stripped prose (§5). |
| RET | Query, language, relevant SOURCE SPANS with acceptable alternatives, direction. Chunk ids are resolved artifacts, never the answer key. |
| COV | None — the frozen physical-input roster plus each input's disposition. |
| FID | Required content units from the ORIGINAL file, never from current extracted text. |
| THR | Required resolvable links (must-join) and forbidden pairs (must-not-join). |
| TIME | Declared source value, parsed instant (or `unknown-zone`), sent/received/ingested provenance. |
| IDENT | Participant observations, verified alias pairs with provenance, must-remain-distinct pairs. |
| RED | Secret-shaped canaries by class (positives) and protected non-secret control spans (negatives). |
| ATTR | Forwarding sender, claimed original author, source-span provenance, state `recoverable` / `ambiguous` / `unresolvable` (never NULL). |
| SNAP | None — canonical text hash per artifact across two clean replays, plus mapping fixtures. |
| EMB | None — per required chunk: vector present, version pins, reproducibility check. |
| IDEM, VIS, ERASE | None — scenario fixtures with expected end-states. |

## 5. Frozen operational definitions

- **`mixed` language** — each of the two languages carries at least 20% of the language-bearing
  word tokens, with at least 5 tokens each; below that the label is `und`. STAGE B: short-text and
  transliteration rules.
- **A confirmed alias** needs an authoritative act recorded as provenance (an explicit
  declaration, an admin confirmation, or a verified auth binding), never a similarity.
- **A disposition reason** is a deterministic policy-derived code recorded beside the scored
  disposition; when several policy clauses match, the MIME-class clause is named first.
- **`fully_redacted`** means no part of a canary survives on ANY surface it was passed through; a
  typed placeholder in its place is the approved transformation.

## 6. Labeling procedure (gold-standard §5)

0. Cell feasibility census over the full corpus BEFORE sampling; an infeasible cell is declared
   out of scope at design time and named in the manifest as `cells_out_of_scope`.
1. Scripted sample draw with the seed and strata recorded in `sample_seed.json`; the model
   pre-labels everything, with its model and config hash recorded per record.
2. The founder audits per the per-set shares. A disagreement amends THIS file first, then the
   affected records are relabeled under the corrected rule.
3. Freeze: manifest and criteria hashed, release named.
4. After any protocol change a fresh validation draw is audited; an old audit never proves a new
   rule.
5. Honest naming: model-labeled with a partial audit is *validated silver*; only fully audited
   sets are gold in the strict sense, and the distinction is recorded per set.
6. Builder is not optimizer. Blindness begins at the freeze: hidden splits move to the hidden
   root and every later optimizing session is a fresh context that has never seen them.

## 7. What Stage A fixed and what STAGE B must add

Fixed here: the shared core, the review-state and origin vocabularies, the per-set label shapes,
the operational definitions above, and the procedure.

STAGE B fills: per-set worked labeling examples, the adjudication rules for each set, the
short-text and transliteration rules for LANG, the designated-boilerplate list for the leakage
groups, the legacy-format (`.doc` / `.xls`) dispositions by file identity, and the per-set audit
shares.

## 8. Change log

- v1 (2026-09-06, Stage A) — stub created with the release tooling; no labels exist yet.
