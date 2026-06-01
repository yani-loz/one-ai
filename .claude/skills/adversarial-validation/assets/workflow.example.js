// Multi-agent adversarial-validation workflow — SKELETON. Adapt the suites + case lists
// to the target, then pass via the Workflow tool's `script`. Each suite-agent authors +
// executes + records its case files in its OWN run-stamped data namespace against the
// live stack, and returns a structured index. Keep timing-sensitive concurrency races in
// a dedicated agent. Only invoke Workflow when the user has opted into multi-agent runs.
//
// PREREQUISITE: before launching, build + VALIDATE testing/<NN>_<target>/harness/_common.py
// end-to-end yourself (login, provision, forge, one probe) so every agent inherits proven
// plumbing. Author testing/README.md + TEMPLATE.md too. Then synthesize the dashboard +
// audit doc yourself AFTER the workflow returns (and re-verify headline NEW findings).
//
// RESILIENCE — the StructuredOutput gate is FRAGILE (observed twice: Target-02 TOKEN suite,
// PC-03a phase-1). A schema-forced agent can do ALL its work (author TC-*.md, run harnesses,
// record results to disk) yet finish WITHOUT calling StructuredOutput, which throws and — inside
// a parallel() — can abort the whole run (later phases never start). DESIGN FOR THIS:
//   1) The durable source of truth is the on-disk TC-<TT>-<NNN>_*.md files, NOT the structured
//      return value. Agents write results to disk as they go, so the work survives the throw.
//   2) On failure, DO NOT blindly re-run the flaky fan-out — Glob testing/<NN>/, harvest the
//      recorded results from the .md files, and finish + re-verify the missing/unrecorded cases
//      yourself with the proven harness (a lead "_finish_*.py" script). That is faster and avoids
//      re-suspending orgs / re-polluting the shared DB.
//   3) Re-derive any HEADLINE result that came from an abnormally-ended run first-hand before you
//      ship it — agent output from an aborted run is exactly what must be independently confirmed.
//   4) Keep schema OPTIONAL-friendly where you can (smaller suites, fewer required fields); the
//      agents still produce the real artifact (the .md files) regardless of the structured echo.

export const meta = {
  name: 'adversarial-validation',
  description: 'Author + execute + record adversarial test cases against the live stack for one target',
  phases: [{ title: 'Execute suites', detail: 'suite-agents: author TC files, run live harness, write results' }],
}

const TARGET_DIR = 'testing/01_infrastructure-authn-authz'  // ADAPT

const SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    suite: { type: 'string' },
    namespace: { type: 'string', description: 'the run-stamp / prefix this agent used' },
    cases: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          id: { type: 'string' }, title: { type: 'string' }, type: { type: 'string' },
          severity_if_fail: { type: 'string' },
          result: { type: 'string', enum: ['PASS', 'FAIL', 'PASS_WITH_CONCERN'] },
          tag: { type: 'string', enum: ['NEW', 'CONFIRMS_FIXED', 'REFUTES_FIX', 'CONFIRMS_DOCUMENTED', 'NA'] },
          file: { type: 'string' }, verdict: { type: 'string' }, evidence_snippet: { type: 'string' },
        },
        required: ['id', 'title', 'result', 'tag', 'file', 'verdict'],
      },
    },
    notes: { type: 'string' },
  },
  required: ['suite', 'cases'],
}

const SHARED = `You are an adversarial QA validator. Break the running code and document it rigorously — dynamic testing against the LIVE stack (real uvicorn at http://localhost:8000), not static review.

FIRST read: testing/README.md (strategy, legend, tags), testing/TEMPLATE.md (case format), ${TARGET_DIR}/harness/_common.py (shared harness — study every helper).

FOR EACH assigned case: (1) AUTHOR ${TARGET_DIR}/TC-<TT>-<NNN>_<slug>.md from TEMPLATE.md (top half). (2) BUILD a standalone ${TARGET_DIR}/harness/tc_<NNN>.py = FULL VERBATIM _common.py contents at top + your async def main() + asyncio.run(main()) (scripts run over stdin, no imports). (3) RUN from repo root: docker compose exec -T backend python - < ${TARGET_DIR}/harness/tc_<NNN>.py and capture real stdout. (4) RECORD the Execution result block back into the .md — raw evidence (codes+bodies), Result (PASS=defense held / FAIL=contract violated = a defect = the win / PASS_WITH_CONCERN), finding tag, verdict with file:line.

TAGS (evidence-based, read docs/audits/* + docs/FIX_BEFORE_PROD.md): NEW, CONFIRMS_FIXED, REFUTES_FIX (a claimed fix broke — escalate), CONFIRMS_DOCUMENTED (known deferral — prove once), NA.

HARD RULES (violating these corrupts other agents' runs):
- NAMESPACE: prefix EVERY org slug + email with your suite code + stamp(); onboard your OWN fresh orgs; NEVER mutate the demo org/admin.
- NEVER write under backend/ or frontend/ — only under testing/ (a write under backend/ triggers a uvicorn --reload that drops live connections).
- ONLY touch your own TC-<TT>-<NNN>_*.md and harness/tc_<NNN>.py. Do NOT edit README.md/TEMPLATE.md/_common.py/audit docs/other suites' files.
- Hit the REAL server through the harness. If a harness errors, fix YOUR tc_<NNN>.py and rerun — never fabricate evidence.
- Concurrency cases: >=50 iterations, fresh org per iteration, report RAW COUNTS; psql ground-truth via: docker compose exec -T db psql -U oneai -d oneai -c "<SQL>".

KEY FACTS: tenant key is org_id; RLS is DEFINED BUT INERT (superuser bypass) so the app-layer org_id filter is the only active control; the dev JWT secret is the forgeable default (in _common.DEV_SECRET) so forged tokens are a real capability. Return the structured index of every case executed.`

function suite(code, title, body) {
  return agent(`${SHARED}\n\n=== YOUR SUITE: ${title} (suite code "${code}") ===\n${body}`,
    { label: `suite:${code}`, phase: 'Execute suites', schema: SCHEMA, agentType: 'general-purpose' })
}

phase('Execute suites')

// ADAPT: one suite() per sub-area; list each case with id, type, expected, break hint, expected tag.
const results = await parallel([
  () => suite('INFRA', 'Infrastructure', `- TC-..-001 health → 200 ...\n- TC-..-002 oversized body → 413 ...\n- ...`),
  () => suite('AUTHN', 'Authentication', `- ...login valid / wrong-pw generic / no-enumeration / inactive / overlong-pw-no-500...`),
  () => suite('AUTHZ', 'Authorization + token validation', `- ...role gate 403 / audience split / alg=none / tampered / expired / missing-claims / malformed-no-500...`),
  () => suite('TENANT', 'Cross-tenant isolation', `- ...PATCH/DELETE→404 / list own-org-only / org_id not smuggle-able / email oracle / forged-token read+write / forged-role escalation...`),
  () => suite('TOKEN', 'Token lifecycle', `- ...rotation single-use / logout idempotent / refresh-after-logout / deactivated tokens / demoted-admin-keeps-power...`),
  () => suite('RACE', 'Concurrency races (>=50 iterations each)', `- ...non-atomic guards (last-admin DELETE+DELETE / PATCH×2 / mixed) / rotation race / dup-create race / onboarding rollback...`),
  () => suite('IV', 'Input validation + fuzz', `- ...byte vs char password limits / role-enum escalation / injection stored-literally / email canonicalization (NEW?) / control-NUL chars → 422 not 500...`),
])

return { suites: results.filter(Boolean) }
