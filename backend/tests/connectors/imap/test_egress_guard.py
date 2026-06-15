"""
Role: Unit tests for the SSRF egress guard (CA-CONN-02) — the resolve-then-validate-then-dial
      defence on the connector's server-initiated IMAP dials. Proves every blocked address class
      (loopback / RFC1918 / link-local incl. cloud metadata 169.254.169.254 / unspecified /
      unique-local / multicast-reserved / IPv4-mapped-IPv6 / IPv6 variants) is REFUSED, a public
      target is ALLOWED, a DNS-rebind (public-looking host resolving to a private IP) is REFUSED,
      getaddrinfo is BOUNDED, the dial is pinned to the VALIDATED IP (rebind-proof) while TLS still
      verifies the ORIGINAL hostname, the refusal is generic (no rule leak), and the dev-only
      opt-out yields the STOCK imaplib class.
Used by: pytest (tests/connectors/imap). No real network — socket.getaddrinfo + socket.create_
         connection + the settings lookup are monkeypatched at the boundary.
Depends on: app.connectors.imap.egress_guard, app.connectors.imap.config, app.core.config.

NB: tests/connectors/imap/conftest.py defaults the guard OFF (autouse) for the vendor-boundary
suites; the guard-ON tests here re-enable it explicitly via _force_guard, overriding that default.
"""

from __future__ import annotations

import ipaddress
import socket
import ssl

import pytest

from app.connectors.imap.config import imap_params_from_config
from app.connectors.imap.egress_guard import (
    _RESOLUTION_TIMEOUT_SECONDS,
    EgressBlockedError,
    _is_disallowed_ip,
    open_guarded_imap4,
    open_guarded_imap4_ssl,
    validate_egress_target,
)
from app.core.config import Settings

_SSL_PARAMS = imap_params_from_config(
    {"host": "mail.example.com", "port": 993, "use_ssl": True, "username": "u@x.com"}
)
_PLAIN_PARAMS = imap_params_from_config(
    {"host": "mail.example.com", "port": 143, "use_ssl": False, "username": "u@x.com"}
)


def _force_guard(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> None:
    """Override the package-default guard state for a single test (on or off)."""
    settings = Settings(connector_egress_guard_enabled=enabled)
    monkeypatch.setattr("app.connectors.imap.egress_guard.get_settings", lambda: settings)


def _fake_getaddrinfo(*ips: str):
    """Build a socket.getaddrinfo stand-in that resolves any host to the given IP literals."""

    def _resolver(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        infos = []
        for ip in ips:
            family = socket.AF_INET6 if ":" in ip else socket.AF_INET
            sockaddr = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
            infos.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
        return infos

    return _resolver


# ── _is_disallowed_ip: the pure address-class predicate (every SSRF-relevant class) ──
@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "127.5.5.5",  # loopback /8
        "10.0.0.5",  # RFC1918 10/8
        "172.16.4.4",  # RFC1918 172.16/12
        "192.168.1.10",  # RFC1918 192.168/16
        "169.254.169.254",  # link-local — THE cloud metadata endpoint
        "169.254.1.1",  # link-local /16
        "0.0.0.0",  # unspecified
        "255.255.255.255",  # broadcast (reserved)
        "224.0.0.1",  # multicast
        "240.0.0.1",  # reserved
        "100.64.0.1",  # RFC 6598 Carrier-Grade NAT (is_private=False; caught via not is_global)
        "100.127.255.254",  # CGN upper bound (100.64.0.0/10)
        "::1",  # IPv6 loopback
        "fe80::1",  # IPv6 link-local
        "fc00::1",  # IPv6 unique-local
        "fd12:3456::1",  # IPv6 unique-local (fd00::/8 within fc00::/7)
        "::",  # IPv6 unspecified
        "::ffff:127.0.0.1",  # IPv4-mapped-IPv6 loopback (must unwrap, else slips through)
        "::ffff:169.254.169.254",  # IPv4-mapped-IPv6 metadata
        "::ffff:10.0.0.1",  # IPv4-mapped-IPv6 RFC1918
    ],
)
def test_is_disallowed_ip_blocks_every_internal_class(ip: str) -> None:
    assert _is_disallowed_ip(ipaddress.ip_address(ip)) is True


@pytest.mark.parametrize(
    "ip",
    [
        "8.8.8.8",  # public IPv4 (Google DNS)
        "1.1.1.1",  # public IPv4 (Cloudflare)
        "93.184.216.34",  # public IPv4 (example.com historic)
        "2606:4700:4700::1111",  # public IPv6 (Cloudflare)
        "::ffff:8.8.8.8",  # IPv4-mapped-IPv6 of a PUBLIC v4 — allowed (unwraps to public)
    ],
)
def test_is_disallowed_ip_allows_public_addresses(ip: str) -> None:
    assert _is_disallowed_ip(ipaddress.ip_address(ip)) is False


