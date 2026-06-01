async def main() -> None:
    """TC-PC-010 — valid demo platform login → 200 {access_token,refresh_token,token_type},
    and the body MUST NOT carry a `user` field (the platform domain excludes it)."""
    async with _client() as c:
        r = await c.post(
            "/platform/login",
            json={"email": PLATFORM_EMAIL, "password": PLATFORM_PW},
        )
        body = r.json()
        print(f"status={r.status_code}")
        print(f"keys={sorted(body.keys())}")
        print(f"token_type={body.get('token_type')!r}")
        print(f"has_user_field={'user' in body}")
        print(f"user_value={body.get('user', '<<ABSENT>>')!r}")
        # token shapes (do not print full secrets beyond a prefix)
        print(f"access_token_len={len(body.get('access_token',''))}")
        print(f"refresh_token_len={len(body.get('refresh_token',''))}")
        # Decode the access token (verify it is a platform-aud token) — no secret needed for header/claims read
        try:
            claims = jwt.decode(
                body["access_token"], DEV_SECRET, algorithms=[ALG], audience=PLATFORM_AUD
            )
            print(f"access_aud={claims.get('aud')!r} access_role={claims.get('role')!r} access_org_id={claims.get('org_id')!r}")
        except Exception as exc:  # noqa: BLE001 - evidence only
            print(f"decode_error={type(exc).__name__}:{exc}")

        ok = (
            r.status_code == 200
            and set(body.keys()) == {"access_token", "refresh_token", "token_type"}
            and body.get("token_type") == "bearer"
            and "user" not in body
        )
        print(f"VERDICT={'PASS' if ok else 'FAIL'}")


asyncio.run(main())
