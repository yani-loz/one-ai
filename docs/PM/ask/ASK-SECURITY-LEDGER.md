# Ask layer — security & correctness findings ledger

**What this is.** Every confirmed finding against the Ask layer, what closed it, and the test
that keeps it closed. A finding is CLOSED only when a named test would fail if the fix were
reverted — not when the code "looks fixed".

**Why it exists.** Four adversarial review rounds produced ~50 confirmed findings. Without a
ledger each round re-litigates the same ground: reviewers re-report what is already fixed, and
the operator re-verifies from memory. This file is the hand-off: a new review round is given
the CLOSED list and told to go past it, and anything it re-reports can be dismissed by pointing
at the pin rather than by arguing.

**Is it still sealed? Run it and see:**

```
cd backend && uv run python -m scripts.ask_loop.seal_check
```

That EXECUTES every pin in this file — each corpus attack through the real
`execute_guarded_sql`, the conformance suite, each pinned test module — and prints one line per
finding, SEALED or BROKEN, naming the pin that gave way. seal_check exits non-zero if any seal is
broken. NOT YET RUN BY CI: the workflow steps at `.github/workflows/ci.yml:59,62,69` are an uncommitted working-tree edit (absent from `git show HEAD:.github/workflows/ci.yml`), the trigger is `push: [main]` + `pull_request`, and branch `ask-tools-loop` has no remote. The table below is the map; that command is the proof.

**Environment note (2026-09-06).** Every seal credited to migration `0023_reader_bcc_and_seen_window`
below holds only on a database where 0023 has actually been applied. The dev database is stamped at
`0022_counterparty_summary_v3` — measured 2026-09-06 (`docs/audits/2026-09-06_built-vs-docs-map.md` §3)
— so on that database V9/V10/V11/V12 are open, and `seal_check` run against it would report those pins BROKEN.

**How to use it.**
- Adding a finding: append a row with status OPEN and no pin.
- Closing one: write the fix AND the pin, then move it to CLOSED. A row with no pin is not
  closed, however convinced anyone is — and `seal_check` will list it as having no runnable pin.
- `tests/ask/security/test_ledger.py` fails if a row names a corpus case that does not exist,
  if a corpus case has no ledger row, or if a CLOSED row has an empty pin — so the map cannot
  drift from the territory.
- Briefing the next review round: hand over this file. Anything it re-reports that is CLOSED
  here is answered by running `seal_check`, not by arguing.

Pin notation: `corpus:<case_id>` = an attack entry in `tests/ask/security/attack_corpus.py`
that must be REFUSED · `redacted:<case_id>` = a `REDACTED_STATEMENTS` entry that may RUN but
whose fabricated id must not reach the caller · `conformance` = a case in
`scripts/ask_loop/conformance.py` · otherwise a test module path.

**Outcome pins are not causal pins.** A `corpus:` pin proves the payload never reached the
caller. It does NOT prove that the mechanism named in "Closed by" is what stopped it — E10
below is exactly that failure: E8's case was refused by an earlier check, so the fix it pinned
could have been deleted with the seal still green. `scripts/ask_loop/defence_matrix.py` carries
the causal half: for each mechanism it names a case that MUST get through when that one
mechanism is disabled, and the build fails when a claim cannot be proven.

---

## CLOSED — generated-SQL hatch (tenant isolation, read-only plane)

