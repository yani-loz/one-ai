"""
Role: ConnectorRegistry — maps a ConnectorType to a factory that builds a BaseConnector, plus
      build_default_registry() which discovers the built-in connectors. This is the pluggability
      seam: connectors register here, and the rest of the app instantiates connectors ONLY
      through this registry, never by importing a concrete connector module.
Used by: connectors.dependencies (holds one default registry); connectors.services.
         connector_service (calls create()).
Depends on: app.connectors.base.connector (BaseConnector), app.connectors.enums,
            app.connectors.exceptions, importlib (defensive discovery), logging.
Key invariants:
  - FAILURE ISOLATION (the hard requirement): build_default_registry never raises. A built-in
    connector whose module is ABSENT (folder removed) is skipped quietly; one that is PRESENT
    but fails to import is logged loudly and skipped — the app still boots either way. So adding
    or removing a connector folder never affects the rest of the app.
  - create() raises UnknownConnectorError for an unregistered type — a clean error, not a crash.
  - The registry stores FACTORIES, not instances: each create() builds a fresh, stateless
    connector from (config mapping, decrypted secret).
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
from collections.abc import Callable, Mapping

from app.connectors.base.connector import BaseConnector
from app.connectors.enums import ConnectorType
from app.connectors.exceptions import UnknownConnectorError

logger = logging.getLogger(__name__)

# (config_mapping, decrypted_secret) -> a ready-to-use connector.
ConnectorFactory = Callable[[Mapping[str, object], str], BaseConnector]

# Built-in connectors, discovered defensively at startup. (type, module path, factory attr).
_BUILTIN_CONNECTORS: list[tuple[ConnectorType, str, str]] = [
    (ConnectorType.imap, "app.connectors.imap.connector", "build_imap_connector"),
]


class ConnectorRegistry:
    """A mutable registry of connector factories keyed by ConnectorType."""

    def __init__(self) -> None:
        """Start with an empty factory table."""
        self._factories: dict[ConnectorType, ConnectorFactory] = {}

    def register(self, connector_type: ConnectorType, factory: ConnectorFactory) -> None:
        """Register (or replace) the factory for `connector_type`."""
        self._factories[connector_type] = factory

    def is_registered(self, connector_type: ConnectorType) -> bool:
        """Return True iff a factory is registered for `connector_type`."""
        return connector_type in self._factories

    def registered_types(self) -> list[ConnectorType]:
        """Return the connector types currently available."""
        return list(self._factories)

    def create(
        self, connector_type: ConnectorType, config: Mapping[str, object], secret: str
    ) -> BaseConnector:
        """Build a connector for `connector_type` from its config + decrypted secret.

        Raises:
            UnknownConnectorError: no factory is registered for the type (-> 400).
        """
        factory = self._factories.get(connector_type)
        if factory is None:
            raise UnknownConnectorError(
                f"Connector type '{connector_type.value}' is not available."
            )
        return factory(config, secret)


def build_default_registry() -> ConnectorRegistry:
    """Build a registry with every available built-in connector (never raises).

    Each connector is discovered defensively so the connectors subsystem tolerates a missing or
    broken connector module without affecting the rest of the app:
      - module ABSENT (find_spec is None)  -> quiet info log, skip;
      - module PRESENT but import/attr fails -> loud error log, skip (app still boots).
    """
    registry = ConnectorRegistry()
    for connector_type, module_path, factory_attr in _BUILTIN_CONNECTORS:
        _register_builtin(registry, connector_type, module_path, factory_attr)
    return registry


def _register_builtin(
    registry: ConnectorRegistry,
    connector_type: ConnectorType,
    module_path: str,
    factory_attr: str,
) -> None:
    """Discover + register one built-in connector, isolating any failure (see build_default)."""
    try:
        spec = importlib.util.find_spec(module_path)
    except ModuleNotFoundError:
        spec = None
    except Exception:
        # find_spec imports the connector's PARENT package to read its __path__, so a
        # present-but-broken connector can raise a non-ModuleNotFoundError right here. Treat it
        # as broken: log loud, skip — the app must still boot (failure isolation).
        logger.exception(
            "Connector '%s' could not be probed — skipping (app continues).",
            connector_type.value,
        )
        return
    if spec is None:
        logger.info(
            "Connector '%s' not installed (module %s absent) — skipping.",
            connector_type.value,
            module_path,
        )
        return
    try:
        module = importlib.import_module(module_path)
        registry.register(connector_type, getattr(module, factory_attr))
    except Exception:  # present but broken: log loud, keep the app up (failure isolation)
        logger.exception(
            "Connector '%s' is present but failed to load — skipping (app continues).",
            connector_type.value,
        )
