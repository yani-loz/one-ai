"""
Role: SSRF egress validation for the IMAP connector's two server-initiated dial paths (the
      connection-test verify path in client.py and the real fetch connect in fetch_session.py).
      Both make the SERVER open a socket to a company_admin-supplied host:port, so an authenticated
      tenant admin could otherwise use distinguishable outcomes + timing as an internal-network
      reachability / port-probe primitive (including the cloud metadata endpoint 169.254.169.254).
      This module RESOLVES the host with a BOUNDED getaddrinfo, REJECTS any resolved IP that is
      loopback / private / link-local / unique-local / unspecified / multicast / reserved, and
      hands callers an imaplib client that dials ONLY a pre-validated IP — so a DNS-rebind (public
      on the check, private on the connect) cannot slip a private target past the guard. The two
      public openers (open_guarded_imap4 / open_guarded_imap4_ssl) own the whole flow incl. the
      on/off config switch, returning the STOCK imaplib class when the guard is disabled.
Used by: app.connectors.imap.client (DefaultImapClient verify path),
         app.connectors.imap.fetch_session (open_imap_session connect path).
Depends on: ipaddress + socket + ssl (stdlib), app.core.config (the on/off-by-env switch),
            app.connectors.exceptions (ConnectorError base for the new EgressBlockedError),
            app.connectors.imap.config (ImapConnectionParams).
Key invariants:
  - RESOLVE-THEN-CHECK-THEN-CONNECT-TO-THE-RESOLVED-IP. The validated IPs are the ones actually
    dialled (guarded_imap4 / guarded_imap4_ssl pin self.host to a validated literal IP), so a
    hostname that resolves public during the check but private during the connect still hits a
    validated IP — DNS-rebind cannot win.
  - BOUNDED RESOLUTION. getaddrinfo is run under its own wall-clock timeout (the socket connect
    timeout does NOT cover name resolution); a hang/slow resolver fails fast as a blocked target,
    never pins a worker thread.
  - FAIL CLOSED. If resolution yields no usable address, or ANY resolved IP is disallowed, the
    whole target is rejected (EgressBlockedError) — we never dial a partially-validated host.
  - GENERIC REFUSAL. EgressBlockedError's message is the same "host is not allowed" string for
    every blocked class; which rule matched is never surfaced, so the error itself does not aid
    probing. (The class IS logged server-side at debug for operators.)
  - TLS INTEGRITY PRESERVED. Pinning the dial to a validated IP keeps the ORIGINAL hostname as
    self.host, so ssl wrap_socket's server_hostname (SNI + cert hostname check) still validates
    against the real hostname, not the IP — security is added, certificate verification is not
    weakened.
  - OPT-OUT IS DEV-ONLY. settings.connector_egress_guard_enabled gates the guard; it defaults True
    (the only safe prod posture). When False, resolution/validation is skipped and the stock
    imaplib classes are used — for a local dev mail host on a private docker network only.
"""

from __future__ import annotations

import imaplib
import ipaddress
import socket
import ssl
from typing import Final

from app.connectors.exceptions import ConnectorError
from app.connectors.imap.config import ImapConnectionParams
from app.core.config import get_settings

# Wall-clock bound on name resolution. The socket connect timeout (15s verify / 60s fetch) does
# NOT cover getaddrinfo, so a slow/hostile resolver could hang a worker thread indefinitely; this
# caps it. Kept short — an interactive "test connection" should fail fast, and a legitimate DNS
# answer returns in well under a second.
_RESOLUTION_TIMEOUT_SECONDS: Final[float] = 5.0

# Generic, class-free refusal message (the same for every blocked address class, so the error
# cannot be used to distinguish loopback-vs-RFC1918-vs-link-local and aid probing).
_BLOCKED_MESSAGE: Final[str] = "The mailbox host is not allowed."


class EgressBlockedError(ConnectorError):
    """A connector dial target resolved to a disallowed (internal/reserved) address, or could not
    be resolved within the bound.

    Internal: surfaced through the existing connection-failure handling — the verify path reports
    the connection as failed/unreachable, the sync path treats it as a failed connection — so a
    blocked target never crashes a request and never reveals which rule matched. NOT mapped to its
    own HTTP status; it rides the generic connection-failure response like ImapConnectionError.
    """


