async def main():
    # TC-PC-070 — 60 concurrent GET /platform/me against the 15-conn pool (4x oversubscribed).
    # PASS if all 200 with a visible latency spread; FAIL on any 500 or client EXC.
    N = 60
    latencies: list[float] = []
    async with _client(timeout=120) as c:
        access, _refresh = await platform_login_pair(c)

        t0 = time.perf_counter()
        base = await c.get("/platform/me", headers=bearer(access))
        base_ms = (time.perf_counter() - t0) * 1000
        print(f"baseline single GET /platform/me: {base.status_code}  {base_ms:.1f}ms")

        async def one(_i: int):
            start = time.perf_counter()
            r = await c.get("/platform/me", headers=bearer(access))
            latencies.append((time.perf_counter() - start) * 1000)
            return r

        print(f"fired {N} concurrent GET /platform/me (client timeout=120s)")
        results = await fire_concurrent(one, N)

        tally = summarize(results)
        print("status tally:", tally)

        latencies.sort()
        if latencies:
            mid = latencies[len(latencies) // 2]
            print(f"latency ms  -> min={latencies[0]:.1f}  median={mid:.1f}  max={latencies[-1]:.1f}")

        n_500 = sum(1 for r in results if not isinstance(r, BaseException) and r.status_code == 500)
        n_exc = sum(1 for r in results if isinstance(r, BaseException))
        print(f"500 count: {n_500}   client-EXC count: {n_exc}")

        ok = next((r for r in results if not isinstance(r, BaseException) and r.status_code == 200), None)
        if ok is not None:
            print("sample body:", ok.json())


asyncio.run(main())
