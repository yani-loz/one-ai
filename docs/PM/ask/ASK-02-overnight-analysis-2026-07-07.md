# Overnight analysis — 2026-07-07 (EXP-001 M-33…M-47 wave + N=3 protocol)

**What this is:** adversarial analysis of the overnight agent logs, verified against the live lab DB
(`exp001-lab-db`) and reconciled with `ASK-02-small-model-to-100-safe.md`. Method: 5-agent wave
(reconstruct / verify / reconcile / target-side / laws); every claimed pass treated as unproven until
checked. **Companion:** ASK-02 (architecture) + ASK-02-findings-report.

---

## 1. The honest number: **27/43, not 33/43** (high confidence, DB-verified)

- **27/43 (62.8%)** is the only complete judged roll (the FINAL GATE, judged by r1–r4 + a D25.1 retry;
  reconciles exactly to the `rec_finalgate_*.json` files: 26 fresh judged passes + A8.2). The auto-grader
  alone gave **8/43** — human judges lifted it to 27.
- **33/43 (76.7%) is a per-question *best-of-N union*, not a run.** It = the 27 that passed the final gate
  **∪** 6 questions (A7.2, A10.3, B12.2, D34.3, E43.1, G62.2) that passed a *prior* roll but **failed the
  final gate**. It is reproducible in **no single roll** and assumes an oracle that picks the lucky roll
  per question — which production does not have.
- **The N=3 ensembling that would legitimately convert variance is UNRUN:** ROLL-2 died at 4/43 answered,
  unscored; no ROLL-3 exists. The ledger's last line is literally "### ROLL-2 launched."
- **Even 27 is a lucky-high roll** — it sits at the *top* of the measured single-roll variance band
  **24→27** (FP8 quantization non-determinism; 6 passes swapped out on this very roll).
- **Sample re-grade found real fabrications inside the 33-union** (DB-verified): D34.3's roll answer cited
  `v.georgiev@parfium.bg` "5 replies" — that address sent **0** emails; E43.1 missed the defining
  cancellation anchor; E41.3 mislabeled a 158 corpus-total as "sent to clients." Confirmed real passes:
  G69.1 (vendor counts DB-exact), A8.2.

**Report to the founder:** *realized capability ≈ 24–27/43 per roll (best observed 27); 33 is an
aspirational best-of-N ceiling, unproven as a deliverable.* And it's a **dev-set** number — no holdout
re-measurement ran overnight, so it's a ceiling, not a production estimate.

**Fair credit:** this is still real progress — the lab was ~19/43 at yesterday's close; the projection
work moved honest single-roll to ~27. The gain is real; the *headline* is inflated.

---

## 2. It strongly VALIDATES the ASK-02 architecture (the important part)

The overnight work independently confirmed the core thesis on a different corpus:

- **"DB computes, model renders" is the winning mechanism.** The first-ever Gemma passes all came from
  **precomputed projection payloads**, not prompt tricks: D34.1 (alias-merged relationship history),
  B14.3 (billing surface), G69.1 (party-role tags), D29.2 (distinct-count relationship rollup), A1.3
  (uniqueness note). This is ASK-02 §4.1–§4.2 working.
- **The `total_mentions` defect (F9) is confirmed.** M-36b's `count(DISTINCT m.id)` fix corrected a ~13×
  over-count (apis 22.5→3.8/wk) — exactly the "replace the double-counting metric with distinct-message
  rollup" fix in ASK-02.
- **Capabilities behind existing tools, no new model-visible tools** — matches §4.1.2 (few tools; no
  router). Projections were delivered through existing tool payloads.
- **Reader omission/truncation of *correct* payloads** (G69.2, A3.2 drop DB-real tail rows) empirically
  confirms §4.3: completeness must be enforced by a deterministic renderer with mandatory claim classes,
  **not** left to the model or a prompt nudge.
- Some **L0 fixes are already implemented in the product working tree** (`shared_core.py`): the
  `participants` AND-filter (F2), the NULL-ILIKE coalesce trap, an always-on Cyrillic/Latin coverage note
  (F7). C1 is partially underway on the real target.
  *(2026-09-06: `shared_core.py` has since been split into per-domain modules and is now a 41-line
  assembly file. Those three fixes are still there, at `backend/app/ask/tools/email_filters.py:33-45`
  and `:112` — the `participants_all` party groups and their `coalesce(...)` NULL-safe ILIKE — and
  `backend/app/ask/tools/email_search.py:230-243` — the always-on `language_coverage` note. All of it
  is still uncommitted.)*

---

## 3. Where it DIVERGES — and why those parts must NOT be ported

Every divergence is a case of *chasing pass-count over safe-disposition* — the exact trade ASK-02 forbids:

- **N-of-3 used backwards.** ASK-02 uses N-best *disagreement → escalate* (safety). The overnight uses
  *majority → pass* (score), which **masks** the FP8 variance ASK-02 says to surface. Do not adopt.
