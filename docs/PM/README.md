# Project Management (PM)

This folder is the **product-management layer**: epics, traceability, and test plans that
sit *above* the code and the engineering audits. It exists so anyone — PM, QA, a new
engineer, or a future Claude session — can answer three questions without reading the
diff:

1. **Why** does this work exist (goal, scope, the stories it satisfies)?
2. **Where** is it (which endpoints / files implement which requirement)?
3. **How do we know it works** (which test proves which acceptance criterion)?

## Structure

```
docs/PM/
  README.md                 ← this file (the convention)
  <module>/                 ← one folder per product module
    README.md               ← module epic index + roadmap/status (epic folders)
    EPIC-<code>-NN-*.md      ← one epic per significant body of work (≈ one PR/feature)
```

- **Module** = a coherent product area. Each gets a short code used to prefix epic +
  requirement ids. As of 2026-09-06 seven module folders exist: `company-admin` (CA),
  `connectors` (CO), `permission-fidelity` (PF), `platform-console` (PC), `ask` (ASK),
  `mcp` (MCP) and `memory` (MEM).
- **Epic** = a shippable body of work, usually one PR. Filename
  `EPIC-<code>-NN-<slug>.md` (e.g. `EPIC-PC-01-super-admin-console.md`).

> **Which folders the convention binds (as of 2026-09-06).** The `EPIC-<code>-NN-<slug>.md`
> filename scheme and the per-module `README.md` apply to the four **epic folders** —
> `company-admin/`, `connectors/`, `platform-console/` and `permission-fidelity/`
> (`permission-fidelity/` has no README yet; the other three do). `ask/`, `mcp/` and
> `memory/` hold **design and analysis documents rather than epics**: they are named under
> their own document ids (`ASK-NN-*`, `MCP-NN-*`, `MEM-NN-*`), plus a few topical files
> (`ask/ASK-SECURITY-LEDGER.md`, `ask/intent-classes.md`), and are **exempt** from the epic
> filename scheme and from the README requirement. `memory/` additionally nests
> `MEM-01/` as a document set rather than holding flat files.

## ID scheme (for traceability)

| Kind | Pattern | Example |
|---|---|---|
| Epic | `<code>-NN` | `PC-01` |
| User story | `<code>-NN-Sn` | `PC-01-S4` |
| Acceptance criterion | `<code>-NN-ACn` | `PC-01-AC4` |

Each acceptance criterion links to the **automated test(s)** that prove it (by file +
`test_name`) and, where relevant, to a **manual QA step**. A criterion with no test is a
gap and must be called out as one.

## Relationship to other docs

- **`docs/audits/`** — engineering review records (adversarial code review, findings +
  fixes). An epic *links to* its audit; it does not duplicate it.
- **`docs/FIX_BEFORE_PROD.md`** — the forward checklist of things that must change before
  production. Epics reference the FIX items they close or depend on.
- **`docs/Project_Bible.md`** — the product/architecture source of truth. Epics implement
  slices of it; they don't restate it.

## Conventions

- Status values: `Planned` · `In progress` · `Done (pending commit)` · `Done` · `Blocked`.
- Keep epics **current**: when behavior changes, update the epic in the same PR.
- Dates are absolute (ISO `YYYY-MM-DD`), never "last week".
