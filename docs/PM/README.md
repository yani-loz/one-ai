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
    README.md               ← module epic index + roadmap/status
    EPIC-<code>-NN-*.md      ← one epic per significant body of work (≈ one PR/feature)
```

- **Module** = a coherent product area (e.g. `platform-console`, and later `connect`,
  `ask`, `learn`). Each gets a short code used to prefix epic + requirement ids.
- **Epic** = a shippable body of work, usually one PR. Filename
  `EPIC-<code>-NN-<slug>.md` (e.g. `EPIC-PC-01-super-admin-console.md`).

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
