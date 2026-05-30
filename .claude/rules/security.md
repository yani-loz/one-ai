# Security Standards

## Tenant Isolation (4 Layers) — HARDEST RULE

1. **PostgreSQL:** `org_id` on every table with NOT NULL constraint
2. **Row-Level Security (RLS):** policies defined (enforced in production, defined in prototype)
3. **Application-level:** every query scoped to tenant context
4. **API Gateway:** tenant context from JWT — no switching

**No code path exists that queries without tenant scope. Non-negotiable.**

## Credential Management

- All secrets via `.env` (local) or Docker secrets (production). NEVER in code.
- `.env` in `.gitignore`. `.env.example` committed with placeholders.
- Secrets NEVER in logs, error messages, or docker inspect.
- Each service accesses only the secrets it needs.

## Authentication (JWT) — Phase 4+

- Self-hosted JWT via Platform Service
- Payload: `user_id`, `tenant_id`, `roles`, `expiration`
- Access token: 15 min. Refresh token: 7 days.
- JWT signing key rotatable.
- Bcrypt for password hashing.

## LLM Data Privacy

- No tenant data sent to LLM providers in a way that allows training.
- Use zero data retention endpoints where available.
- NON-NEGOTIABLE.

## Input/Output Security

- Input validation on all endpoints (Pydantic).
- Prompt injection defense in Agent Runtime.
- Output validation: prevent unauthorized data exposure, cross-tenant leakage.
- Sensitive data classification and PII detection.

## Audit Trail

- `audit_log` table is append-only (no UPDATE/DELETE).
- Logs: who, what, when, which entity, details (JSONB), IP address.

## Encryption

- At rest: AES-256 (PostgreSQL encryption)
- In transit: TLS 1.3 (production deployment)
