"""
Role: Liveness/readiness endpoint. Confirms the API is up AND the DB is reachable.
Used by: Docker healthchecks, load balancers, the frontend connection probe.
Depends on: app.core.database, app.core.config.
Key invariants: /health performs a real DB round-trip — a 200 means DB-reachable.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness + DB readiness probe")
async def health_check(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    """Return service status after a real `SELECT 1` against Postgres."""
    await session.execute(text("SELECT 1"))
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "database": "reachable",
    }
