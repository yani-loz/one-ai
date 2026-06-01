# Lead re-verification of the headline TC-OL-003 (the one result taken from the aborted run).
# A = definitive black-box (does the SAME pre-suspension refresh survive the 403?).
# B = psql corroboration (stop at the 403, leave the token unconsumed so revoked_at can be read).
#   cat testing/05_org-lifecycle/harness/_common.py \
#       testing/05_org-lifecycle/harness/_reverify_003.py | docker compose exec -T backend python -


async def main() -> None:
    async with _client() as c:
        plat, _ = await platform_login_pair(c)

        # A — black-box: present the SAME token before (403) and after reactivation (expect 200 rotated).
        a = await provision_company(c, plat, "rv003a")
        raw_a = a["admin_refresh"]
        await patch_status(c, plat, a["org_id"], STATUS_SUSPENDED)
        a1 = await c.post("/auth/refresh", json={"refresh_token": raw_a})
        await patch_status(c, plat, a["org_id"], STATUS_ACTIVE)
        a2 = await c.post("/auth/refresh", json={"refresh_token": raw_a})
        new_a = a2.json().get("refresh_token") if a2.status_code == 200 else None
        print(f"[A black-box] suspended present={a1.status_code} (403) -> reactivate -> SAME token present="
              f"{a2.status_code} (200) rotated={bool(new_a) and new_a != raw_a}")

        # B — psql corroboration: stop at the 403, do NOT reactivate/rotate, so revoked_at is readable.
        b = await provision_company(c, plat, "rv003b")
        raw_b = b["admin_refresh"]
        await patch_status(c, plat, b["org_id"], STATUS_SUSPENDED)
        b1 = await c.post("/auth/refresh", json={"refresh_token": raw_b})
        print(f"[B psql-probe] suspended present={b1.status_code} (403); HASH={sha256_hex(raw_b)}")


asyncio.run(main())
