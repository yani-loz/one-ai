async def main() -> None:
    print("== TC-OL-001 — suspend gate is NOT an enumeration oracle (DISCRIMINATING, AC3) ==")
    async with _client() as c:
        p_access, _ = await platform_login_pair(c)
        comp = await provision_company(c, p_access, "sus001")
        oid, email = comp["org_id"], comp["admin_email"]
        print("[setup]   org", oid, "admin", email)

        # BASELINE: a wrong-password 401 while the org is STILL ACTIVE. This is the
        # reference body the suspended wrong-pw / unknown-email 401s must match byte-for-byte.
        base = await login(c, email, "wrong-password-baseline-xyz")
        print(f"[baseline] active-org wrong-pw: {base.status_code} body={base.content!r}")

        # Now suspend the org.
        s = await patch_status(c, p_access, oid, STATUS_SUSPENDED)
        print(f"[suspend]  PATCH status=suspended: {s.status_code} status={s.json().get('status')}")

        # (a) VALID creds under suspension -> 403 (reachable ONLY with valid creds).
        a = await login(c, email, DEFAULT_PW)
        print(f"[a valid]  suspended valid creds: {a.status_code} body={a.content!r}")

        # (b) WRONG password under suspension -> 401, body must equal the active baseline.
        b = await login(c, email, "wrong-password-baseline-xyz")
        print(f"[b wrong]  suspended wrong-pw:    {b.status_code} body={b.content!r}")

        # (c) UNKNOWN email -> 401, body must equal the active baseline.
        unknown_email = f"nobody-{stamp()}@oneai.dev"
        d = await login(c, unknown_email, "wrong-password-baseline-xyz")
        print(f"[c unknown] unknown email:        {d.status_code} body={d.content!r}")

        # Cleanup: reactivate the throwaway org.
        await patch_status(c, p_access, oid, STATUS_ACTIVE)

        identical = base.content == b.content == d.content
        codes_ok = (a.status_code == 403 and b.status_code == 401 and d.status_code == 401)
        oracle_free = a.content != b.content  # the 403 body differs from the 401 body
        ok = codes_ok and identical and oracle_free
        print("RESULT:", "PASS — 403 only with valid creds; (b)/(c) byte-identical to active baseline -> NO oracle"
              if ok else f"FAIL — codes_ok={codes_ok} identical={identical} oracle_free={oracle_free}")


asyncio.run(main())
