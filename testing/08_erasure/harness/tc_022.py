async def _erase_with_pw(c, plat_token, org_id, *, confirm_slug, password,
                         reason="GDPR offboarding (test)"):
    # LIVE ErasureRequest requires a re-auth `password` (the stale _common.erase_org omits it).
    return await c.post(f"/platform/orgs/{org_id}/erase", headers=bearer(plat_token),
                        json={"reason": reason, "confirm_slug": confirm_slug, "password": password})


def _secret_value_hits(body):
    """Detect SECRET VALUES in the export, not benign metadata field names.

    `tokens_deleted` (a count in the org.erased certificate details) and a future
    `legal_hold` etc. are field NAMES, not secrets — strip the known-benign metadata keys
    before scanning so the content-blind check flags a real password/hash/JWT/token VALUE.
    """
    import copy, json
    probe = copy.deepcopy(body)
    benign_keys = {"tokens_deleted", "users_erased", "support_decider_emails_scrubbed",
                   "audit_log_retained"}
    for entry in probe.get("audit", []):
        details = entry.get("details")
        if isinstance(details, dict):
            for k in list(details.keys()):
                if k in benign_keys:
                    details.pop(k)
    blob = json.dumps(probe).lower()
    forbidden = ["password", "hash", "bcrypt", "$2b$", "secret", "eyj", "bearer", "token_hash",
                 PLATFORM_PW.lower(), DEFAULT_PW.lower()]
    return [s for s in forbidden if s in blob]


async def main():
    import json
    async with _client() as c:
        plat = await platform_login(c)

        # 1. Provision org Y.
        org = await provision_company(c, plat, "retain-er022")
        org_id, slug = org["org_id"], org["slug"]
        print("=== TC-ER-022 ===")
        print("ORG_ID:", org_id)
        print("SLUG:", slug)

        # 2. Erase Y (IRREVERSIBLE — our own org only).
        er = await _erase_with_pw(c, plat, org_id, confirm_slug=slug, password=PLATFORM_PW)
        print("ERASE:", er.status_code)
        cert = er.json()
        print("CERT users_erased:", cert.get("users_erased"),
              "tokens_deleted:", cert.get("tokens_deleted"),
              "decider_scrubbed:", cert.get("support_decider_emails_scrubbed"),
              "status:", cert.get("status"))

        # 3. Compliance export AFTER erase — must still build.
        ex = await compliance_export(c, plat, org_id)
        print("EXPORT_AFTER_ERASE:", ex.status_code)
        body = ex.json()
        org_block = body.get("organization", {})
        print("ORG_STATUS:", org_block.get("status"))
        print("ORG_USER_COUNT:", org_block.get("user_count"))
        print("ORG_LEGAL_HOLD:", org_block.get("legal_hold"))
        audit = body.get("audit", [])
        actions = [e["action"] for e in audit]
        print("AUDIT len:", len(audit), "actions:", actions)
        print("HAS_ORG_ERASED:", "org.erased" in actions)
        print("GENERATED_AT PRESENT:", "generated_at" in body)

        # Show the org.erased details so the only 'token' occurrence is visibly a COUNT.
        for e in audit:
            if e["action"] == "org.erased":
                print("ORG_ERASED_DETAILS:", json.dumps(e["details"]))
        hits = _secret_value_hits(body)
        print("SECRET_VALUE_HITS (benign count fields stripped):", hits)
        print("ORG_ID_FOR_PSQL:", org_id)

        ok = (ex.status_code == 200
              and org_block.get("status") == "offboarded"
              and org_block.get("user_count") == 0
              and "org.erased" in actions
              and len(audit) > 0
              and not hits)
        print("RESULT:", "PASS" if ok else "FAIL")


asyncio.run(main())
