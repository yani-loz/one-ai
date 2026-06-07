"""Unit tests for ConnectorRegistry + build_default_registry (pluggability + failure isolation)."""

from __future__ import annotations

import importlib
import importlib.util

import pytest

from app.connectors.base.registry import ConnectorRegistry, build_default_registry
from app.connectors.enums import ConnectorType
from app.connectors.exceptions import UnknownConnectorError


def test_create_unregistered_type_raises_unknown_connector() -> None:
    registry = ConnectorRegistry()

    with pytest.raises(UnknownConnectorError):
        registry.create(ConnectorType.imap, {}, "secret")


def test_register_then_create_invokes_the_factory() -> None:
    registry = ConnectorRegistry()
    sentinel = object()
    registry.register(ConnectorType.imap, lambda config, secret: sentinel)  # type: ignore[arg-type,return-value]

    assert registry.create(ConnectorType.imap, {}, "s") is sentinel
    assert registry.is_registered(ConnectorType.imap)
    assert ConnectorType.imap in registry.registered_types()


def test_default_registry_includes_imap() -> None:
    registry = build_default_registry()

    assert registry.is_registered(ConnectorType.imap)


def test_default_registry_skips_absent_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate the connector folder being removed: find_spec returns None -> quiet skip, no raise.
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    registry = build_default_registry()

    assert not registry.is_registered(ConnectorType.imap)


def test_default_registry_survives_a_broken_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    # Present-but-broken connector: import raises -> logged loud, skipped, app still builds.
    def _raise(name: str) -> object:
        raise ImportError("connector is broken")

    monkeypatch.setattr(importlib, "import_module", _raise)

    registry = build_default_registry()

    assert not registry.is_registered(ConnectorType.imap)


def test_default_registry_survives_find_spec_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    # find_spec imports the connector's PARENT package, so a present-but-broken connector can
    # raise a NON-ModuleNotFoundError here. The registry must still not crash boot (regression
    # for the guard that previously only caught ModuleNotFoundError).
    def _raise(name: str) -> object:
        raise RuntimeError("probe blew up while importing the parent package")

    monkeypatch.setattr(importlib.util, "find_spec", _raise)

    registry = build_default_registry()

    assert not registry.is_registered(ConnectorType.imap)
