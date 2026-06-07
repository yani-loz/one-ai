"""
Role: Maps connector-domain exceptions to HTTP responses. Registered once from main.py.
Used by: app.main (register_connector_exception_handlers(app)).
Depends on: app.connectors.exceptions, fastapi.
Key invariants:
  - Single source of the connector exception -> status mapping (co-located with the exceptions):
    ConnectionNotFound -> 404, Duplicate -> 409, UnknownConnector -> 400,
    ConnectorConfiguration -> 503. The catch-all ConnectorError handler returns 400 as a safe
    default for any future connector error lacking an explicit mapping.
  - Response bodies carry only the exception's own message, which is already secret-free.
  - ConnectorSecretError is intentionally NOT mapped: it is caught inside the service (a failed
    connection test), never propagated to a response.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.connectors.exceptions import (
    ConnectionNotFoundError,
    ConnectorConfigurationError,
    ConnectorError,
    DuplicateConnectionError,
    UnknownConnectorError,
)

_STATUS_BY_EXCEPTION: dict[type[ConnectorError], int] = {
    UnknownConnectorError: status.HTTP_400_BAD_REQUEST,
    ConnectionNotFoundError: status.HTTP_404_NOT_FOUND,
    DuplicateConnectionError: status.HTTP_409_CONFLICT,
    ConnectorConfigurationError: status.HTTP_503_SERVICE_UNAVAILABLE,
}


def register_connector_exception_handlers(app: FastAPI) -> None:
    """Register one handler per connector exception type on `app`.

    Contract: after this runs, raising any mapped ConnectorError from a route/service/dependency
    yields the corresponding status with a JSON `{"detail": <message>}` body. The catch-all
    ConnectorError handler returns 400 for any unmapped connector error.
    """
    for exception_type, status_code in _STATUS_BY_EXCEPTION.items():
        app.add_exception_handler(exception_type, _make_handler(status_code))
    app.add_exception_handler(ConnectorError, _make_handler(status.HTTP_400_BAD_REQUEST))


def _make_handler(
    status_code: int,
) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    """Build a FastAPI exception handler that responds with `status_code`."""

    async def handler(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    return handler
