async def main() -> None:
    """TC-PC-011 — no user-enumeration oracle on /platform/login.

    Wrong password for the REAL demo email vs a totally-unknown (run-stamped) email must
    both → 401 with a BYTE-IDENTICAL body. Also time many trials of each and report the
    medians are comparable (DUMMY_PASSWORD_HASH pays bcrypt either way). Never mutates the
    demo admin: it only sends a WRONG password, which cannot succeed."""
    import statistics

    prefix = "plogin"
    unknown_email = f"nobody-{prefix}-{stamp()}@oneai.dev"
    wrong_pw = "definitely-the-wrong-password-2026"

    async with _client() as c:
        # 1) Byte-identical body check (single representative request each)
        r_wrong = await c.post(
            "/platform/login", json={"email": PLATFORM_EMAIL, "password": wrong_pw}
        )
        r_unknown = await c.post(
            "/platform/login", json={"email": unknown_email, "password": wrong_pw}
        )
        print(f"wrong_pw_status={r_wrong.status_code} unknown_status={r_unknown.status_code}")
        print(f"wrong_pw_body={r_wrong.text!r}")
        print(f"unknown_body={r_unknown.text!r}")
        bodies_identical = r_wrong.content == r_unknown.content
        print(f"bodies_byte_identical={bodies_identical}")

        # 2) Timing: many trials each, report medians (s)
        trials = 30

        async def time_post(email: str) -> float:
            t0 = time.perf_counter()
            resp = await c.post("/platform/login", json={"email": email, "password": wrong_pw})
            dt = time.perf_counter() - t0
            assert resp.status_code == 401, resp.status_code
            return dt

        wrong_times = [await time_post(PLATFORM_EMAIL) for _ in range(trials)]
        unknown_times = [await time_post(f"nobody-{prefix}-{stamp()}@oneai.dev") for _ in range(trials)]

        med_wrong = statistics.median(wrong_times)
        med_unknown = statistics.median(unknown_times)
        ratio = (med_unknown / med_wrong) if med_wrong else float("inf")
        print(f"median_wrong_pw_s={med_wrong:.4f} median_unknown_email_s={med_unknown:.4f} ratio={ratio:.3f}")
        # Comparable if within ~2x (both pay one bcrypt verify); not a strict pass/fail gate.
        comparable = 0.5 <= ratio <= 2.0
        print(f"timings_comparable(0.5..2.0x)={comparable}")

        both_401 = r_wrong.status_code == 401 and r_unknown.status_code == 401
        ok = both_401 and bodies_identical
        print(f"VERDICT={'PASS' if ok else 'FAIL'} (timing_comparable={comparable})")


asyncio.run(main())