- **Projections built as MATERIALIZED TABLES** (`lab_org_activity`, `lab_party_roles`, `lab_thread`, …),
  not `security_invoker` views — the lab has no RLS, so ASK-02's non-negotiable ("never materialize a
  tenant aggregate," finding F12) was **bypassed, not tested.** Porting these tables to production would
  reproduce the leak I demonstrated. Rebuild the *content* as live views.
- **The safety layer is inverted.** The overnight built a *generative, asymmetric* "no-flip /
  anti-literalism" lane that pushes the model *away* from negative/narrow answers — and it **manufactured
  a false-Yes (G62.3) and a false-No (C22.1: dismissed CodeBot as "not Data+" when CodeBot *is* Data+).**
  That is the confident-wrong failure ASK-02's deterministic *symmetric* verifier exists to kill. Do not
  port.
- **No Answer-IR / deterministic renderer** — answer shaping was done by prompt enforcement, the approach
  §4.3 warns "silently re-opens the omission hole" (proven: M-40's depth-nudge oscillates run-to-run).
- **The harness never measured safe-disposition** — no unsupported-claim rate, no escalate/clarify
  dispositions at all. It optimized the wrong metric.

---

## 4. What CHANGES in ASK-02 (act on these)

1. **FP8 quantization variance is the dominant residual** — a new, empirically-measured finding (~3-question
   swing per roll, band 24→27). Add self-consistency / N-best **with variance bands** to L4 — but wired as
   **escalate-on-disagreement**, never majority-for-pass.
2. **Promote L3 (deterministic renderer + mandatory claim classes) to the critical path.** The dominant
   *post-projection* failure is the reader omitting/truncating correct payloads, and prompt nudges
   provably cannot fix it at FP8 (G69.2 stayed variance-bound across 3 rolls). Move it out of the C5/C6
   tail.
3. **Coverage estimate tempered, not raised.** Stable single-roll ≈27/43 is the *low* end of the earlier
   ~30–34 band — and it's dev-selected. Do not quote 33/76.7%.
4. **Projection *content* is validated → proceed with C4/C5**, but rebuild as `security_invoker` views and
   pair with a real L4 escalate disposition. Never port the materialized tables or the prompt-enforcement
   lanes.
5. **Adopt the overnight's genuinely useful engineering laws** (§5).

---

## 5. New laws worth adopting (production-relevant)

1. **Enforcement lanes must be fact-preserving** — accept a rewrite only if it names ≥ as many demanded
   facts (totals/spans/rows) as the prior draft; a markup-only guard is insufficient. → ASK-02 L3/L4: any
   completeness/verify lane needs a *monotonic fact-count floor*, or a verifier step silently regresses a
   correct draft.
2. **Channel-consistency extends to pipeline ordering** — every quality gate runs on the FINAL answer,
   *after* all rewrite lanes (an ordering bug truncated enumerations when a depth-check ran before the
   compute rewrite). → order L4's gates so nothing rewrites the answer after a gate passes.
3. **Per-entity flags over many messages use a SHARE/COUNT threshold, never `bool_or`** — one newsletter
   must not flag a human counterparty as automated. → ASK-02 `party_role` / `is_automated` /
   `is_free_mail` over `message_party_fact` (encode as ≥0.8 share, not any-true).
4. **Registry adoption requires closing prompt bypass licenses** — a "tags are hints" rule let the model
   re-derive enumerations from keyword sweeps. → validates renderer-MANDATED claim classes and keeping
   raw reader-SQL in shadow/analyst mode only.
5. **A forced-decision path can codify a wrong dismissal** when the model lacks the identity/equivalence
   fact. → ASK-02 L4 symmetric verifier: only force a decision when the required identity facts are *in
   the evidence set*; otherwise **escalate**, don't rule out.
6. **Identity: full display-name match = same person; shared name *token* alone = different person** —
   never conflate on token overlap. → ASK-02 `entity_dossier` / resolver: merge on identical local-part
   AND display name.

**Also adopt the acceptance discipline:** the **PROBE bias-audit** (a novel never-scored control of the
exact exploit shape, independent gold re-derivation, pre-agreed parity⇒clear / lift⇒revert, 22/22 clear)
— the anti-grader-artifact protocol, directly reusable for gating ASK-02 mechanisms.

---

## 6. Target reality (EXP-002) is unchanged — and separate

- **Best target config = trunk-v2 (CKPT2): 9/28 = 32.1% dev, 0/15 holdout.** No config beats 9/28 on a
  full 28-question run. The lab's 76.7% is a **different corpus (5,982), benchmark (A/B/C/… qids), and
  shim** — it does **not** transfer.
- **Real target-side wins this cycle (DB-verified):** the **XiYan SQL arm** drove V026 — the first-ever
  aggregation-cluster pass (named all five gold clients ocenki/gbs/apis/dataplus/gang); **attachment
  paging** converted V027 0/3→3/3 (the Tridea/Vetera contract terms — 19,817 EUR, 40/60 split — sit at
  char ~11,313, past the 5k cap); **`date_span` anchors** fixed the "last communication" recency bug
  (breeze span 2024-09-10..2026-05-26 = gold exact).
- **Rejected/inconclusive:** V2MUT6 completeness-enumeration (0 enum targets moved — enumeration doesn't
  yet engage the >50-row questions); V2COMPOSITE / XIYAN escalated at rung-1 only (+1 each, the V026
  flip).

**Net:** the overnight work is a strong *validation* of the ASK-02 direction and a *warning* about the
score-chasing shortcuts to avoid. It does not change the plan; it sharpens it (promote L3; add
escalate-on-disagreement; adopt the 6 laws + PROBE discipline; keep projections as live views).

---

## 7. Campaign-close addendum (2026-07-08) — what happened after §1-§6 were written

**Source:** the EXP-001 ledger archive (`archive/CAMPAIGN-1.md`) final scoreboard + close-out. §1-§6 above
predate the close; this section records the verified end-state. Numbers are not softened or inflated.

- **The N-of-3 protocol §1 called UNRUN was completed.** 3 full identical rolls, 2-of-3 majority:
  **26/43 (60.5%)** — the honest ensemble number, and the one to quote. Single-roll variance band confirmed
  **24→28** (FP8 non-determinism, one point wider than §1's observed 24→27). **§1's warning stands: 33/43
  remains a best-of-N union, not a run** — the ensemble did not lift the union to a run, it landed *below*
  the best single roll.
- **Sealed-24 one-shot holdout (founder-authored, protocol declared before the run, ZERO harness changes in
  response) confirms §1's dev-set caveat and quantifies it.** Email-answerable: **2/6 (33%)** vs the
  email43 band **56–63%** ⇒ the founder's projection-adaptation-bias hypothesis is **CONFIRMED and
  quantified at ~20–30 points** — that fraction of the email43 headline is portfolio-selection adaptation,
  not transferable capability. **18/24 were channel-gap** (need Slack 802 day-files / Fathom 258 transcripts
  never ingested into the lab DB) — a *provisioning* report, not a capability ceiling. **Zero fabrication
  across all 24**: the discipline stack (citations, no-data gate, evidence fidelity) generalizes to an
  unseen holdout even where *capability* does not — the safety thesis holds off-distribution.
- **The ASK v1 architecture arms were measured — a stronger reader is NOT a superset, composition decides.**
  - **Arm 0** — single-call strong judge (Kimi K2.6) over the parked six: **0/6**. A one-shot judge cannot
    repair retrieval-gap failures; the miss is upstream of the reader.
  - **Arm 1b** — Kimi K2.6 solo: **30/43 reconciled.** Class-split: converts synthesis / role-depth /
    computation / diagnosis; does **NOT** convert recognition / completeness; and **REGRESSED D29.2** (the
    SQL-lane distinct-count win). A stronger reader is not a strict superset of the weaker-reader-plus-lanes.
  - **Arm 2** — supervisor-loop v1 (Kimi supervisor + Gemma lanes): **22/43 — NEGATIVE.** Lane hops
    pre-truncate evidence (the "bandwidth law"), forfeiting the synthesis depth a solo reader gets from one
    uninterrupted context.
  - **Declared winner:** strong **solo reader** over the full tool/projection stack + a **local text-to-SQL
    compute lane** + **N-best as escalate-on-disagreement** (never majority-for-pass — exactly §3's law).
    **Kept from the supervisor arm:** aftermath-fetch mandate, per-candidate audit form, budget/honest-miss
    shell.
- **Two late laws are now lab-validated and porting to `backend/app/ask/`:**
  - **M-49 (NULLS law)** — Postgres `DESC` sorts NULLs *first*, so a NULL row is never the honest top-1.
    Advisory prompt notes do **not** bind a 7B SQL model; only a **mechanical `DESC → DESC NULLS LAST`
    rewrite in the guard** works (adding to `sql_guard.py`). Extends §5's channel-consistency/ordering laws.
  - **M-36b / F9 (counterparty rollup)** — must count **DISTINCT messages, never message×party contact
    rows** (~**13×** over-count measured — reconfirms §2's `total_mentions` defect). `counterparty_summary`
    v3 migration in progress.
  - **M-48 (cross-verifier ordering)** — a decorrelated cross-verifier pays off **only when placed LAST**
    in the pipeline, with an audit-leak guard. Reinforces §5 law 2 (every gate runs on the final answer).
- **Final scoreboard for the record:** Gemma-band single-roll **24–28** · N-of-3 **26/43** ·
  demonstrated-capability union **33/43** · Kimi solo **30/43** · supervisor-v1 **22/43** · sealed
  email-answerable **2/6** · sealed channel-gap **18/24** · fabrication **0**. **The 85% target was NOT
  reached; experiments were stopped per founder instruction (2026-07-07).**
- **Consequence for ASK-02 priorities (consistent with §4): the capability frontier is NOT more harness.**
  It is (a) **multi-channel ingest** (Slack / Fathom) to close the sealed channel gap — 18/24 of the
  holdout misses were unprovisioned channels, not reasoning failures; (b) the **L3 deterministic renderer**
  for the recognition/completeness class the stronger reader could not convert (§4 item 2, now double-
  confirmed by Arm 1b's class-split); (c) a **fresh founder holdout set** — sealed-24 is spent.
