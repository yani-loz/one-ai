"""
Role: Route-level HTTP helpers for the auth endpoints — client-IP extraction for the login
      throttle (N-01), the httpOnly refresh-token COOKIE set/clear/read (Control C), and the
      first-party-request CSRF guard for the cookie-authenticated state-changers. Keeps
      cookie-attribute policy in ONE place so login, refresh, and logout can never drift apart.
Used by: routes.auth_routes (login/refresh/logout) and routes.platform_routes (client-IP only).
Depends on: app.core.config (cookie attrs / CORS origins), app.identity.exceptions
            (CrossSiteRequestRejectedError), starlette Request/Response. Leaf otherwise.
Key invariants:
  - The refresh cookie is ALWAYS httpOnly (never readable by JS — the whole point vs the
    prior localStorage posture). Secure + SameSite + name + path come from config so prod
    ships hardened and tests can assert the attrs; the dev default is non-Secure + lax
    (dev http can't satisfy SameSite=None+Secure for the cross-origin :5173->:8000 SPA —
    see config; prod behind one origin / a proxy is where it fully works).
  - clear is the exact inverse of set (same name/path/samesite/secure) so the browser
    actually drops the cookie — a mismatched attribute would orphan it.
  - client_ip_from_request reads request.client.host ONLY (X-Forwarded-For is NOT trusted
    here — a spoofed header could poison the throttle; behind a trusted proxy, wire XFF via
    a trusted-hops parser as a production step, mirroring core.request_context).
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings
from app.identity.exceptions import CrossSiteRequestRejectedError

# Sec-Fetch-Site values that mean the request is first-party (browser-attested). 'none' is a
# user-initiated request with no initiator (address bar / bookmark) — never a cross-site forgery.
_FIRST_PARTY_FETCH_SITES = frozenset({"same-origin", "same-site", "none"})


def require_first_party_request(request: Request) -> None:
    """Reject a cross-site BROWSER request to a cookie-authenticated state-changing endpoint (CSRF).

    Wired on /auth/refresh + /auth/logout, which authenticate via the ambient httpOnly cookie
    ALONE. When that cookie is SameSite=None (the documented cross-origin posture), SameSite no
    longer blocks a cross-site POST, so a malicious page could force a logout or rotate the
    victim's refresh token. This check does NOT depend on SameSite:
      - trust the browser's `Sec-Fetch-Site` when present (allow same-origin / same-site / none);
      - else fall back to an `Origin` allowlist (allow only configured CORS origins);
      - a request with NO browser-set Origin (a non-browser API client / server-side caller) cannot
        be a forged cross-site browser request, so it is allowed (the body-fallback's audience).
    Raises CrossSiteRequestRejectedError (403) for a cross-site or disallowed-Origin request,
    BEFORE any revoke/rotate runs (fail-closed).
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        if fetch_site.strip().lower() in _FIRST_PARTY_FETCH_SITES:
            return
        # Explicit cross-site (or an unknown value) — fall through to the Origin allowlist so a
        # legitimately allowlisted cross-origin SPA (Sec-Fetch-Site: cross-site) still works.
    origin = request.headers.get("origin")
    if origin is None:
        return  # no browser Origin -> not a forgeable cross-site browser request
    if origin in set(get_settings().cors_origins):
        return  # allowlisted cross-origin SPA
    raise CrossSiteRequestRejectedError("Cross-site request to a cookie-authenticated endpoint.")


def client_ip_from_request(request: Request) -> str | None:
    """Return the connecting client's IP (request.client.host), or None if unavailable.

    Used as the per-IP throttle key. X-Forwarded-For is deliberately ignored (untrusted);
    None collapses to the throttle's shared 'unknown' bucket rather than bypassing it.
    """
    return request.client.host if request.client is not None else None


def set_refresh_cookie(response: Response, raw_refresh_token: str) -> None:
    """Set the httpOnly refresh-token cookie on `response` with the configured attributes.

    Contract: writes `raw_refresh_token` as an httpOnly cookie named per config, with the
    configured Secure / SameSite / Path and a Max-Age equal to the refresh-token TTL. The
    token is never returned in the JSON body once this is used (Control C).
    """
    settings = get_settings()
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=raw_refresh_token,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=_normalized_samesite(settings.refresh_cookie_samesite),
        path=settings.refresh_cookie_path,
    )


def clear_refresh_cookie(response: Response) -> None:
    """Delete the refresh cookie (logout). Mirrors set_refresh_cookie's name/path/attrs.

    The delete MUST echo the same path + samesite + secure used on set, or the browser
    keeps the original cookie (attribute mismatch). Done via an explicit expired set_cookie
    rather than delete_cookie so SameSite/Secure are included on the clearing header too.
    """
    settings = get_settings()
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value="",
        max_age=0,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=_normalized_samesite(settings.refresh_cookie_samesite),
        path=settings.refresh_cookie_path,
    )


def read_refresh_cookie(request: Request) -> str | None:
    """Return the refresh token from the configured cookie, or None if absent."""
    return request.cookies.get(get_settings().refresh_cookie_name)


def _normalized_samesite(value: str) -> str | None:
    """Normalize the configured SameSite to Starlette's accepted literals.

    Starlette accepts 'lax' | 'strict' | 'none' (lowercase) or None. An unrecognized config
    value falls back to 'lax' — the safe default — rather than emitting an invalid attribute.
    """
    normalized = value.strip().lower()
    if normalized in {"lax", "strict", "none"}:
        return normalized
    return "lax"
