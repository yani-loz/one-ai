async def main() -> None:
    """TC-PC-014 — request-shape fuzz on /platform/login (extra='forbid').

    PlatformLoginRequest has model_config extra='forbid' and email: NormalizedEmail
    (EmailStr). Assert:
      - an extra unknown field -> 422 (extra_forbidden)
      - missing 'password'     -> 422 (missing)
      - empty-string email     -> 422 (invalid email)
    None may 500; all must be 422 BEFORE any bcrypt/DB work (validation-layer rejection)."""
    cases = {
        "extra_field": {"email": PLATFORM_EMAIL, "password": PLATFORM_PW, "unexpected": "x"},
        "missing_password": {"email": PLATFORM_EMAIL},
        "empty_email": {"email": "", "password": PLATFORM_PW},
        # bonus: null password and wrong-type password (defensive shape checks)
        "null_password": {"email": PLATFORM_EMAIL, "password": None},
        "missing_email": {"password": PLATFORM_PW},
        "empty_body": {},
    }

    async with _client() as c:
        results = {}
        for label, payload in cases.items():
            r = await c.post("/platform/login", json=payload)
            results[label] = r.status_code
            print(f"{label}: status={r.status_code} body={r.text[:220]!r}")

        required_422 = ["extra_field", "missing_password", "empty_email"]
        all_required_422 = all(results[k] == 422 for k in required_422)
        no_5xx = all(v < 500 for v in results.values())
        # critically: an extra field with VALID creds must NOT authenticate (no token leak)
        extra_not_authed = results["extra_field"] == 422

        print(f"all_required_422={all_required_422}")
        print(f"no_5xx_anywhere={no_5xx}")
        print(f"extra_field_rejected_not_authed={extra_not_authed}")

        ok = all_required_422 and no_5xx and extra_not_authed
        print(f"VERDICT={'PASS' if ok else 'FAIL'}")


asyncio.run(main())
