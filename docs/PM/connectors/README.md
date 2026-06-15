# Connectors (`CO`) — PM epics

The customer data-ingestion plane (Connect). Connectors pull a company's data into unified memory;
this folder tracks the **product/PM epics** over that plane (the engineering design + traceability),
parallel to `company-admin/` and `platform-console/`.

| Epic | Title | Status |
|---|---|---|
| [CO-01](EPIC-CO-01-connector-authorization.md) | Connector Authorization: Entitlement → Governance → Self-Connect | 📝 Planned / spec |

## The connector data plane today (built, separately)

The IMAP connector itself — connection model, encrypted credentials, incremental fetch/sync, parsing,
content-identity dedup, attachment extraction, entity resolution, erasure hooks, SSRF egress guard,
secrets masking — is **built and data-quality-verified** (see `docs/FIX_BEFORE_PROD.md` CA-CONN-* and
the `docs/audits/` IMAP data-quality reports). CO-01 is the **authorization model** layered over it.

## Authorization model (CO-01) in one line

**Platform admin** unlocks connector *types* per a company's paid plan → **company admin** enables them
**org-wide and per-user** (health-only visibility, never content) → **every user** self-connects *their
own* mailbox (OAuth-first, consent, owns + can erase). Nobody ever types another person's credentials;
§7 ("admins see knowledge, not conversations") holds throughout.
