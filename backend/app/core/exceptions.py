"""
Role: Project-wide custom exception hierarchy (rule A5 — no bare `Exception`).
Used by: every module that raises a domain error (tenancy, database, services).
Depends on: nothing internal — leaf module.
Key invariants:
  - All domain errors subclass OneAIError.
  - Exception names describe the failure, e.g. TenantContextMissingError.
"""

from __future__ import annotations


class OneAIError(Exception):
    """Base class for all One AI domain errors."""


class TenantContextMissingError(OneAIError):
    """Raised when a tenant-scoped operation runs without an org_id in context.

    Guardrail for the hardest security rule: no query may execute outside a
    tenant scope. In production a missing/unresolvable tenant is a hard failure.
    """


class InsecureConfigurationError(OneAIError):
    """Raised at startup when production runs with a known-insecure default secret.

    Fail-closed guard: the app refuses to boot in production while a dev-default
    secret (the JWT signing key or the database password) is still in place, rather
    than silently signing tokens with a publicly-known, forgeable key.
    """
