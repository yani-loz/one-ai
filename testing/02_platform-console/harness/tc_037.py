async def main():
    async with _client() as c:
        probes = {
            "Bearer not.a.jwt": "not.a.jwt",
            "random opaque string": "Zm9vYmFyYmF6cXV1eA-randomopaque-1234567890",
        }
        results = {}
        for label, raw in probes.items():
            r = await c.get("/platform/me", headers={"Authorization": f"Bearer {raw}"})
            results[label] = r.status_code
            print(f"GARBAGE {label!r} /platform/me -> {r.status_code} {r.text}")
        ok = all(code == 401 for code in results.values())
        no500 = all(code != 500 for code in results.values())
        print(f"assert_all_401_not_500: {'PASS' if ok and no500 else 'FAIL'} {results}")


asyncio.run(main())