| # | Finding | Round | Closed by | Pin |
|---|---|---|---|---|
| S1 | `set_config` callable from a generated SELECT rewrites the RLS scope | PF-FBP-8 | text guard + plan function allowlist | `corpus:plain-set-config` |
| S2 | Quoted identifier `"set_config"` hid the name from the token scan | fix-wave | three-view lexer: identifiers stay verbatim in the `code` view | `corpus:quoted-identifier-set-config` |
| S3 | `U&"\0073et_config"` — the SERVER decodes unicode escapes, the scan does not | R1 | `U&` syntax refused; plan review sees the canonical name | `corpus:unicode-escape-set-config` |
| S4 | `$` inside an identifier opened a phantom dollar quote, masking the rest | fix-wave | identifier runs consumed whole; unterminated literals rejected | `corpus:phantom-dollar-quote`, `corpus:second-statement-behind-phantom-quote` |
| S5 | Move-read-restore inside ONE statement defeats a before/after scope check | self-audit | plan review (the tripwire is documented as a tripwire, not a boundary) | `corpus:hijack-read-restore-scalar-subqueries`, `corpus:hijack-via-from-clause`, `corpus:hijack-via-cte` |
| S6 | `query_to_xml` executes SQL from a string argument | R1 | plan function allowlist | `corpus:query-to-xml-executes-a-string`, `corpus:query-to-xml-in-from-clause` |
| S7 | `database_to_xml` / `schema_to_xml` dump every reader-granted table | R2 | plan function allowlist | `corpus:database-to-xml-dump`, `corpus:schema-to-xml-dump`, `corpus:xmlschema-variant-dump` |
| S8 | `ts_rewrite` runs its second argument as SQL through SPI | R3 | **denylist replaced by an allowlist** of ~60 read-only builtins | `corpus:ts-rewrite-spi-executor` |
| S9 | The hatch reached all 16 reader-granted tables, not the documented 6 | R2 | EXPLAIN relation allowlist | `corpus:read-audit-log`, `corpus:read-connector-secrets`, `corpus:read-identity-bindings`, `corpus:smuggle-forbidden-table-in-a-subquery` |
| S10 | `lo_from_bytea` WRITES a large object from a plain SELECT | R2 (cross-vendor) | allowlist (no `lo_*`) | `corpus:large-object-write-from-a-select` |
| S11 | Session-level advisory locks outlive the savepoint that took them | R2 (cross-vendor) | allowlist | `corpus:session-advisory-lock` |
| S12 | A failed statement aborted the transaction, killing every later tool call | fix-wave (N2) | SAVEPOINT around every executor | `tests/ask/tools/test_registry_dispatch.py` |
| S13 | Search terms containing SQL keywords were rejected as code (~19 words) | fix-wave (N1) | literal-aware lexer; scans run on masked views | `corpus:literal-that-looks-like-a-forbidden-call`, `corpus:literal-with-denylisted-words` |
| S14 | `$$desc$$` / `'%desc%'` were rewritten INSIDE literals by the DESC fix | fix-wave (R7) | token-span rewrite instead of regex over text | `corpus:literal-with-desc-and-comment-markers`, `tests/ask/tools/test_sql_guard.py`, `tests/ask/tools/test_lexer_alignment.py` |
| S15 | The plane must stay SELECT-only and single-statement (PF-FBP-8 baseline) | PF-FBP-8 | text guard: SELECT/WITH prefix, no `;`, no SELECT INTO, fail-closed lexing. **Measured R5:** at runtime these four are held by EXPLAIN and by the reader role's privileges, not by the guard — the guard is the first filter and the error message, not the boundary | `corpus:write-attempt`, `corpus:multi-statement`, `corpus:select-into`, `corpus:unterminated-literal` |
| S17 | `has_limit` was set by a `limit` token ANYWHERE in the statement, so a LIMIT inside a subquery suppressed the appended cap while bounding nothing at the top level. There is no streaming, no `statement_timeout` and no server-side row bound, so SQLAlchemy buffers the ENTIRE result before `max_rows` slices 50 rows off it — a ~35M-row cartesian product in process memory on the dev corpus | R5 red team pass 4 | only a paren-depth-0 LIMIT suppresses the cap, plus a ceiling on the planner's own `Plan Rows` estimate as the general control | `tests/ask/tools/test_result_bounds.py` |
| S16 | The plane must not reach the filesystem or stall the connection | PF-FBP-8 | plan function allowlist (no `pg_read_file`, no `pg_sleep`) | `corpus:filesystem-read`, `corpus:resource-stall`, `corpus:forbidden-call-in-a-filter` |

## CLOSED — fabricated evidence

