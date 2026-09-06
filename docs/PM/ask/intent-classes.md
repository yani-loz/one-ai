# Ask layer — universal intent classes (router taxonomy)

**Status:** IMPLEMENTED in `backend/app/ask/services/router.py:30-100` (`INTENT_CLASSES`) — all six classes
below shipped, their names verbatim, each with a condensed definition, a tool kit and a procedure block.
The routed arm was **measured with
a kit-shrink confound** (`router.py:26-29`: the rescinded counterparty-summary tools lingered in the kits,
silently shrinking 4 of the 6 and pointing procedures at phantom tools) and is **PARKED** — see
`ASK-01-ask-layer-architecture.md` §5. Originally circulated as DRAFT for founder review (2026-07-04);
status verified 2026-09-06. · **Owner:** ask-tools loop
**Derivation protocol:** benchmark questions abstracted to intent *shapes* (entities/dates/companies stripped) → clustered → each class passed the universality test → coverage checked against the 170-type question taxonomy in both directions. Benchmark questions are evidence of demand, never definitions — no class may name or paraphrase a specific gold question (verifier-audited).

**The universality test (every class must pass all three):**
1. Definition mentions nothing only our corpus has (no source names, company names, domain quirks).
2. Unlimited fresh instances can be generated for a hypothetical company we've never seen.
3. Maps onto the workload-derived question-type taxonomy (not onto the benchmark file).

---

## The six classes

### 1. `entity_lookup` — Entity identification & contact resolution
**Definition:** Identify a person or organization and resolve its attributes: who they are, role/affiliation as evidenced in communications, email addresses/aliases, associated organizations, related people.
**Example shapes (fictional):** "Who is Maria Weber?" · "What is the email address of the CFO at Acme GmbH?" · "List everyone associated with Nordwind Logistics."
**Taxonomy mapping:** A1 Person Lookup, A8 Contact Info Lookup; J89–93 (network views of the same data).
**Tool implications:** person/org resolution with fuzzy + alias matching (trigram, normalized addresses), entity → address/affiliation projections.

### 2. `content_search` — Targeted content retrieval
**Definition:** Find specific communications, documents, or attachments by any combination of topic/keyword, participant, direction, type, folder, or attribute; return the items themselves (or their identifiers) as the answer.
**Example shapes:** "Find emails about the framework contract with Acme." · "What documents has their project manager sent us?" · "Do we have any .zip archives from vendors?"
**Taxonomy mapping:** A3 Email Search, A4 Document Retrieval, A7 Attachment Search; P126 Specification Scavenger Hunt, P134 Find the Decision, Q139 Contract & Commercial Memory.
**Tool implications:** hybrid search (FTS + semantic when available), attachment/type filters, participant scoping, evidence-id returns.

### 3. `aggregation` — Counting, ranking & distribution
**Definition:** Compute counts, totals, extremes (largest/oldest/most), top-N rankings, or distributions over the communication corpus; the answer is a number, a ranked list, or a comparison — computed, never enumerated by the model.
**Example shapes:** "How many email threads do we have?" · "Top 10 organizations by people involved." · "What's the largest attachment we ever received?" · "Compare communication volume across our clients."
**Taxonomy mapping:** A9 Aggregation/Stats; G67–68 (volume/ratio analyses); H, I86 Velocity Comparison.
**Tool implications:** typed aggregate tools (count/top-N/extreme with group-by), so the weak reader never hand-counts rows.

### 4. `temporal_activity` — Time-anchored activity & timeline
**Definition:** Answer when something first/last happened, reconstruct chronological sequences, assess activity within a time window, or detect changes in communication cadence (e.g., a counterparty going quiet).
**Example shapes:** "When did we last hear from Acme?" · "Full timeline of the Phoenix project communications." · "Has anyone from Nordwind been active in the last 90 days?" · "Which clients stopped replying recently?"
**Taxonomy mapping:** A2 Activity Check, A10 Timeline/History; I84–88 Temporal Patterns; D24 Deal Risk (the went-quiet signal); B16 Communication Timeline.
**Tool implications:** first/last-contact projections, windowed activity queries, chronological ordering with stable pagination.

### 5. `synthesis` — Multi-object dossier & full picture
**Definition:** Assemble a briefing from MANY objects across the corpus about one entity, relationship, or project: who's involved, what happened, current state, key documents. Inherently multi-hop; the answer is structured prose grounded in retrieved evidence.
**Example shapes:** "Give me a complete overview of our relationship with Acme." · "Full dossier on their lead engineer." · "What do we know about the Phoenix initiative? Check everything."
**Taxonomy mapping:** B11–16 Cross-Source Synthesis; Q135–141 Account Management; U164–167 Crisis Context; M110 Institutional Memory Retrieval.
**Tool implications:** the multi-tool class — composes classes 1–4; benefits most from pre-joined dossier/timeline views to cut hop count for a small reader.

### 6. `existence_check` — Existence, coverage & no-data honesty
**Definition:** Determine whether anything matching a description exists at all, and say "no data" with confidence when it doesn't. The answer's value is in the verified absence as much as the presence.
**Example shapes:** "Do we have any contact at Acme's Vienna office?" · "Are there any invoices from Q1 2023?" · "Did anyone ever mention 'penalty clause'?"
**Taxonomy mapping:** N112 Data Gap Awareness; the negative/edge variants that appear across every taxonomy family (the benchmark's no-data traps instantiate this).
**Tool implications:** tools must return distinguishable "zero results" (not errors); counts before content; the empty-behavior gate lives here.

---

## Routing notes

- **Flat router, ≤7 options** (6 classes + low-confidence fallback → generalist with shared-core tools). Depth added only if measurement demands it.
- **Shared core available to every specialist:** entity resolution, basic search, fetch-by-id. Class kits add their specialized tools on top.
- **Cross-class questions** route to `synthesis` (the composition class) — that is its job, not a router failure.
- Analytic/judgment questions (sentiment, prediction, risk scoring) are NOT retrieval classes: they route to the class whose *evidence* they need (usually `synthesis` or `temporal_activity`); the judgment happens in the reader, not the tools.

## Coverage check (taxonomy → classes, both directions)

- Every taxonomy family (A–V) maps into at least one class for its *retrieval* component; families whose essence is judgment/prediction (H, K) map via their evidence needs.
- Classes with no current email-corpus benchmark coverage: none — all six are instantiated by the current email-answerable benchmark subset. Slack/Fathom arrival extends instances, not classes (source-agnostic definitions).

## Anti-bias controls on this artifact

- Class definitions and examples use fictional entities only.
- The Opus verifier audits this file the same as tool diffs: wording that paraphrases a specific gold question = GAMED at the router layer.
- Changes to class definitions after the loop starts = a new experiment arm (they reshape the router's decision space), never a silent edit.
