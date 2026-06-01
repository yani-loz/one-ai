async def main():
    # TC-PC-072 — 40 concurrent VALID /platform/login. bcrypt rounds=12 runs in the anyio
    # worker threadpool; expect latency to balloon but ALL 200, no 500. Baseline for TC-073.
    N = 40
    latencies: list[float] = []
    async with _client(timeout=120) as c:
        # Baseline single login.
        t0 = time.perf_counter()
        base = await c.post("/platform/login", json={"email": PLATFORM_EMAIL, "password": PLATFORM_PW})
        base_ms = (time.perf_counter() - t0) * 1000
        print(f"baseline single valid /platform/login: {base.status_code}  {base_ms:.1f}ms")

        async def one(_i: int):
            start = time.perf_counter()
            r = await c.post("/platform/login", json={"email": PLATFORM_EMAIL, "password": PLATFORM_PW})
            latencies.append((time.perf_counter() - start) * 1000)
            return r

        print(f"fired {N} concurrent valid POST /platform/login (client timeout=120s)")
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
            print("sample body keys:", sorted(ok.json().keys()))


asyncio.run(main())
