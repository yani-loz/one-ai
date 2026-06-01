async def main() -> None:
    """TC-PC-012 — inactive platform admin login is rejected WITHOUT a status leak.

    Logs in tw-inactive-tw06012c3@oneai.dev (is_active=false in the throwaway pool) with the
    CORRECT password → expect 401 whose body is the SAME generic message as a wrong-password
    401 (no 'account disabled' / 'inactive' leak that would distinguish it from bad creds).
    Owned read-only: we never mutate this account."""
    inactive_email = "tw-inactive-tw06012c3@oneai.dev"
    correct_pw = "Valid-Pass-2026!"
    wrong_pw = "this-is-the-wrong-password-2026"

    async with _client() as c:
        # inactive admin + CORRECT password
        r_inactive = await c.post(
            "/platform/login", json={"email": inactive_email, "password": correct_pw}
        )
        # the SAME inactive admin + WRONG password (baseline generic 401)
        r_wrong = await c.post(
            "/platform/login", json={"email": inactive_email, "password": wrong_pw}
        )
        # an active demo admin + WRONG password (cross-check the generic body is universal)
        r_active_wrong = await c.post(
            "/platform/login", json={"email": PLATFORM_EMAIL, "password": wrong_pw}
        )

        print(f"inactive_correct_pw_status={r_inactive.status_code}")
        print(f"inactive_correct_pw_body={r_inactive.text!r}")
        print(f"inactive_wrong_pw_status={r_wrong.status_code}")
        print(f"inactive_wrong_pw_body={r_wrong.text!r}")
        print(f"active_wrong_pw_status={r_active_wrong.status_code}")
        print(f"active_wrong_pw_body={r_active_wrong.text!r}")

        same_as_wrong = r_inactive.content == r_wrong.content
        same_as_active_wrong = r_inactive.content == r_active_wrong.content
        leaks_status = any(
            kw in r_inactive.text.lower() for kw in ("inactive", "disabled", "deactivat", "suspend")
        )
        print(f"body_matches_inactive_wrong_pw={same_as_wrong}")
        print(f"body_matches_active_wrong_pw={same_as_active_wrong}")
        print(f"body_leaks_account_status={leaks_status}")

        ok = (
            r_inactive.status_code == 401
            and same_as_wrong
            and same_as_active_wrong
            and not leaks_status
        )
        print(f"VERDICT={'PASS' if ok else 'FAIL'}")


asyncio.run(main())
