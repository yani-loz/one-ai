async def main() -> None:
    prefix = "onb45"
    async with _client() as c:
        plat = await platform_login(c)
        stmp = stamp()

        # 30 emoji: 30 chars (< 128 max_length) but each is 4 UTF-8 bytes = 120 bytes > 72.
        multibyte_pw = "\U0001F600" * 30
        cases = {
            "7-char (under min 8)": "Abc123!",
            "200-char (over max 128)": "A" * 200,
            "30-emoji (120 bytes > 72, chars < 128)": multibyte_pw,
        }
        for label, pw in cases.items():
            char_len = len(pw)
            byte_len = len(pw.encode("utf-8"))
            slug = f"{prefix}-{stmp}-{char_len}-{byte_len}"
            email = f"{slug}@oneai.dev"
            r = await onboard_org(
                c, plat, name=f"Org {prefix}", slug=slug, admin_email=email,
                admin_pw=pw,
            )
            print(f"[{label}] chars={char_len} bytes={byte_len} -> status={r.status_code}")
            detail = r.json().get("detail")
            if isinstance(detail, list):
                print("   422 detail loc/type/msg:",
                      [(d.get("loc"), d.get("type"), d.get("msg")) for d in detail])
            else:
                print("   body:", r.json())


asyncio.run(main())
