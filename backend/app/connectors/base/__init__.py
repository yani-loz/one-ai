"""
Role: Public face of the connector ABSTRACTION layer — the contract concrete connectors
      implement and the registry the app instantiates them through.
Used by: concrete connectors (subclass BaseConnector / register a factory) and the connector
         service + dependencies (use the registry); nothing here imports a concrete connector.
Depends on: app.connectors.base.connector, app.connectors.base.registry.
"""

from __future__ import annotations

from app.connectors.base.connector import BaseConnector, ConnectionCheck
from app.connectors.base.registry import (
    ConnectorFactory,
    ConnectorRegistry,
    build_default_registry,
)

__all__ = [
    "BaseConnector",
    "ConnectionCheck",
    "ConnectorFactory",
    "ConnectorRegistry",
    "build_default_registry",
]
