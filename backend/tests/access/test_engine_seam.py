"""
Role: The AC18 engine-seam guards, proven structurally — (a) the access package has NO import
      path to the global (BYPASSRLS) engine surface, checked by AST over every module; (b) a
      tenant-scoped session running as any role but oneai_app aborts LOUDLY before tenant SQL
      runs (the silent fail-open — a tenant flow on a bypass pool — is unrepresentable).
Used by: pytest (tests/access). The AST test is pure; the runtime tests need provisioned roles.
Depends on: app.core.database (scoped_session + the listener under test), stdlib ast.
"""

from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

import app.access
from app.core.database import (
    EngineSeamViolationError,
    GlobalSessionLocal,
    _bind_scope,
    reader_session,
    runtime_roles_present,
    scoped_session,
)
from tests.conftest import seed_org

# The global-engine surface no access module may name (AC18: no import path, by construction).
_FORBIDDEN_DATABASE_SYMBOLS = {"global_engine", "GlobalSessionLocal", "get_session"}


def _imported_database_symbols(module_path: Path) -> set[str]:
    """The names a module imports from app.core.database (AST — no execution)."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.database":
            imported.update(alias.name for alias in node.names)
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names if alias.name == "app.core.database")
    return imported


def test_access_package_never_imports_the_global_engine() -> None:
    # AC18 (module-import guard): scanning EVERY module under app/access — none may import the
    # BYPASSRLS surface. 'app.core.database' as a bare module import is also forbidden (it would
    # allow attribute access to global_engine).
    package_root = Path(app.access.__file__).parent

    scanned = 0
    for module_path in package_root.rglob("*.py"):
        imported = _imported_database_symbols(module_path)
        forbidden = imported & (_FORBIDDEN_DATABASE_SYMBOLS | {"app.core.database"})
        assert not forbidden, (
            f"{module_path.relative_to(package_root)}: imports the global-engine surface "
            f"{sorted(forbidden)} — the access package must have NO path to a BYPASSRLS pool"
        )
        scanned += 1
    assert scanned >= 10, "AST sweep looks vacuous — access package files not found"


async def test_scope_binding_aborts_on_the_wrong_role() -> None:
    # AC18 (runtime assertion): binding a scope onto a BYPASSRLS session must abort the very
    # first statement — a scoped flow on the wrong pool fails LOUD, never open/silent.
    if not await runtime_roles_present():
        pytest.skip("Runtime DB roles missing — provision the DB first.")

    async with GlobalSessionLocal() as bypass_session:
        _bind_scope(bypass_session, uuid4(), None, "oneai_app")
        with pytest.raises(EngineSeamViolationError, match="oneai_app"):
            await bypass_session.execute(text("SELECT 1"))
        await bypass_session.rollback()


async def test_scoped_and_reader_sessions_run_as_their_expected_roles() -> None:
    # The positive control for the abort test: both REAL seams work, each on its own role —
    # scoped_session = the write plane (oneai_app), reader_session = the retrieval plane
    # (oneai_reader, the role the visibility policies target).
    if not await runtime_roles_present():
        pytest.skip("Runtime DB roles missing — provision the DB first.")
    org = await seed_org()

    async with scoped_session(org) as session:
        app_user = (await session.execute(text("SELECT current_user"))).scalar_one()
    async with reader_session(org, uuid4()) as session:
        reader_user = (await session.execute(text("SELECT current_user"))).scalar_one()

    assert app_user == "oneai_app"
    assert reader_user == "oneai_reader"
