"""Connector-agnostic attachment/file text-extraction pipeline (bytes → ExtractionResult).

Connector-agnostic — imports nothing from any specific connector; CON-04 (local folders) and
future connectors reuse these directly.
"""
