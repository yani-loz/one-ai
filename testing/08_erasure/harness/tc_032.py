async def main():
    async with _client() as c:
        plat = await platform_login(c)

        # ---- Part A: FORGED token (random sub) tries to erase fresh throwaway org Z ----
        # The shared erase_org() helper cannot send `password` (the LIVE schema now requires
        # it), so POST the erase body directly here with the now-required password field.
        z = await provision_company(c, plat, "authz-forge")
        print("Z_SLUG", z["slug"])
        print("Z_ORG_ID", z["org_id"])

        forged_platform = forge_platform_token()  # random sub, dev secret, valid exp
        forged_erase = await c.post(
            f"/platform/orgs/{z['org_id']}/erase",
            headers=bearer(forged_platform),
            json={"reason": "GDPR offboarding (test)", "confirm_slug": z["slug"],
                  "password": "any-password-the-forger-guesses"},
        )
        print("FORGED_ERASE_STATUS", forged_erase.status_code)
        print("FORGED_ERASE_BODY", forged_erase.text)

        # ---- Part B: POSITIVE CONTROL — real super admin + correct password erases Z ----
        # Isolates the 403 above as specifically the password/identity gate (the erase path
        # itself works end-to-end). This is credential-compromise, NOT pure secret-leak.
        real_erase = await c.post(
            f"/platform/orgs/{z['org_id']}/erase",
            headers=bearer(plat),
            json={"reason": "GDPR offboarding (test)", "confirm_slug": z["slug"],
                  "password": PLATFORM_PW},
        )
        print("REAL_ERASE_STATUS", real_erase.status_code)
        print("REAL_ERASE_BODY", real_erase.text)

        print("PSQL_TARGET_SLUG", z["slug"])
        print("PSQL_TARGET_ORG_ID", z["org_id"])

asyncio.run(main())
