"""Permission-fidelity module (EPIC-PF-01) — ACL grants, visibility promotion, source identity.

ENGINE-SEAM RULE (AC18): nothing under app.access may import app.core.database's global engine
surface (global_engine / GlobalSessionLocal / get_session) — tenant/retrieval flows run ONLY on
the RLS-enforced oneai_app pool. Proven structurally by
tests/access/test_engine_seam.py::test_access_package_never_imports_the_global_engine.
"""
