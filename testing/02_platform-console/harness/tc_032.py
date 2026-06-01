async def main():
    async with _client() as c:
        # Mint a VALID platform token (real demo admin sub so only the signature is wrong).
        valid = forge_platform_token(sub="609f2b17-bee9-4f7f-a26d-cb08f666497a")
        # Sanity: the untampered token must work (200) so the only variable is the flipped char.
        r_ok = await c.get("/platform/me", headers=bearer(valid))
        print(f"CONTROL valid token /platform/me -> {r_ok.status_code} {r_ok.text}")

        header_b64, payload_b64, sig_b64 = valid.split(".")
        # Flip one char in the signature segment (avoid no-op: pick a different char).
        first = sig_b64[0]
        flipped = "A" if first != "A" else "B"
        tampered = f"{header_b64}.{payload_b64}.{flipped}{sig_b64[1:]}"
        print(f"sig first char: {first!r} -> {flipped!r}")

        r = await c.get("/platform/me", headers=bearer(tampered))
        print(f"TAMPERED-SIG /platform/me -> {r.status_code} {r.text}")
        ok = r.status_code == 401 and r_ok.status_code == 200
        print(f"assert_control200_and_tampered401: {'PASS' if ok else 'FAIL'}")


asyncio.run(main())
