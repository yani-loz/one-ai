# Lead re-verification of the FLIPPED headline (TC-ER-032): the new sudo password re-auth
# (commit 13da7fe) blocks the forged-token erase. Posts the erase body directly (the shared
# erase_org helper predates the password field).
#   cat testing/08_erasure/harness/_common.py \
#       testing/08_erasure/harness/_reverify_authz.py | docker compose exec -T backend python -


async def main() -> None:
    async with _client() as c:
        plat, _ = await platform_login_pair(c)

        async def do_erase(token, org_id, slug, pw):
            return await c.post(f"/platform/orgs/{org_id}/erase", headers=bearer(token),
                                json={"reason": "reverify", "confirm_slug": slug, "password": pw})

        # A — forged random-sub platform token → 403 (password step: get_by_id(random)=None)
        z1 = await provision_company(c, plat, "rv32a")
        fe = await do_erase(forge_platform_token(), z1["org_id"], z1["slug"], PLATFORM_PW)
        print(f"A) FORGED random-sub erase: {fe.status_code} '{fe.json().get('detail') if fe.status_code != 200 else ''}' (expect 403 BLOCKED)")

        # B — real demo admin + CORRECT password → 200 (positive control)
        z2 = await provision_company(c, plat, "rv32b")
        ok = await do_erase(plat, z2["org_id"], z2["slug"], PLATFORM_PW)
        print(f"B) REAL admin + correct pw: {ok.status_code} users_erased={ok.json().get('users_erased') if ok.status_code == 200 else ok.json()} (expect 200)")

        # C — real demo admin + WRONG password → 403, org untouched
        z3 = await provision_company(c, plat, "rv32c")
        wr = await do_erase(plat, z3["org_id"], z3["slug"], "wrong-password-xyz")
        det = await get_org_detail(c, plat, z3["org_id"])
        print(f"C) REAL admin + WRONG pw: {wr.status_code} '{wr.json().get('detail')}' ; org status after={det.json().get('status')} (expect 403 / active)")

        # D — no password field → 422 (the field is required)
        z4 = await provision_company(c, plat, "rv32d")
        np = await c.post(f"/platform/orgs/{z4['org_id']}/erase", headers=bearer(plat),
                          json={"reason": "x", "confirm_slug": z4["slug"]})
        print(f"D) erase WITHOUT password field: {np.status_code} (expect 422 — field required)")

        # E — forged token → compliance export → 200 (export has NO password gate; read radius stands)
        z5 = await provision_company(c, plat, "rv33")
        fx = await compliance_export(c, forge_platform_token(), z5["org_id"])
        print(f"E) FORGED token compliance export: {fx.status_code} keys={list(fx.json().keys()) if fx.status_code == 200 else fx.text[:80]} (expect 200 — read still works)")


asyncio.run(main())
