async def main():
    # TC-PC-071 — escalate concurrency 30/60/120/200 on GET /platform/me to find the knee.
    # CRITICAL: a bare httpx client caps at max_connections=100, which would queue the 120/200
    # levels CLIENT-side and mask the server's 15-conn pool. Use a raised-limit client so all
    # requests reach uvicorn — we measure the SERVER, not httpx.
    limits = httpx.Limits(max_connections=500, max_keepalive_connections=500)
    print(f"RAISED-LIMIT client: max_connections=500, max_keepalive_connections=500")

    # One login to get a token (uses a normal short-lived client; under the 100 cap).
    async with _client(timeout=30) as login_c:
        access, _refresh = await platform_login_pair(login_c)

    # Baseline with the raised-limit client.
    async with httpx.AsyncClient(base_url=BASE, timeout=120, limits=limits) as c:
        t0 = time.perf_counter()
        base = await c.get("/platform/me", headers=bearer(access))
        print(f"baseline single GET /platform/me: {base.status_code}  {(time.perf_counter()-t0)*1000:.1f}ms")
    print()

    first_knee = None
    for level in (30, 60, 120, 200):
        latencies: list[float] = []
        # Fresh client per level so keepalive carryover doesn't skew the next level.
        async with httpx.AsyncClient(base_url=BASE, timeout=120, limits=limits) as c:
            async def one(_i: int):
                start = time.perf_counter()
                r = await c.get("/platform/me", headers=bearer(access))
                latencies.append((time.perf_counter() - start) * 1000)
                return r

            results = await fire_concurrent(one, level)

        tally = summarize(results)
        latencies.sort()
        median = latencies[len(latencies) // 2] if latencies else float("nan")
        mx = latencies[-1] if latencies else float("nan")
        n_500 = sum(1 for r in results if not isinstance(r, BaseException) and r.status_code == 500)
        n_exc = sum(1 for r in results if isinstance(r, BaseException))
        print(f"level={level:<4} tally={tally}    median={median:.1f}ms   max={mx:.1f}ms   500s={n_500}  EXC={n_exc}")
        if first_knee is None and (n_500 > 0 or n_exc > 0):
            first_knee = level

    print()
    print(f"first 500 / pool-timeout EXC at level: {first_knee if first_knee is not None else 'NONE (no knee within 200)'}")


asyncio.run(main())