# ── validate_egress_target: resolution + the whole-target fail-closed decision ──
def test_validate_egress_target_public_host_returns_resolved_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))

    assert validate_egress_target("mail.example.com", 993) == "93.184.216.34"


def test_validate_egress_target_loopback_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("127.0.0.1"))

    with pytest.raises(EgressBlockedError):
        validate_egress_target("localhost-alias.example.com", 993)


def test_validate_egress_target_cloud_metadata_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # The headline target: a tenant admin pointing the connector at 169.254.169.254 to read cloud
    # instance credentials must be refused before any socket opens.
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("169.254.169.254"))

    with pytest.raises(EgressBlockedError):
        validate_egress_target("metadata.example.com", 80)


def test_validate_egress_target_rejects_whole_target_if_any_ip_is_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Split-horizon / round-robin: a name resolving to one public AND one private A record must be
    # refused outright — dialling the public one would still let the private one be reached on retry
    # and signals the private one exists.
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34", "10.0.0.5"))

    with pytest.raises(EgressBlockedError):
        validate_egress_target("mixed.example.com", 993)


def test_validate_egress_target_unresolvable_host_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _nxdomain(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _nxdomain)

    with pytest.raises(EgressBlockedError):
        validate_egress_target("does-not-exist.invalid", 993)


def test_validate_egress_target_message_is_generic_and_class_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The refusal must NOT reveal which rule matched (loopback vs RFC1918 vs metadata) — the error
    # itself would otherwise be a probing oracle. Same message for an internal IP and an NXDOMAIN.
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("10.0.0.1"))

    with pytest.raises(EgressBlockedError) as exc_info:
        validate_egress_target("private.example.com", 993)

    message = str(exc_info.value)
    assert "10.0.0.1" not in message  # the resolved IP never leaks
    assert "loopback" not in message.lower() and "private" not in message.lower()
    assert "rfc" not in message.lower()


def test_validate_egress_target_bounds_getaddrinfo_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The socket connect timeout does NOT cover getaddrinfo, so the guard must impose its own
    # wall-clock bound on resolution — assert it sets the default socket timeout for the call and
    # restores it afterwards (a slow/hostile resolver fails fast, never pins a worker thread).
    seen_timeout: list[float | None] = []
    original_default = socket.getdefaulttimeout()

    def _capturing_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen_timeout.append(socket.getdefaulttimeout())
        return _fake_getaddrinfo("93.184.216.34")(host, port)

    monkeypatch.setattr(socket, "getaddrinfo", _capturing_getaddrinfo)

    validate_egress_target("mail.example.com", 993)

    assert seen_timeout == [_RESOLUTION_TIMEOUT_SECONDS]  # bounded DURING resolution
    assert socket.getdefaulttimeout() == original_default  # and restored after


# ── open_guarded_imap4_ssl: rebind-proof dial + preserved TLS hostname (guard ON) ──
class _StopHandshakeError(Exception):
    """Sentinel raised by the fake socket on the first IMAP command write.

    The security contract under test is *which IP gets dialled* and *which hostname gets
    TLS-wrapped* — both captured at the socket/wrap seam BEFORE imaplib's post-connect handshake
    (read greeting → send CAPABILITY). We let imaplib get exactly that far, then abort the
    command write, rather than faking a full IMAP server. So the dial/wrap assertions are valid
    and the construction stops deterministically without a real network exchange.
    """


class _SocketProbe:
    """Captures the (ip, port) create_connection dialled and the wrap_socket server_hostname."""

    def __init__(self) -> None:
        self.dialled: tuple[str, int] | None = None
        self.wrapped_hostname: str | None = None