def _is_disallowed_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if dialling this IP must be refused (SSRF-relevant address classes).

    Refuses the UNION of the explicit special-use flags AND any non-globally-routable address
    (`not is_global`). Both are needed — neither alone is complete (verified against the stdlib):
      - the explicit flags catch MULTICAST (224.0.0.0/4) and RESERVED (240.0.0.0/4), which Python
        reports as `is_global = True` (so `not is_global` would let them through);
      - `not is_global` catches RFC 6598 Carrier-Grade NAT (100.64.0.0/10) — common internal
        cloud/k8s/overlay infra — which `is_private` does NOT flag (so the flag-list misses it).
    Classes refused:
      - loopback            127.0.0.0/8, ::1
      - private (RFC1918)   10/8, 172.16/12, 192.168/16
      - CGN (RFC 6598)      100.64.0.0/10                  ← via `not is_global`
      - link-local          169.254/16 (incl. cloud metadata 169.254.169.254), fe80::/10
      - unique-local        fc00::/7
      - unspecified         0.0.0.0, ::
      - multicast           224.0.0.0/4                    ← via the explicit flag
      - reserved + the IPv4 broadcast 255.255.255.255
    Plus an explicit IPv4-mapped-IPv6 unwrap FIRST — an attacker could otherwise smuggle 127.0.0.1
    as ::ffff:127.0.0.1, whose v6 object reports the v4 special-use flags as False (and is_global
    True), slipping a loopback past the check.
    """
    # An IPv4 address mapped into IPv6 (::ffff:a.b.c.d) does not carry the v4 special-use semantics
    # on the v6 object — unwrap it to its v4 form and judge THAT, or 127.0.0.1 slips through as v6.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
        or ip.is_reserved
        or not ip.is_global  # closes RFC 6598 CGN (100.64.0.0/10) the flag-list leaves open
    )


def _resolve_host(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve `host` to its IP addresses under a bounded wall-clock timeout (fail-closed).

    Uses socket.getaddrinfo (the same resolver imaplib would use) but caps it via the
    process-global default socket timeout for the duration of the call — getaddrinfo respects it,
    and the socket connect timeout does NOT cover resolution. A resolution failure or an empty
    answer raises EgressBlockedError (we never dial an unresolvable / partially-resolved host).
    Returns the parsed, de-duplicated IPs for the caller to validate.
    """
    previous_default = socket.getdefaulttimeout()
    socket.setdefaulttimeout(_RESOLUTION_TIMEOUT_SECONDS)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError, UnicodeError) as exc:
        # UnicodeError: an IDNA-illegal hostname; gaierror/OSError: NXDOMAIN, timeout, no network.
        raise EgressBlockedError(_BLOCKED_MESSAGE) from exc
    finally:
        socket.setdefaulttimeout(previous_default)

    resolved: dict[str, ipaddress.IPv4Address | ipaddress.IPv6Address] = {}
    for info in infos:
        sockaddr = info[4]
        ip_literal = sockaddr[0]
        try:
            resolved[ip_literal] = ipaddress.ip_address(ip_literal)
        except ValueError:
            # A getaddrinfo result we cannot parse as an IP is itself suspicious — fail closed.
            raise EgressBlockedError(_BLOCKED_MESSAGE) from None
    if not resolved:
        raise EgressBlockedError(_BLOCKED_MESSAGE)
    return list(resolved.values())


