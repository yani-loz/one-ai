async def main() -> None:
    prefix = "onb44"
    async with _client() as c:
        plat = await platform_login(c)
        stmp = stamp()

        # Each invalid slug must be rejected at the Pydantic boundary -> 422.
        # pattern ^[a-z0-9][a-z0-9-]*$, min_length=1, max_length=100.
        cases = {
            "uppercase 'Bad'": "Bad",
            "leading-hyphen '-bad'": "-bad",
            "space 'a b'": "a b",
            "empty ''": "",
            "101-char (over max)": "a" * 101,
        }
        for label, bad_slug in cases.items():
            email = f"{prefix}-{stmp}-{abs(hash(label)) % 100000}@oneai.dev"
            r = await onboard_org(
                c, plat, name=f"Org {prefix}", slug=bad_slug, admin_email=email
            )
            detail = r.json().get("detail")
            # Trim huge slug detail for readability.
            print(f"[{label}] len={len(bad_slug)} -> status={r.status_code}")
            if r.status_code == 422 and isinstance(detail, list):
                print("   422 detail loc/type:",
                      [(d.get("loc"), d.get("type")) for d in detail])
            else:
                print("   body:", r.json())


asyncio.run(main())