| # | Finding | Round | Closed by | Pin |
|---|---|---|---|---|
| E1 | A uuid passed as a tool ARGUMENT counted as tool evidence | fix-wave (N5) | grader reads `result_payload` only | `conformance` |
| E2 | `UnknownToolError` echoed the model-chosen tool name | R1 | name no longer echoed | `tests/ask/services/test_agent_runner.py` |
| E3 | `_parse_iso_date` echoed the rejected argument | R1 | value no longer echoed | `tests/ask/tools/test_sql_guard.py` (redaction), `tool_helpers.parse_id_arg` |
| E4 | `per_term_matches` keys echoed the model's own search terms | R1 | `redact_uuids` on the keys | `tests/ask/security/test_attack_corpus.py` |
| E5 | The redaction regex required word boundaries the grader's did not | R2 | one regex, identical to the extractor | `tests/ask/security/test_attack_corpus.py`, `tests/ask/tools/test_sql_guard.py` |
| E6 | A relationless `SELECT '<uuid>'` laundered a literal into `rows` | R2 | touches-a-relation rule. **R5:** once provenance was enforced at every plan depth, EVERY relationless statement that produces output became caller-authored and was refused by that rule instead — leaving touches-a-relation with nothing of its own. `relationless-count` is the exception that still isolates it: `SELECT count(*)` with no FROM is read as data (it IS a count) and answers `1`, a fabricated quantity, to a question about the archive | `corpus:relationless-literal-laundering`, `corpus:relationless-dump`, `corpus:relationless-prose-laundering`, `corpus:relationless-count` |
| E7 | A decorative `FROM` defeated that rule | R3 | no constant in the top-level plan output | `corpus:decorative-from-launders-a-literal-id` |
| E8 | A uuid-shaped column ALIAS became a row key (invisible in the plan) | R3 | result column names checked after execution | `corpus:uuid-shaped-column-alias`, `corpus:uuid-shaped-column-alias-over-a-real-table` |
| E9 | `concat()` is STABLE, so the planner never folds it: the plan shows harmless fragments and the canonical uuid exists only at runtime | R4 | provenance decided per VALUE — ids from computed expressions are redacted, ids read from columns survive | `redacted:concat-assembled-id`, `redacted:concat-ws-assembled-id` |
| E10 | E8's only pinned case was RELATIONLESS, so touches-a-relation refused it first and the alias check never executed — the fix could be deleted with its pin still green | R5 mechanism audit | a table-anchored alias case, plus a causal claim per mechanism in the defence matrix | `corpus:uuid-shaped-column-alias-over-a-real-table` |
| E11 | E6 had the same defect: its uuid-shaped case is ALSO caught by the literal-id output rule, so it proved nothing about the touches-a-relation rule | R5 mechanism audit | a relationless case with no id in it — fabricated evidence need not be uuid-shaped | `corpus:relationless-prose-laundering` |
| E12 | E9's second case used `replace`, which is IMMUTABLE: the planner folds it to a literal and the statement is refused before the redaction ever runs | R5 mechanism audit | `concat_ws` (measured STABLE, plan shows the unfolded call), and the matrix now proves each redaction case leaks with the redaction OFF | `redacted:concat-ws-assembled-id` |
| E13 | **Fabricated evidence does not have to be uuid-shaped.** The literal rule matched uuid SHAPES, so a sentence anchored to a real table — `SELECT 'Acme signed the renewal on 2024-03-01' AS finding, count(*) FROM email_message` — walked past every layer and arrived in `rows` verbatim, where the critic reads the tool payload as transcript-supported | R5 red-team | the rule is now about PROVENANCE, not shape: no output column may be a constant of any type | `corpus:table-anchored-prose-laundering` |
| E22 | `_ANY_IDENTIFIER` cannot tell a column name from a SQL KEYWORD, so `CASE WHEN (now() IS NOT NULL) THEN '<fabricated>' ELSE '' END` was classified as DATA on the strength of the word `CASE` — an expression that reads nothing, depends on no row, and returns the caller's sentence unconditionally | R5 red team pass 3 | a surviving identifier must be a KNOWN COLUMN of an allowlisted relation (built from the ORM models, not from the hand-written card), plus a caller-literal precondition so `now()` and bare `NULL` are not payloads | `corpus:keyword-only-case-expression`, `tests/ask/tools/test_provenance.py` |
| E20 | **The E14 and E15 fixes did not COMPOSE.** The depth scan matched only BARE constants; the provenance test ran only on the TOP output. A function-assembled constant behind a fence was in neither: the deep node shows `concat_ws(...)`, the top shows `fake.finding`. Measured escaping PAIRED WITH A REAL MESSAGE ID — the worst shape, since the citation passes fidelity while the fact beside it is invented. Same gap through an InitPlan, and with a NUMBER (the depth scan was text-only) | R5 pass 2 | one rule over the whole plan TREE: every node's output entry must read something from the corpus | `corpus:cte-fenced-function-assembled`, `corpus:cte-fenced-number`, `corpus:scalar-subquery-assembled-prose` |
| E19 | The `count` exemption asked whether an expression MENTIONED count, not whether it WAS one, so any authored payload bought immunity by carrying a count as decoration | R5 pass 2 | the exemption is structural — the whole entry must be the count | `corpus:count-decorated-payload` |
| E18 | The alias fix tested for WHITESPACE; an underscore or hyphen carries a sentence just as well | R5 pass 2 | names must be identifier-shaped AND free of digit runs of 3+ (the amount or date is the part that asserts something) | `corpus:underscore-phrase-column-alias` |
| E21 | Splitting the module CORRUPTED a regex literal — a `` became a literal backspace, so the call scan matched nothing, every expression was classified as data, and the entire anti-fabrication layer was silently off. Ruff passed | R5 self-audit | unit pins that assert the machinery, not only its verdicts (`test_provenance.py`), including one that the call scan matches a call at all | `tests/ask/tools/test_provenance.py` |
| E17 | The alias rule matched uuid SHAPES only, so the row-key channel still carried a fabricated FACT — `SELECT count(*) AS "Acme owes 42000 EUR"` | R5 follow-through on E13 | a result column name must be identifier-shaped; whitespace is refused | `corpus:phrase-shaped-column-alias` |
| E16 | The first E15 fix exempted every AGGREGATE from the provenance test, reasoning that an aggregate reads the rows. `string_agg('<fabricated>', ',')` reads nothing and returns the caller's sentence once per row — the exemption would have waved through the exact shape the fix was written to stop | R5 self-audit | only `count` is exempt (it reads every row while naming no column); an aggregate over a real column keeps that column's identifier and needs no exemption | `redacted:aggregate-over-a-constant` |
| E15 | **A function assembles a fabricated fact without ever writing a constant.** `concat_ws(' ', 'Acme', 'owes', '42000')` renders as a CALL, so no constant rule matches it, and the redaction only ever stripped uuid SHAPES — so a fabricated sentence reached `rows` intact even after E13/E14 | R5 (found by following E13's own logic) | provenance decided by whether the expression READS anything: literals, call names and cast types stripped, and if no identifier remains the value is the caller talking to itself and is replaced outright. Aggregates count as reading the corpus | `redacted:function-assembled-prose`, `corpus:relationless-function-assembled-prose` |
| E14 | **A constant parked one node down is invisible at the top.** Behind `MATERIALIZED` (an optimisation fence) the top output renders as the plain Var `fake.ev` — indistinguishable from a real column by any text rule. The literal check read only the top node, and the per-value redaction read a bare column name as column provenance and left it alone. Measured escaping with the fabricated id intact | R5 red-team | every node's output is scanned, not just the top | `corpus:cte-fenced-uuid-laundering`, `corpus:cte-fenced-prose-laundering` |

## CLOSED — per-person visibility

| # | Finding | Round | Closed by | Pin |
|---|---|---|---|---|
| V1 | `get_email` served BCC recipients to any grant holder | R2 (cross-vendor) | `kind <> 'bcc'` | `tests/ask/tools/test_read_tools_isolation.py` |
| V2 | Participant filters matched BCC rows — a membership oracle via counts | R2 | `kind <> 'bcc'` in both filter clauses | `tests/ask/tools/test_read_tools_isolation.py` |
| V3 | `find_person`'s seen-window LATERAL still matched BCC rows | R3 | `kind <> 'bcc'` (the fourth and last site) | `tests/ask/tools/test_person_and_isolation.py` |
| V4 | `find_person` served write-plane seen-window aggregates over ALL messages | R2 | window recomputed from readable messages | `tests/ask/tools/test_person_and_isolation.py` |
| V5 | The reader seam checked the role NAME only — a BYPASSRLS role passed | R2 (cross-vendor) | probe also returns `rolsuper OR rolbypassrls` | `tests/ask/tools/test_reader_seam.py` |
| V6 | No cross-tenant negatives for the id-addressed and document tools | fix-wave | four cross-tenant tests | `tests/ask/tools/test_read_tools_isolation.py` |
| V9 | **BCC recipients served verbatim through the generated-SQL hatch.** The `visibility` policy keys CHILD rows on the PARENT (`email_recipient.email_id`), so any grant holder reads EVERY recipient row of a message — deliberate at the policy level, compensated for in four hand-written tool queries. `email_recipient` is in the hatch's relation allowlist and nothing on that path filters `kind`. No injection needed: "who else was on that email?" produces `SELECT * FROM email_recipient WHERE email_id = …`. V1-V8 closed the four queries; S9 closed the hatch's table REACH but settled on an allowlist containing exactly the tables where the rule lives — the two hardening efforts never met | R5 isolation red team | **the rule moved into the DATABASE**: RESTRICTIVE policy `kind IN ('to','cc')` on `email_recipient` for the reader role (migration 0023). **Closed in code by migration 0023 — UNTRACKED in git and UNAPPLIED on the dev DB (`alembic_version = 0022_counterparty_summary_v3`). OPEN on any database at 0022: measured 2026-09-06 as 59 BCC rows, 5,893 enumerable acl_grant rows, 839 readable seen-window values, 10 BCC-only counterparty domains.** | `tests/ask/tools/test_read_tools_isolation.py`, `tests/ask/tools/test_sql_hatch_isolation.py` |
| V10 | `counterparty_summary` counts BCC rows — a precise membership ORACLE. Its BCC-inclusive `total_mentions` minus `count_emails`'s BCC-exclusive count is exactly the number of readable messages where a domain appears ONLY as BCC; `distinct_addresses` exceeding the visible to/cc addresses proves a hidden address exists | R5 isolation red team | closed by the same policy — `security_invoker = true` propagates it into the view. **Closed in code by migration 0023 — UNTRACKED in git and UNAPPLIED on the dev DB (`alembic_version = 0022_counterparty_summary_v3`). OPEN on any database at 0022: measured 2026-09-06 as 59 BCC rows, 5,893 enumerable acl_grant rows, 839 readable seen-window values, 10 BCC-only counterparty domains.** | `tests/access/test_counterparty_summary.py` |
| V11 | `acl_grant` was allowed on the retrieval plane as "grant bookkeeping, never content". It carries one row per recipient of EVERY kind, so joining it to `person`/`person_email` reconstructs the blind-copied recipient list BY NAME without touching `email_recipient` — and with org isolation alone it also enumerated message ids the caller holds no grant for, which colleagues are party to each, and each colleague's mailbox size | R5 isolation red team | RESTRICTIVE policy `person_id = current person` on `acl_grant` (0023); a no-op for the `visibility` policy, whose own EXISTS already constrains the same column. **Closed in code by migration 0023 — UNTRACKED in git and UNAPPLIED on the dev DB (`alembic_version = 0022_counterparty_summary_v3`). OPEN on any database at 0022: measured 2026-09-06 as 59 BCC rows, 5,893 enumerable acl_grant rows, 839 readable seen-window values, 10 BCC-only counterparty domains.** | `tests/ask/tools/test_sql_hatch_isolation.py` |
| V12 | `person.first_seen_at/last_seen_at` served through the hatch reopened BOTH V3 and V4 with one column: they are maintained on the WRITE plane over every ingested message, and `bcc` is absent from the ingest service's `_NON_PERSON_RECIPIENT_KINDS`, so a BCC-only contact gets a person row and a moving window — precisely the value `find_person`'s LATERAL excludes | R5 isolation red team | column-level `REVOKE SELECT (first_seen_at, last_seen_at) ON person FROM oneai_reader` (0023), and removed from the M-Schema card — a card is documentation, never a boundary. **Closed in code by migration 0023 — UNTRACKED in git and UNAPPLIED on the dev DB (`alembic_version = 0022_counterparty_summary_v3`). OPEN on any database at 0022: measured 2026-09-06 as 59 BCC rows, 5,893 enumerable acl_grant rows, 839 readable seen-window values, 10 BCC-only counterparty domains.** | `tests/ask/tools/test_sql_hatch_isolation.py` |
| V13 | The BCC rule was a DENYLIST over a five-value enum (`kind <> 'bcc'`), so `reply_to` and `sender` rows were served as recipients while `get_email`'s own description promised "to/cc" | R5 isolation red team | `kind IN ('to','cc')` at all four sites and in the policy | `tests/ask/tools/test_read_tools_isolation.py` |
| V14 | The M-Schema card described `email_recipient.kind` as "'to' or 'cc'" while the 0008 CHECK allows five values including `bcc`. Filed separately from V9 because it is WHY V9 survived four adversarial rounds: a reviewer reading the card concludes the hatch cannot see BCC | R5 isolation red team | card corrected; O6 (generate it from `information_schema`) is the durable fix | `tests/ask/tools/test_sql_hatch_isolation.py` |
| V7 | `org_isolation` itself was pinned only where a per-person policy ALSO held the row out — dropping it changed nothing | R3 armour audit | the person graph carries org isolation ALONE, so a cross-tenant `find_person` is where that policy stands unaided | `tests/ask/tools/test_person_and_isolation.py` |
| V8 | The BCC rule reached four query sites but only two had a test with a BCC row in the fixture | R3 armour audit | a BCC-only person (window must stay empty) and a `participants_all` oracle probe | `tests/ask/tools/test_person_and_isolation.py` |

## CLOSED — measurement integrity (the harness may not flatter, nor slander)

| # | Finding | Round | Closed by | Pin |
|---|---|---|---|---|
| M1 | Citation uuids leaked digit groups into count extraction | R2 | uuids stripped first, with the module's own regex | `conformance` |
| M2 | Any number in the headline sentence satisfied a count gold | R2 | binds to the FIRST number of the claim | `conformance` |
| M3 | `_match_entity` was a polarity-blind substring test | R2 | word-boundary matching | `conformance` |
| M4 | A truncated `results.jsonl` graded as a complete run | R2 | coverage reported; PARTIAL COVERAGE banner | `tests/ask/services/test_harness_integrity.py` |
| M5 | Invented citations only failed rows the grader had already passed | R2 | fabricated evidence fails in EVERY state | `conformance` |
| M6 | Results bound to gold by `qid` alone — an edited question re-pointed them | R2 | stored question text compared; `stale` verdict | `tests/ask/services/test_harness_integrity.py` |
| M7 | Cache key ignored corpus, endpoint, person, org, question text | R1/R2 | all folded into the basis | `tests/ask/services/test_cache_identity.py` |
| M8 | Infrastructure failures graded as model failures | R2 | `error` verdict, excluded from accuracy | `conformance`, `tests/ask/services/test_harness_integrity.py` |
| M9 | A prose date became the count — same answer passed or failed by format | R3 | prose dates stripped before extraction | `conformance` |
| M10 | Short gold names (`GBS`, `IBM`) were discarded by a length floor | R3 | short names match case-SENSITIVELY | `conformance` |
| M11 | A trailing honesty note disqualified a correct, fully-cited answer | R3 | the refusal guard is scoped to the claim sentence | `conformance` |
| M12 | A duplicate qid silently overwrote an authored gold | R3 | the grader aborts on duplicates | `tests/ask/services/test_harness_integrity.py` |
| M13 | Routing tokens were spent off-book on both routed arms | R3 | `usage_sink` threaded through | `tests/ask/services/test_router.py` |

## CLOSED — runtime robustness

| # | Finding | Round | Closed by | Pin |
|---|---|---|---|---|
| R1 | A blind 6k slice cut mid-JSON, dropping the fields the contract promised | fix-wave | structural, size-driven truncation | `tests/ask/services/test_agent_runner.py` |
| R2 | Trimming by key name emptied small complete lists to protect a huge string | fix-wave review | trimming by size contribution | `tests/ask/services/test_agent_runner.py` |
| R3 | One round charged a whole overshoot to one field, wiping a 50-row listing | R2 | the sample page is spent first; halving per round | `tests/ask/services/test_agent_runner.py` |
| R4 | A non-trimmable shape produced unparseable JSON | R3 | scalars kept; valid envelope always | `tests/ask/services/test_agent_runner.py` |
| R5 | A malformed id from the model aborted the whole question | R3 | `parse_id_arg` validates before the database | `tests/ask/services/test_agent_runner.py` |
| R6 | Already-parsed tool arguments raised an uncaught `TypeError` | R3 | both shapes accepted | `tests/ask/services/test_agent_runner.py` |
| R7 | Router kits named tools that no longer existed; 4 of 6 silently shrank | fix-wave (N12) | `ToolRegistry.subset` refuses unknown names | `tests/ask/services/test_router.py` |
| R8 | Classification matched a class name inside a longer token | R3 | word-bounded, last-mention | `tests/ask/services/test_router.py` |

---


## CLOSED — the WRITE plane (grant derivation, R5)

The failure mode here is not disclosure but **AUTHORING**: an unauthenticated party decides who
holds a grant. `principal_source_identity` authenticates the PERSON; nothing authenticates the
CLAIM that the person was on the message, and `headers.py` drops
`DKIM-Signature`/`Authentication-Results`/`ARC-*` at parse under CA-CONN-05 minimisation, so the
evidence that could validate such a claim is discarded before grant derivation runs.

**The fix is one rule, and it closed more than the two-part shape predicted: grants derive ONLY
from fields that are IN THE DEDUP KEY.** Two copies of one message therefore derive identical
grants by construction, which makes the whole ordering class impossible rather than patched.

| # | Finding | Round | Closed by | Pin |
|---|---|---|---|---|
| W1 | A literal `Bcc:` on delivered mail is NOT stripped by the RECEIVING MTA, so mailing any synced mailbox with `Bcc: victim@corp.com` placed attacker-authored text inside the victim's PRIVATE retrieval scope — and the victim never receives the message, so there is no inbox in which to notice | R5 write-plane | `DISCLOSED_RECIPIENT_KINDS = {to, cc}`: bcc/reply_to/sender never mint a grant. A genuinely blind-copied person still reaches their own copy, where they are the connection OWNER | `tests/access/test_grant_writer.py` (bcc-never-grants + backfill-agrees-with-ingest) |
| W2 | **The dedup key's recipient set was a strict SUBSET of the grant-derivation set** — keyed on to/cc, derived from all five. A dedup hit reconciles against the NEW parse and TOMBSTONES `live - derivable`, and SENT shares the dedup scope with INBOX, so **whichever copy was ingested second silently revoked the other's grants. Ingest order decided who could read the message — no attacker required** | R5 write-plane | the same rule: every grant source (owner, `From`, to/cc) is now a KEYED field, so two copies cannot disagree | `tests/access/test_grant_writer.py::test_ingest_order_does_not_decide_who_can_read_the_message` |
| W4 | `display_name` is first-writer-wins and a RECIPIENT display name is chosen by the SENDER about somebody else, so `To: "Chief Fraud Officer" <cfo@corp.com>` named that person permanently and `find_person` reported it | R5 write-plane | a recipient's address resolves them; the sender-supplied name for a THIRD PARTY is dropped. Naming oneself in `From:` stays allowed | `tests/connectors/imap/services/test_email_ingest_service.py` |

**Causally verified, not merely green:** with the kind filter temporarily reverted, both W1/W2
pins FAIL — the ordering test failing exactly as predicted — and pass with it restored.

## OPEN — accepted, deferred, or a founder decision

**W-series = the WRITE plane** (grant derivation + entity resolution), opened by the R5 write-plane red team. Its failure mode is not disclosure but AUTHORING: an unauthenticated party decides who holds a grant. `principal_source_identity` authenticates the PERSON; nothing authenticates the CLAIM that the person was on the message — and `headers.py` drops `Authentication-Results`/`DKIM-Signature`/`ARC-*` at parse under CA-CONN-05 data minimisation, so the evidence that could validate such a claim is discarded before grant derivation runs. A defensible privacy call with an unintended consequence.

**What holds:** `get_verified_person_ids` filters `verified.is_(True)`, so an arbitrary attacker address NEVER becomes a principal. Every W-finding can only grant to someone who already holds a verified binding — a real employee. The attacker chooses WHICH employee; they cannot mint themselves a principal, grant to an outsider, or reach another org.

| # | Item | Why it is open | Where |
|---|---|---|---|
| O1 | The org contact graph (person, person_email, company…) is org-visible, not per-person | PF-01 design scope: is the counterparty graph a company-level asset? A product decision, not a bug to patch inside a security pass | `FIX_BEFORE_PROD.md` |
| O2 | `same_person_candidates` infers identity from spoofable headers | Needs the HITL / verified-identity tier | `PF-FBP-10` |
| O3 | **Three separate rules now depend on how the planner RENDERS expressions** — the call allowlist (`name(`), the constant rules, and the provenance test (strip literals/call names/casts, then look for a surviving identifier) | A PostgreSQL major upgrade must re-run the defence matrix BEFORE the hatch serves users. This is the single largest structural dependency in the design, and it grew in R5 | `PF-FBP-8` |
| O15 | `compute_dedup_key` hashes `safe_header(message, 'From')` — the FIRST From header — while `_first_address` iterates `get_all('from')`. With two From headers where the first carries no addr-spec, the key covers header #1 and the derived sender comes from header #2 | A dedup-key SOUNDNESS gap (two distinct senders could share a key), NOT a surviving ordering bug: no MTA adds a second From header, so two copies of one message stay identical and derive identical grants. Recorded so the invariant written down is the true one | R5 write-plane verification |
| O16 | `connector_connection.owner_user_id` is MUTABLE and the backfill script writes it. Changing a connection's owner makes the next reconcile tombstone the old owner's grant | Correct behaviour ("the owner changed", not ingest order) — but it means owner-stability rests on an operational assumption rather than on the dedup key itself | R5 write-plane verification |
| O17 | Dropping sender-supplied names for THIRD PARTIES leaves permanently unnamed anyone who appears only as to/cc and never as a `From` sender — external counterparties who receive but never reply, colleagues who are cc'd but never send. `person_alias` now accumulates only self-declared spellings, so name-variant lookup narrows | The accepted cost of the W4 integrity property, stated so it stays a decision rather than becoming a discovery | R5 write-plane verification |
| O18 | **Indirect prompt injection is not addressed anywhere.** It appears in neither the ledger, `FIX_BEFORE_PROD.md`, nor `app/ask/` — while `.claude/rules/security.md` requires "Prompt injection defense in Agent Runtime". Email content is attacker-authored by definition and reaches the model as observation text | Today's ceiling is answer INTEGRITY and tool-use steering: the role is read-only, RLS holds, there is no write path, no scope widening and no cross-tenant read — which is why this is an OPEN row and not a fix round. **The sharp version is MCP-01's write tools** (`record_fact`, `record_session_summary`, `flag_impact`), where injected text steering a write becomes PERSISTENCE INTO COMPANY MEMORY. Delimiting observation content is a mitigation, not a boundary, and CLAUDE.md's own MCP-01 standard is "by construction, never prompt-level trust" | R5 closing sweep |
| O19 | `agent_runner`'s loop bounds TURNS, not WORK: `ask_max_tool_turns = 8` caps model turns, but a single turn may carry unbounded tool calls, each a DB round-trip plus (with `query_database` registered) a local 7B generation, run sequentially. Fan-out is capped only by `max_tokens`, so ~10² executions per question rather than 8 — and the docstring claims "BOUNDED … mirrors the production posture" | LOW **only because of reachability**: no FastAPI route imports `app.ask` at all. It becomes MEDIUM the moment a route or the MCP server wires it up, so it is an MCP-01 gate. Fix: a per-run tool-call budget beside the turn cap | R5 closing sweep |
| O20 | The error-observation channel is per-site discipline rather than an invariant: `agent_runner` builds `{"error": str(exc)}` without passing it through `redact_uuids`. Every interpolating raise site that can reach it was enumerated and **no live instance exists** — E2/E3/E4 were three per-site fixes of one class | Structural value only: a chokepoint would make it an invariant a NEW tool cannot violate. Note it closes only the uuid half — E13 established that fabricated evidence need not be uuid-shaped — so it must not be booked as a class closure | R5 closing sweep |
| O21 | Cosmetics recorded so they are not re-found: `_COUNTERPARTY_SUMMARY_COLUMNS` is a second hand-written schema copy (the O6 drift class, fails SAFE by over-refusing); `_KNOWN_COLUMNS` is computed at import, defeating the lazy-import rationale; `varying` is allowlisted because `character varying(255)` renders as a call, but `numeric(10,2)`/`timestamp(0)`/`char(n)` are not, so a legitimate money cast is refused (fails closed); the `LIMIT` append turns a valid `FETCH FIRST n ROWS ONLY` into a syntax error (fails closed) | All fail in the safe direction | R5 closing sweep |
| O13 | **The residual header-trust model.** `to`/`cc`/`From` are forgeable and still mint grants, so an attacker who can mail a synced mailbox can cause a VERIFIED employee to hold a grant on a message the attacker composed. This is inherent to "ingest email and grant access based on who is on it" — every mail system works this way. The sharp variant (Bcc: invisible to the victim AND unkeyed) is CLOSED; what remains names the victim IN THE OPEN, so an auditor reading the message sees them. Closing it entirely needs either DKIM/ARC verification (reversing CA-CONN-05 minimisation) or a possession-only access model, in which an employee reads only mail in their OWN synced mailbox — a material capability change | Founder decision, and the one to take BEFORE MCP-01 adds a second authoring path | R5 write-plane |
| O14 | A spoofed `From:` mints and UPGRADES a `'sender'` grant (`_PROVENANCE_RANK` ranks sender above recipient). Nothing authorizes on `provenance` today — but it is exactly the field a curation or Learn tier reads as "this person wrote this" | **Pin it BEFORE MEM-01 consumes it**, or a forged `From:` is baked into derived facts rather than sitting on one row. `provenance` is header-derived and must never be treated as authenticated authorship | R5 write-plane (W3) |
| O11 | `_bind_scope` builds its `set_config` call by STRING-INTERPOLATING `person_id` as a literal (`core/database.py`). Not a break today — both live callers pass a UUID read from the database and the signature types it `UUID \| None` — but a type hint is not enforcement | This is a scope-binder injection the moment an UNTRUSTED HOST AGENT supplies the id, which is exactly the MCP-01 shape. Belongs with the MCP port, not filed as "not a break today". Minimum: assert `UUID(...)` before that line | R5 isolation red team, near-miss |
| O12 | The generated-SQL hatch cannot distinguish a relation a POLICY expands into from one the caller wrote. `_plan_relations` flattens the whole tree, so `acl_grant` had to be allowlisted because the `visibility` policy's own scan names it in every legitimate plan | Closed for now at RLS instead (0023's `own_grants`). The structural fix — count only main-plan-tree relations — would let `acl_grant` leave the allowlist entirely and is the better long-term shape | R5 isolation red team |
| O9 | **Any mention of a known column** anywhere in an output expression grants immunity from the provenance test — the rule is `not any(name in _KNOWN_COLUMNS for name in surviving)`, so it is broader than "a dead branch". `substr('<fabricated>', 1, 100 + 0*length(subject))` is not a dead branch at all; it is arithmetic that provably contributes zero, and `subject` survives the strip. Stated this way so a future reviewer cannot "close" O9 with a dead-branch-specific fix that misses the shape. The original examples still hold: `coalesce(nullif(subject, subject), '<fabricated>')` always returns the literal, and `CASE WHEN subject IS NOT NULL THEN '<x>' ELSE '<x>' END` decides nothing — both keep `subject` as a surviving identifier, so both read as data | The invariant that WOULD close it is "a string literal may appear in a predicate but never in a value-producing output expression". That kills `coalesce(subject, '(none)')` (a pinned ALLOWED case) and CASE-label bucketing — both legitimate analytical answers. This is a CAPABILITY TRADE for the founder, not a fix to take unilaterally inside a security pass | R5 red-team pass 2 |
| O10 | Column NAMES cannot carry database provenance at all. A plausible label on a REAL number (`AS emails_from_acme_about_the_renewal`) is caller text with no fabricated payload to detect, so no rule over the name can catch it | The durable fix is CONSUMER-side: the grader and critic must not read row KEYS as evidence. The name rule (E17/E18) closes only the shapes that carry a payload | R5 red-team pass 2 |
| O7 | `_plan_relations` compares BARE relation names, ignoring the schema | Not exploitable today: `oneai_reader` is SELECT-only, so it cannot create a table, view or temp object named `person`/`email_message` in another schema. It becomes exploitable the moment any migration grants the reader CREATE or TEMP — so that grant is the thing to guard, not the check | R5 red-team §3 |
| O4 | The 50-row `listing_complete` contract exceeds the 6k observation budget | Contract and budget disagree; needs paging or a smaller promise | `ASK-FBP-5` |
| O5 | Attachment "is this a document" predicate is defined in three places | `is_inline` excludes 0.3% on the real corpus; needs one shared predicate | `ASK-FBP-2` |
| O6 | The SQL schema card is hand-written | Must be generated from `information_schema` | `ASK-FBP-1` |
