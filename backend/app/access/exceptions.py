"""
Role: Access-module exceptions — typed failures for the promotion path (rule A5: custom
      exceptions only, never bare Exception).
Used by: app.access.services.promotion_service, app.access.routes (mapped to HTTP statuses).
Depends on: stdlib only.
Key invariants:
  - ContentObjectNotFoundError serves BOTH "does not exist" and "not yours" — a cross-tenant or
    unauthorized probe gets the same 404, never a 200-with-empty or an existence-leaking 403.
"""

from __future__ import annotations


class ContentObjectNotFoundError(Exception):
    """The content object does not exist in this org (or the caller may not see it)."""


class PromotionNotAllowedError(Exception):
    """The caller is not the owner of the content's source connection (owners widen, not admins)."""
