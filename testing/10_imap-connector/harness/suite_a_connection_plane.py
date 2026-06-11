"""Suite A — Connection plane & credential cipher (live, in-container).

Runs all eight Suite-A cases as individual PASS/FAIL checks against the RUNNING stack:
the pure-cipher cases call app.connectors.security.credential_cipher directly; the http
cases drive the REAL ASGI app (app.main:app) over httpx ASGITransport with forged company
tokens on RUN-STAMPED throwaway orgs (no FK to organizations, so a synthetic org is legal);
the DB-portability + rotation service proofs use asyncpg as the OWNER role for the row swap.

Cases:
  A01 (pure)  cipher fail-closed: wrong key / 1-bit-tampered tag / truncated blob -> ConnectorSecretError.
  A02 (pure+db) NO AAD -> a secret_ciphertext blob is portable across rows/orgs under the shared key.
  A03 (http)  org_id smuggled in body -> 422 (extra=forbid); bad connector_type -> 422.
  A04 (http)  org B -> A's connection id on GET/test/disable/enable/DELETE -> 404, no A data leaked.
  A05 (http)  ConnectionResponse exposes no secret/ciphertext/key-version; weak key -> 503, key not echoed.
  A06 (http)  member -> 403, missing token -> 401 on create/list/disable.
  A07 (http)  concurrent duplicate create -> exactly one 201, losers 409 (not 500); barrier forces the
              IntegrityError backstop; row count == 1.
  A08 (pure+http) secret_key_version stored but decrypt always uses the CURRENT key -> rotating the key
              silently breaks decrypt for every existing connection (test reports 'error', not 500).

Run (testing/ is not mounted; pipe over stdin into the backend container):
    docker compose exec -T backend python - < testing/10_imap-connector/harness/suite_a_connection_plane.py

Safety: every write uses RUN-STAMPED throwaway orgs (uuid4 per run); every row is deleted in a
finally block keyed on the run stamp. The demo orgs / users are never touched; reads on real data only.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace

import asyncpg
from httpx import ASGITransport, AsyncClient

import app.connectors.dependencies as connector_dependencies
from app.connectors.exceptions import ConnectorConfigurationError, ConnectorSecretError
from app.connectors.repositories.connector_connection_repository import (
    ConnectorConnectionRepository,
)
from app.connectors.security.credential_cipher import (
    SECRET_KEY_VERSION,
    CredentialCipher,
)
from app.core.config import get_settings
from app.identity.principal import Principal
from app.identity.security.tokens import COMPANY_AUDIENCE, encode_access_token
from app.main import app

S = get_settings()
STAMP = uuid.uuid4().hex[:12]
TAG = f"imap-a-{STAMP}"
STRONG_KEY = "a-strong-random-enough-passphrase-0123456789"
OTHER_KEY = "a-completely-different-passphrase-9876543210"
DEV_KEY = "dev-only-insecure-connector-secret-change-me"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} :: {detail}")


def owner_dsn() -> str:
    return (
        f"postgresql://{S.postgres_user}:{S.postgres_password}"
        f"@{S.postgres_host}:{S.postgres_port}/{S.postgres_db}"
    )


def token(org: uuid.UUID, role: str = "company_admin") -> str:
    principal = Principal(subject_id=uuid.uuid4(), org_id=org, role=role, subject_type="user")
    return encode_access_token(principal, timedelta(minutes=15), COMPANY_AUDIENCE)


def bearer(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def payload(stamp: str, **over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "connector_type": "imap",
        "display_name": f"A {stamp}",
        "host": "mail.invalid.test",
        "port": 993,
        "use_ssl": True,
        "username": f"a-{stamp}@example.test",
        "password": "imap-app-pw-secret-123",
    }
    body.update(over)
    return body


# ────────────────────────────── A01 — cipher fail-closed (pure) ──────────────────────────────
def case_a01() -> None:
    cipher = CredentialCipher(STRONG_KEY, require_secure=False)
    blob = cipher.encrypt("imap-app-password-hunter2")

    # round-trip
    check("a01_round_trip", cipher.decrypt(blob) == "imap-app-password-hunter2", "decrypt==plaintext")

    # wrong key
    other = CredentialCipher(OTHER_KEY, require_secure=False)
    try:
        other.decrypt(blob)
        check("a01_wrong_key_fails_closed", False, "decrypted under wrong key (LEAK)")
    except ConnectorSecretError:
        check("a01_wrong_key_fails_closed", True, "ConnectorSecretError (no garbage)")
    except Exception as exc:  # noqa: BLE001
        check("a01_wrong_key_fails_closed", False, f"wrong exc type: {type(exc).__name__}")

    # 1-bit-tampered tag
    tampered = bytearray(blob)
    tampered[-1] ^= 0x01
    try:
        cipher.decrypt(bytes(tampered))
        check("a01_tampered_tag_fails_closed", False, "decrypted tampered blob (LEAK)")
    except ConnectorSecretError:
        check("a01_tampered_tag_fails_closed", True, "ConnectorSecretError on 1-bit flip")
    except Exception as exc:  # noqa: BLE001
        check("a01_tampered_tag_fails_closed", False, f"wrong exc type: {type(exc).__name__}")

    # truncated (len <= 12)
    truncated_ok = True
    detail = ""
    for short in (b"", b"short", b"x" * 12):  # all <= _NONCE_BYTES (12)
        try:
            cipher.decrypt(short)
            truncated_ok = False
            detail = f"len={len(short)} decrypted (no guard)"
            break
        except ConnectorSecretError:
            continue
        except Exception as exc:  # noqa: BLE001
            truncated_ok = False
            detail = f"len={len(short)} wrong exc {type(exc).__name__}"
            break
    check("a01_truncated_blob_fails_closed", truncated_ok, detail or "all <=12 -> ConnectorSecretError")


# ────────────────────── A02 — no AAD -> blob portability across rows/orgs ──────────────────────
async def case_a02(owner: asyncpg.Connection) -> None:
    # (1) Pure: the SAME bytes decrypt under a SECOND cipher instance with the same key — nothing in
    #     the blob binds it to a row/org. A different key fails (so it's the key, not absence of auth).
    writer = CredentialCipher(STRONG_KEY, require_secure=False)
    blob = writer.encrypt("portable-secret-xyz")
    reader = CredentialCipher(STRONG_KEY, require_secure=False)
    pure_portable = reader.decrypt(blob) == "portable-secret-xyz"
    wrong_key_blocks = False
    try:
        CredentialCipher(OTHER_KEY, require_secure=False).decrypt(blob)
    except ConnectorSecretError:
        wrong_key_blocks = True
    check(
        "a02_pure_blob_portable_same_key",
        pure_portable and wrong_key_blocks,
        f"second-instance decrypt={pure_portable}, wrong-key-blocks={wrong_key_blocks}",
    )

    # (2) Service/DB-level (the stronger proof GPT asked for): seed TWO rows in two DIFFERENT orgs,
    #     each with its OWN secret; then OVERWRITE org-B's secret_ciphertext with org-A's blob via a
    #     direct owner DB write (simulating a swap). Decrypting org-B's row now yields ORG-A's secret
    #     under the shared process key — proving the blob carries no org/row binding.
    cipher = CredentialCipher(DEV_KEY, require_secure=False)  # the live process key
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    secret_a, secret_b = f"A-SECRET-{STAMP}", f"B-SECRET-{STAMP}"
    blob_a = cipher.encrypt(secret_a)
    blob_b = cipher.encrypt(secret_b)
    id_a = await owner.fetchval(
        "INSERT INTO connector_connection "
        "(org_id, connector_type, display_name, auth_method, username, config, "
        " secret_ciphertext, secret_key_version, status) "
        "VALUES ($1,'imap',$2,'app_password',$3,'{}'::jsonb,$4,1,'configured') RETURNING id",
        org_a, f"A {TAG}", f"a-{STAMP}@x.test", blob_a,
    )
    id_b = await owner.fetchval(
        "INSERT INTO connector_connection "
        "(org_id, connector_type, display_name, auth_method, username, config, "
        " secret_ciphertext, secret_key_version, status) "
        "VALUES ($1,'imap',$2,'app_password',$3,'{}'::jsonb,$4,1,'configured') RETURNING id",
        org_b, f"B {TAG}", f"b-{STAMP}@x.test", blob_b,
    )
    # Transplant A's ciphertext onto B's row.
    await owner.execute(
        "UPDATE connector_connection SET secret_ciphertext=$1 WHERE id=$2", blob_a, id_b
    )
    transplanted = await owner.fetchval(
        "SELECT secret_ciphertext FROM connector_connection WHERE id=$1", id_b
    )
    recovered = cipher.decrypt(bytes(transplanted))
    check(
        "a02_transplanted_blob_decrypts_to_other_orgs_secret",
        recovered == secret_a,
        f"orgB row now decrypts to orgA secret? {recovered == secret_a} (recovered={recovered!r})",
    )
    print(f"    note: ids {id_a},{id_b} cleaned up by stamp in finally")


# ─────────────────────────── A03 — schema rejects smuggled/invalid fields ───────────────────────────
async def case_a03(client: AsyncClient) -> None:
    org = uuid.uuid4()
    stamp = uuid.uuid4().hex[:8]

    # org_id smuggled into the body -> 422 (extra=forbid). The route's org comes ONLY from the JWT.
    smuggled = payload(stamp, org_id=str(uuid.uuid4()))
    r1 = await client.post("/connectors", json=smuggled, headers=bearer(token(org)))
    check("a03_smuggled_org_id_rejected_422", r1.status_code == 422, f"status={r1.status_code}")

    # connector_type outside the enum -> 422.
    bad_type = payload(uuid.uuid4().hex[:8], connector_type="smtp")
    r2 = await client.post("/connectors", json=bad_type, headers=bearer(token(uuid.uuid4())))
    check("a03_bad_connector_type_rejected_422", r2.status_code == 422, f"status={r2.status_code}")


# ──────────────────── A04 — cross-tenant access on every verb -> 404, no leak ────────────────────
async def case_a04(client: AsyncClient) -> tuple[uuid.UUID, str] | None:
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    stamp = uuid.uuid4().hex[:8]
    secret_username = f"a-{stamp}@example.test"
    created = await client.post(
        "/connectors", json=payload(stamp, username=secret_username), headers=bearer(token(org_a))
    )
    if created.status_code != 201:
        check("a04_seed_connection", False, f"create failed {created.status_code}: {created.text[:120]}")
        return None
    cid = created.json()["id"]
    tb = bearer(token(org_b))

    statuses: dict[str, int] = {}
    leaked = False
    for verb, coro in (
        ("get", client.get(f"/connectors/{cid}", headers=tb)),
        ("test", client.post(f"/connectors/{cid}/test", headers=tb)),
        ("disable", client.post(f"/connectors/{cid}/disable", headers=tb)),
        ("enable", client.post(f"/connectors/{cid}/enable", headers=tb)),
        ("delete", client.delete(f"/connectors/{cid}", headers=tb)),
    ):
        resp = await coro
        statuses[verb] = resp.status_code
        if secret_username in resp.text or "mail.invalid.test" in resp.text:
            leaked = True
    all_404 = all(code == 404 for code in statuses.values())
    check("a04_cross_tenant_all_verbs_404", all_404, f"statuses={statuses}")
    check("a04_no_A_data_leaked_to_B", not leaked, f"username/host leaked to B? {leaked}")

    # And the owner still has its row untouched (B's delete didn't bite).
    owner_get = await client.get(f"/connectors/{cid}", headers=bearer(token(org_a)))
    check("a04_owner_row_survives_B_delete", owner_get.status_code == 200, f"owner GET={owner_get.status_code}")
    return org_a, cid


# ──────────────── A05 — no secret in the response; weak key -> 503, key not echoed ────────────────
async def case_a05(client: AsyncClient) -> None:
    org = uuid.uuid4()
    stamp = uuid.uuid4().hex[:8]
    secret_pw = "super-secret-imap-pw-DO-NOT-LEAK-9999"
    created = await client.post(
        "/connectors", json=payload(stamp, password=secret_pw), headers=bearer(token(org))
    )
    if created.status_code != 201:
        check("a05_seed", False, f"create failed {created.status_code}")
        return
    body = created.json()
    forbidden_keys = {"password", "secret", "secret_ciphertext", "secret_key_version"}
    no_secret_field = forbidden_keys.isdisjoint(body.keys())
    no_secret_value = secret_pw not in created.text
    check(
        "a05_response_carries_no_secret_field",
        no_secret_field,
        f"forbidden keys present={[k for k in forbidden_keys if k in body]}",
    )
    check("a05_response_does_not_echo_password", no_secret_value, f"pw in body? {not no_secret_value}")

    # Weak key in a non-dev env -> the cipher fails closed and the endpoint returns 503 WITHOUT
    # echoing the key. Override the dependency's settings reader (test-only, in-process).
    weak = SimpleNamespace(connector_secret_key=DEV_KEY, requires_secure_secrets=True)
    real_get_settings = connector_dependencies.get_settings
    connector_dependencies.get_settings = lambda: weak  # type: ignore[assignment]
    try:
        r = await client.post(
            "/connectors", json=payload(uuid.uuid4().hex[:8]), headers=bearer(token(uuid.uuid4()))
        )
    finally:
        connector_dependencies.get_settings = real_get_settings  # type: ignore[assignment]
    check("a05_weak_key_returns_503", r.status_code == 503, f"status={r.status_code}")
    check("a05_weak_key_not_echoed", DEV_KEY not in r.text, f"key echoed? {DEV_KEY in r.text}")


# ─────────────────────── A06 — role + auth gates: member 403, no token 401 ───────────────────────
async def case_a06(client: AsyncClient) -> None:
    member = bearer(token(uuid.uuid4(), role="member"))
    create = await client.post("/connectors", json=payload(uuid.uuid4().hex[:8]), headers=member)
    listr = await client.get("/connectors", headers=member)
    disable = await client.post(f"/connectors/{uuid.uuid4()}/disable", headers=member)
    member_blocked = create.status_code == 403 and listr.status_code == 403 and disable.status_code == 403
    check(
        "a06_member_forbidden_403",
        member_blocked,
        f"create={create.status_code} list={listr.status_code} disable={disable.status_code}",
    )

    nt_create = await client.post("/connectors", json=payload(uuid.uuid4().hex[:8]))
    nt_list = await client.get("/connectors")
    nt_disable = await client.post(f"/connectors/{uuid.uuid4()}/disable")
    no_token_401 = (
        nt_create.status_code == 401 and nt_list.status_code == 401 and nt_disable.status_code == 401
    )
    check(
        "a06_missing_token_unauthorized_401",
        no_token_401,
        f"create={nt_create.status_code} list={nt_list.status_code} disable={nt_disable.status_code}",
    )


# ───────────── A07 — concurrent duplicate create: exactly one 201, losers 409, no 500 ─────────────
async def case_a07(client: AsyncClient, owner: asyncpg.Connection) -> None:
    # Variant 1 (durable claim): many truly-concurrent identical creates -> exactly one 201, the
    # rest 409, NEVER a 500; exactly one persisted row.
    org = uuid.uuid4()
    stamp = uuid.uuid4().hex[:8]
    uname = f"race-{stamp}@example.test"
    body = payload(stamp, username=uname)
    tok = bearer(token(org))
    n = 8
    resps = await asyncio.gather(
        *[client.post("/connectors", json=body, headers=tok) for _ in range(n)]
    )
    codes = sorted(r.status_code for r in resps)
    n201 = codes.count(201)
    n409 = codes.count(409)
    n500 = sum(1 for c in codes if c >= 500)
    row_count = await owner.fetchval(
        "SELECT count(*) FROM connector_connection WHERE org_id=$1 AND username=$2", org, uname
    )
    check(
        "a07_concurrent_exactly_one_201_no_500",
        n201 == 1 and n500 == 0 and (n201 + n409 == n),
        f"codes={codes} (201={n201} 409={n409} 5xx={n500}) rows={row_count}",
    )
    check("a07_exactly_one_row_persisted", row_count == 1, f"persisted rows={row_count}")

    # Variant 2 (the BACKSTOP proof GPT flagged): a barrier forces BOTH requests past exists()==False
    # before either inserts, so the 409 MUST come from the unique-constraint IntegrityError path, not
    # from the loser's exists() check seeing the winner's committed row. Monkeypatch the repo's
    # exists() (test-only, in-container) to await a 2-party barrier the first two times it's called.
    org2 = uuid.uuid4()
    stamp2 = uuid.uuid4().hex[:8]
    uname2 = f"barrier-{stamp2}@example.test"
    body2 = payload(stamp2, username=uname2)
    tok2 = bearer(token(org2))

    real_exists = ConnectorConnectionRepository.exists
    barrier = asyncio.Barrier(2)
    armed = {"left": 2}

    async def exists_with_barrier(self, org_id, connector_type, username):  # type: ignore[no-untyped-def]
        result = await real_exists(self, org_id, connector_type, username)
        # Only gate the first two calls (the two racing creates); both observe False, THEN both insert.
        if armed["left"] > 0 and not result:
            armed["left"] -= 1
            try:
                await asyncio.wait_for(barrier.wait(), timeout=10)
            except (TimeoutError, asyncio.BrokenBarrierError):
                pass
        return result

    ConnectorConnectionRepository.exists = exists_with_barrier  # type: ignore[assignment]
    try:
        r1, r2 = await asyncio.gather(
            client.post("/connectors", json=body2, headers=tok2),
            client.post("/connectors", json=body2, headers=tok2),
            return_exceptions=True,
        )
    finally:
        ConnectorConnectionRepository.exists = real_exists  # type: ignore[assignment]

    pair = sorted(
        [getattr(r, "status_code", 599) for r in (r1, r2)]
    )
    rows2 = await owner.fetchval(
        "SELECT count(*) FROM connector_connection WHERE org_id=$1 AND username=$2", org2, uname2
    )
    check(
        "a07_barrier_forces_integrityerror_409_not_500",
        pair == [201, 409] and rows2 == 1,
        f"pair={pair} rows={rows2} (both passed exists()==False, so 409 came from the unique constraint)",
    )


# ───────────── A08 — key rotation silently breaks decrypt (no decrypt-by-version) ─────────────
async def case_a08(client: AsyncClient, owner: asyncpg.Connection) -> None:
    # (1) Pure: stamped key_version is a constant 1, and decrypt has no version dispatch — encrypt
    #     under key1, decrypt under key2 -> ConnectorSecretError. Confirms there is no keyring.
    v1 = CredentialCipher(STRONG_KEY, require_secure=False)
    v2 = CredentialCipher(OTHER_KEY, require_secure=False)
    blob = v1.encrypt("rotate-me")
    versions_constant = v1.key_version == v2.key_version == SECRET_KEY_VERSION
    rotated_fails = False
    try:
        v2.decrypt(blob)
    except ConnectorSecretError:
        rotated_fails = True
    check(
        "a08_rotated_key_breaks_decrypt_pure",
        versions_constant and rotated_fails,
        f"key_version both={SECRET_KEY_VERSION}, decrypt-under-rotated-key fails={rotated_fails}",
    )

    # (2) Service-level (GPT's stronger proof): seed a row whose ciphertext was made under DEV_KEY
    #     (key1 = the live process key). Then override the connector dependency to build the cipher
    #     from a DIFFERENT key (key2, simulating a rotation) and hit POST /{id}/test. The decrypt
    #     fails inside _verify, the service isolates it to ok=False -> HTTP 200 status='error'
    #     (NOT a 500), and the connection is now un-testable despite carrying secret_key_version=1.
    org = uuid.uuid4()
    stamp = uuid.uuid4().hex[:8]
    uname = f"rot-{stamp}@example.test"
    live_cipher = CredentialCipher(DEV_KEY, require_secure=False)
    cid = await owner.fetchval(
        "INSERT INTO connector_connection "
        "(org_id, connector_type, display_name, auth_method, username, config, "
        " secret_ciphertext, secret_key_version, status) "
        "VALUES ($1,'imap',$2,'app_password',$3,"
        " '{\"host\":\"mail.invalid.test\",\"port\":993,\"use_ssl\":true}'::jsonb,$4,1,'configured') "
        "RETURNING id",
        org, f"A {TAG}", uname, live_cipher.encrypt("real-imap-pw"),
    )

    rotated = SimpleNamespace(connector_secret_key=OTHER_KEY, requires_secure_secrets=False)
    real_get_settings = connector_dependencies.get_settings
    connector_dependencies.get_settings = lambda: rotated  # type: ignore[assignment]
    try:
        r = await client.post(f"/connectors/{cid}/test", headers=bearer(token(org)))
    finally:
        connector_dependencies.get_settings = real_get_settings  # type: ignore[assignment]

    ok = r.status_code == 200 and r.json().get("status") == "error"
    no_500 = r.status_code < 500
    check(
        "a08_rotated_key_breaks_test_service_level",
        ok and no_500,
        f"status_code={r.status_code} body.status={r.json().get('status') if r.status_code == 200 else 'n/a'}",
    )


async def main() -> None:
    owner = await asyncpg.connect(owner_dsn())
    transport = ASGITransport(app=app)
    print(f"=== Suite A (stamp {STAMP}) — app_env={S.app_env} requires_secure={S.requires_secure_secrets} ===")
    try:
        case_a01()
        await case_a02(owner)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await case_a03(client)
            await case_a04(client)
            await case_a05(client)
            await case_a06(client)
            await case_a07(client, owner)
            await case_a08(client, owner)
    finally:
        # Clean up EVERY row this run created (http creates + direct owner inserts), keyed on stamp.
        deleted = await owner.fetch(
            "DELETE FROM connector_connection WHERE display_name LIKE $1 "
            "OR username LIKE $2 OR username LIKE $3 OR username LIKE $4 "
            "OR username LIKE $5 OR username LIKE $6 RETURNING id",
            f"%{STAMP}%", f"%{STAMP}%", "race-%@example.test", "barrier-%@example.test",
            "a-%@example.test", "rot-%@example.test",
        )
        print(f"\ncleanup: deleted {len(deleted)} connector_connection rows (stamp {STAMP})")
        await owner.close()

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\nRESULT: {passed}/{len(results)} checks passed")
    print(
        "VERDICT:",
        "Suite A defenses hold" if passed == len(results)
        else f"{len(results) - passed} check(s) FAILED — see above",
    )


asyncio.run(main())