def validate_egress_target(host: str, port: int) -> str:
    """Resolve `host` + validate every resolved IP; return ONE validated IP literal to dial.

    Unconditional (callers gate the on/off switch). Resolves under the resolution bound, rejects
    the WHOLE target if ANY resolved IP is disallowed (fail-closed — a split-horizon / round-robin
    name returning one public and one private A record must not be dialled on the public one), and
    returns the first allowed IP as a literal string. Callers MUST dial this returned literal (not
    the original hostname) so the connect cannot re-resolve to a DNS-rebind target.

    Raises:
        EgressBlockedError: the host could not be resolved within the bound, or any resolved IP is
            a loopback / private / link-local / unique-local / unspecified / multicast / reserved
            address. The message is the generic, class-free refusal.
    """
    resolved_ips = _resolve_host(host, port)
    if any(_is_disallowed_ip(ip) for ip in resolved_ips):
        raise EgressBlockedError(_BLOCKED_MESSAGE)
    return str(resolved_ips[0])


def _guarded_create_socket(port: int, timeout: float | None, dial_ip: str) -> socket.socket:
    """Open a TCP socket to the PRE-VALIDATED `dial_ip` (NOT the hostname) under `timeout`.

    Mirrors imaplib.IMAP4._create_socket but pins the connect to the validated IP, so the dial can
    never re-resolve the hostname to a rebind target.
    """
    if timeout is not None and not timeout:
        raise ValueError("Non-blocking socket (timeout=0) is not supported")
    address = (dial_ip, port)
    if timeout is not None:
        return socket.create_connection(address, timeout)
    return socket.create_connection(address)


def open_guarded_imap4(params: ImapConnectionParams, timeout: float) -> imaplib.IMAP4:
    """Open a cleartext imaplib.IMAP4 to `params.host`, SSRF-guarded unless disabled by config.

    Guard ON (prod default): resolve + validate the host, then dial ONLY the validated IP via a
    subclass that overrides _create_socket — DNS-rebind cannot redirect the connect, and a
    blocked target raises EgressBlockedError before any socket opens. Guard OFF (dev-only opt-out):
    the STOCK imaplib.IMAP4 is used unchanged (no SSRF protection, by explicit configuration).

    Raises EgressBlockedError (guard on, blocked/unresolvable) — callers map it to their generic
    connection failure. Construction OSError/imaplib errors propagate as today.
    """
    if not get_settings().connector_egress_guard_enabled:
        return imaplib.IMAP4(params.host, params.port, timeout=timeout)

    dial_ip = validate_egress_target(params.host, params.port)

    class _GuardedImap4(imaplib.IMAP4):
        def _create_socket(self, sock_timeout: float | None = None) -> socket.socket:  # type: ignore[override]
            return _guarded_create_socket(params.port, sock_timeout, dial_ip)

    return _GuardedImap4(params.host, params.port, timeout=timeout)


def open_guarded_imap4_ssl(
    params: ImapConnectionParams, timeout: float, ssl_context: ssl.SSLContext
) -> imaplib.IMAP4_SSL:
    """Open an imaplib.IMAP4_SSL to `params.host`, SSRF-guarded unless disabled by config.

    Guard ON (prod default): resolve + validate the host, dial ONLY the validated IP, but TLS-wrap
    with server_hostname = the ORIGINAL hostname — so certificate + hostname verification still run
    against the real name, never the IP (the guard adds an SSRF check WITHOUT weakening transport
    security). Guard OFF: the STOCK imaplib.IMAP4_SSL is used unchanged.

    Raises EgressBlockedError (guard on, blocked/unresolvable). Construction errors propagate.
    """
    if not get_settings().connector_egress_guard_enabled:
        return imaplib.IMAP4_SSL(params.host, params.port, ssl_context=ssl_context, timeout=timeout)

    dial_ip = validate_egress_target(params.host, params.port)

    class _GuardedImap4Ssl(imaplib.IMAP4_SSL):
        def _create_socket(self, sock_timeout: float | None = None) -> socket.socket:  # type: ignore[override]
            raw_sock = _guarded_create_socket(params.port, sock_timeout, dial_ip)
            # server_hostname = the ORIGINAL hostname → SNI + cert hostname check use the real name,
            # never the dialled IP. Mirrors imaplib.IMAP4_SSL._create_socket.
            return self.ssl_context.wrap_socket(raw_sock, server_hostname=params.host)

    return _GuardedImap4Ssl(params.host, params.port, ssl_context=ssl_context, timeout=timeout)
