async def main() -> None:
    """TC-PC-013 — password length boundaries on /platform/login.

    Brief hypothesis: a 200-char password (>72 bytes) → 422; a 7-char password (<8) → 422.
    The REAL contract that matters for security is 'never a 500'. PlatformLoginRequest's
    password field is `Field(min_length=1, max_length=256)` (NOT the BcryptPassword type
    used by user-create), so:
      - 7 chars  -> passes validation -> bcrypt verify, no match -> 401 (NOT 422)
      - 200 chars (>72 bytes) -> passes validation (200 < 256) -> verify_password catches
        bcrypt's ValueError and returns False -> 401 (NOT 422, and CRUCIALLY NOT 500)
      - 257 chars -> exceeds max_length=256 -> 422 (the only real bound)
    We assert the security-relevant invariant: no 5xx on any of these. We also record the
    422-vs-401 divergence from the brief's assumption as evidence."""
    seven = "short12"            # 7 chars (< the assumed min_length 8)
    overlong = "a" * 200         # 200 chars => 200 bytes > bcrypt's 72-byte limit
    over_max = "a" * 257         # 257 chars > Field max_length 256

    async with _client() as c:
        r7 = await c.post("/platform/login", json={"email": PLATFORM_EMAIL, "password": seven})
        r200 = await c.post("/platform/login", json={"email": PLATFORM_EMAIL, "password": overlong})
        r257 = await c.post("/platform/login", json={"email": PLATFORM_EMAIL, "password": over_max})

        for label, r in (("7char", r7), ("200char_72byte", r200), ("257char_over_max", r257)):
            print(f"{label}: status={r.status_code} body={r.text[:200]!r}")

        no_5xx = all(r.status_code < 500 for r in (r7, r200, r257))
        seven_is_422 = r7.status_code == 422
        overlong_is_422 = r200.status_code == 422
        overlong_is_500 = r200.status_code == 500
        over_max_is_422 = r257.status_code == 422

        print(f"no_server_error_5xx={no_5xx}")
        print(f"seven_char_is_422(brief_expected)={seven_is_422}  actual_status={r7.status_code}")
        print(f"overlong_200_is_422(brief_expected)={overlong_is_422}  is_500={overlong_is_500}  actual_status={r200.status_code}")
        print(f"over_max_257_is_422={over_max_is_422}  actual_status={r257.status_code}")

        # PASS = the real security invariant (no 500) held AND the only true bound (max_length)
        # produced a 422. The brief's min_length=8 / 72-byte-422 assumption is recorded as a
        # divergence (the field is not BcryptPassword), surfaced as a PASS_WITH_CONCERN.
        invariant_held = no_5xx and over_max_is_422
        brief_assumption_held = seven_is_422 and overlong_is_422
        print(f"security_invariant_held(no_500 + maxlen_422)={invariant_held}")
        print(f"brief_assumption_held(short&overlong_are_422)={brief_assumption_held}")
        print(f"VERDICT={'PASS' if invariant_held else 'FAIL'}; CONCERN={'NO' if brief_assumption_held else 'YES (no min_length=8 / no <=72-byte bound on platform login password)'}")


asyncio.run(main())