def _install_socket_probe(monkeypatch: pytest.MonkeyPatch, probe: _SocketProbe) -> None:
    """Monkeypatch socket.create_connection so a guarded open dials a fake socket we can inspect."""

    class _FakeSock:
        def makefile(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            import io

            # imaplib reads this as the server greeting; no CAPABILITY in it, so imaplib then
            # tries to SEND a CAPABILITY command — which is where sendall aborts construction.
            return io.BytesIO(b"* OK fake-imap ready\r\n")

        def sendall(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            # First command write after the greeting — both the dial and the TLS-wrap have already
            # been captured by now, so abort here instead of faking the rest of the protocol.
            raise _StopHandshakeError

        def shutdown(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            # imaplib's __init__ error path calls sock.shutdown() during cleanup — must be a no-op.
            pass

        def close(self) -> None:
            pass

    def _fake_create_connection(address, *args, **kwargs):  # type: ignore[no-untyped-def]
        probe.dialled = address
        return _FakeSock()

    monkeypatch.setattr(socket, "create_connection", _fake_create_connection)


def test_open_guarded_imap4_ssl_dials_validated_ip_not_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The connect must go to the VALIDATED IP, never re-resolve the hostname — this is what defeats
    # DNS-rebind (a name that validated public then flips to private on the connect).
    _force_guard(monkeypatch, enabled=True)
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    probe = _SocketProbe()
    _install_socket_probe(monkeypatch, probe)

    class _CapturingContext:
        def wrap_socket(self, sock, server_hostname=None):  # type: ignore[no-untyped-def]
            probe.wrapped_hostname = server_hostname
            return sock

    # Construction dials the validated IP + TLS-wraps the original hostname, then aborts at the
    # first IMAP command write (_StopHandshakeError) — both captures land before the abort.
    with pytest.raises(_StopHandshakeError):
        open_guarded_imap4_ssl(_SSL_PARAMS, 15.0, _CapturingContext())  # type: ignore[arg-type]

    assert probe.dialled == ("93.184.216.34", 993)  # dialled the validated IP, not the hostname
    assert probe.wrapped_hostname == "mail.example.com"  # but TLS verifies the ORIGINAL hostname


def test_open_guarded_imap4_ssl_blocks_private_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_guard(monkeypatch, enabled=True)
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("169.254.169.254"))
    probe = _SocketProbe()
    _install_socket_probe(monkeypatch, probe)

    with pytest.raises(EgressBlockedError):
        open_guarded_imap4_ssl(_SSL_PARAMS, 15.0, ssl.create_default_context())

    assert probe.dialled is None  # refused BEFORE any socket opened


def test_open_guarded_imap4_dials_validated_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_guard(monkeypatch, enabled=True)
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("8.8.8.8"))
    probe = _SocketProbe()
    _install_socket_probe(monkeypatch, probe)

    # Construction dials the validated IP, then aborts at the first IMAP command write.
    with pytest.raises(_StopHandshakeError):
        open_guarded_imap4(_PLAIN_PARAMS, 15.0)

    assert probe.dialled == ("8.8.8.8", 143)  # validated IP dialled, not the hostname


def test_open_guarded_imap4_blocks_private_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_guard(monkeypatch, enabled=True)
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("192.168.0.10"))
    probe = _SocketProbe()
    _install_socket_probe(monkeypatch, probe)

    with pytest.raises(EgressBlockedError):
        open_guarded_imap4(_PLAIN_PARAMS, 15.0)

    assert probe.dialled is None


# ── DNS-rebind: a public-LOOKING host that resolves to a private IP is refused ──
def test_dns_rebind_public_hostname_resolving_private_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The classic SSRF-bypass: the attacker controls a public-looking hostname whose A record points
    # at an internal address. validate-then-dial-the-resolved-IP means the resolved private IP is
    # judged and the whole dial is refused — the connect never reaches the private target.
    _force_guard(monkeypatch, enabled=True)
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("169.254.169.254"))
    probe = _SocketProbe()
    _install_socket_probe(monkeypatch, probe)

    with pytest.raises(EgressBlockedError):
        open_guarded_imap4_ssl(_SSL_PARAMS, 15.0, ssl.create_default_context())

    assert probe.dialled is None


# ── dev-only opt-out: guard OFF uses the STOCK imaplib class, no resolution/validation ──
def test_guard_disabled_uses_stock_imap4_ssl_without_resolving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With the guard OFF (dev opt-out) the stock imaplib.IMAP4_SSL is returned and our resolver is
    # never consulted — a private host is reachable BY EXPLICIT CONFIGURATION (a local docker mail
    # container), which is the whole point of the opt-out.
    _force_guard(monkeypatch, enabled=False)
    import imaplib

    resolved: list[str] = []
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: resolved.append("called") or _fake_getaddrinfo("10.0.0.1")(a[0], a[1]),
    )

    captured: dict[str, object] = {}

    class _StockSslSpy(imaplib.IMAP4_SSL):
        def open(self, host="", port=993, timeout=None):  # type: ignore[no-untyped-def]
            # imaplib calls open() BEFORE its _connect() try-block, so capturing the host here and
            # raising stops construction cleanly — we only need to prove WHICH host the stock class
            # was handed (the original, un-pinned name), not run a real SSL session.
            captured["host"] = host
            raise _StopHandshakeError

    monkeypatch.setattr(imaplib, "IMAP4_SSL", _StockSslSpy)

    with pytest.raises(_StopHandshakeError):
        open_guarded_imap4_ssl(_SSL_PARAMS, 15.0, ssl.create_default_context())

    assert captured["host"] == "mail.example.com"  # stock class, original host, no IP pinning
    assert resolved == []  # the egress resolver was never called
