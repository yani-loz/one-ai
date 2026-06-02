async def _erase_with_pw(c, plat_token, org_id, *, confirm_slug, password,
                         reason="GDPR offboarding (test)"):
    # The LIVE server's ErasureRequest requires a re-auth `password` (a stronger guard than the
    # on-disk 2-field schema / the stale _common.erase_org helper). POST directly with it.
    return await c.post(f"/platform/orgs/{org_id}/erase", headers=bearer(plat_token),
                        json={"reason": reason, "confirm_slug": confirm_slug, "password": password})


async def main():
    async with _client() as c:
        plat = await platform_login(c)

        # 1. Provision a fresh run-stamped org R (org.onboard + auth.login.success).
        org = await provision_company(c, plat, "retain-er020")
        org_id, slug = org["org_id"], org["slug"]
        print("=== TC-ER-020 ===")
        print("ORG_ID:", org_id)
        print("SLUG:", slug)

        # 2. Support request + approve (support.requested + support.approved).
        rs = await request_support(c, plat, org_id)
        grant_id = rs.json()["id"]
        print("REQUEST_SUPPORT:", rs.status_code)
        ap = await company_approve(c, org["admin_access"], grant_id)
        print("APPROVE:", ap.status_code, "decided_by_email:", ap.json().get("decided_by_email"))

        # 3. Status PATCH last, just before erase (org.suspend).
        ps = await patch_status(c, plat, org_id, "suspended")
        print("PATCH_STATUS:", ps.status_code, "status:", ps.json().get("status"))

        # 4. Pre-erase audit snapshot.
        pre = await get_org_audit(c, plat, org_id)
        pre_actions = [e["action"] for e in pre.json()]
        print("PRE_ERASE_AUDIT:", pre.status_code, "actions:", pre_actions)

        # 5. Erase R (IRREVERSIBLE — our own org only).
        er = await _erase_with_pw(c, plat, org_id, confirm_slug=slug, password=PLATFORM_PW)
        print("ERASE:", er.status_code)
        cert = er.json()
        print("CERT users_erased:", cert.get("users_erased"),
              "tokens_deleted:", cert.get("tokens_deleted"),
              "decider_scrubbed:", cert.get("support_decider_emails_scrubbed"),
              "audit_log_retained:", cert.get("audit_log_retained"))

        # 6. Post-erase audit — pre-erase rows must survive + org.erased appended.
        post = await get_org_audit(c, plat, org_id)
        post_actions = [e["action"] for e in post.json()]
        print("POST_ERASE_AUDIT:", post.status_code, "actions:", post_actions)
        survived = all(a in post_actions for a in pre_actions)
        has_erased = "org.erased" in post_actions
        print("PRE_ROWS_SURVIVED:", survived)
        print("HAS_ORG_ERASED:", has_erased)
        for e in post.json():
            if e["action"] == "auth.login.success":
                print("RETAINED_LOGIN actor_email:", e.get("actor_email"),
                      "ip:", e.get("ip_address"))
        print("ORG_ID_FOR_PSQL:", org_id)
        print("RESULT:", "PASS" if (survived and has_erased and er.status_code == 200) else "FAIL")


asyncio.run(main())
