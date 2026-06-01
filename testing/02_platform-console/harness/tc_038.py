async def main():
    async with _client() as c:
        none_tok = forge_platform_token(alg="none")
        # A minimal valid onboard body so a 401 cannot be confused with a 422 (validation).
        body = {
            "org_name": "Gate Probe Org",
            "org_slug": f"paz-gate-{stamp()}",
            "admin_email": f"paz-gate-{stamp()}@oneai.dev",
            "admin_full_name": "Gate Probe Admin",
            "admin_password": DEFAULT_PW,
        }
        results = {}

        # GET /platform/orgs — missing bearer + alg=none
        r = await c.get("/platform/orgs")
        results["GET orgs no-bearer"] = r.status_code
        print(f"GET /platform/orgs NO-BEARER -> {r.status_code} {r.text[:200]}")

        r = await c.get("/platform/orgs", headers=bearer(none_tok))
        results["GET orgs alg=none"] = r.status_code
        print(f"GET /platform/orgs ALG=NONE -> {r.status_code} {r.text[:200]}")

        # POST /platform/orgs — missing bearer + alg=none (body valid, so reject is auth not 422)
        r = await c.post("/platform/orgs", json=body)
        results["POST orgs no-bearer"] = r.status_code
        print(f"POST /platform/orgs NO-BEARER -> {r.status_code} {r.text[:200]}")

        r = await c.post("/platform/orgs", headers=bearer(none_tok), json=body)
        results["POST orgs alg=none"] = r.status_code
        print(f"POST /platform/orgs ALG=NONE -> {r.status_code} {r.text[:200]}")

        ok = all(code == 401 for code in results.values())
        print(f"assert_all_401: {'PASS' if ok else 'FAIL'} {results}")
        # Confirm nothing was created under our gate-probe stamp (no fail-open write).


asyncio.run(main())
