# Lead re-verification of the headline NEW/FAIL findings — first-hand, independent of the
# suite-agents. Read-mostly (one refresh chain; no new orgs). Run:
#   cat testing/02_platform-console/harness/_common.py \
#       testing/02_platform-console/harness/_reverify.py | docker compose exec -T backend python -


async def main() -> None:
    import time as _t
    async with _client() as c:
        # A) FORGED-TOKEN ISOLATION BYPASS (XDOM TC-025, the security headline; READ-ONLY).
        ghost = forge_platform_token()  # random sub, dev secret, aud=platform
        orgs = await c.get("/platform/orgs", headers=bearer(ghost))
        n = len(orgs.json()) if orgs.status_code == 200 else orgs.text
        print(f"A) forged platform token -> GET /platform/orgs: {orgs.status_code}  org_count={n}"
              "  (200 + full fleet => dev-secret JWT is the single isolation layer)")

        # B) LOGIN PASSWORD BOUNDS (PLOGIN TC-013): only max_length=256 bounds it; no min8/72-byte.
        r7 = await c.post("/platform/login", json={"email": PLATFORM_EMAIL, "password": "short7x"})
        r100 = await c.post("/platform/login", json={"email": PLATFORM_EMAIL, "password": "a" * 100})
        r300 = await c.post("/platform/login", json={"email": PLATFORM_EMAIL, "password": "a" * 300})
        print(f"B) login pw bounds: 7-char={r7.status_code} (expect 401, NOT 422)  "
              f"100-char={r100.status_code} (expect 401, NOT 500)  300-char={r300.status_code} (expect 422 max_len)")

        # C) bcrypt cost paid on INVALID login (PLOGIN TC-011 / STRESS TC-073): unknown email ~ valid.
        t0 = _t.perf_counter()
        await c.post("/platform/login", json={"email": f"nobody-{stamp()}@oneai.dev", "password": "whatever-xyz"})
        inv = (_t.perf_counter() - t0) * 1000
        t0 = _t.perf_counter()
        await c.post("/platform/login", json={"email": PLATFORM_EMAIL, "password": PLATFORM_PW})
        val = (_t.perf_counter() - t0) * 1000
        print(f"C) bcrypt cost: invalid(unknown email)={inv:.0f}ms  valid={val:.0f}ms  "
              "(comparable => unknown email pays full bcrypt = the DoS-amplification surface)")

        # D) LOGOUT DOES NOT REVOKE THE TOKEN FAMILY (RACE TC-063): a descendant survives logout(parent).
        p_access, r0 = await platform_login_pair(c)
        rot = await c.post("/platform/refresh", json={"refresh_token": r0})
        r1 = rot.json()["refresh_token"]                       # descendant, minted by the refresh
        lo = await c.post("/platform/logout", json={"refresh_token": r0})   # user logs out the parent
        desc = await c.post("/platform/refresh", json={"refresh_token": r1})  # descendant still alive?
        print(f"D) family-revocation: rotate R0->R1={rot.status_code}; logout(R0)={lo.status_code}; "
              f"descendant R1 refresh={desc.status_code}  (200 => logout revokes only the presented hash, not the family)")


asyncio.run(main())
