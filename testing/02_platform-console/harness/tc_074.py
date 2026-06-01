async def main():
    # TC-PC-074 — ~30s sustained mixed load, then assert full recovery + no connection leak.
    # The objective leak check is psql (run from host after this finishes); here we drive the
    # load and verify /health + a single /platform/me return to baseline latency.
    LOAD_SECONDS = 30.0
    WORKERS = 12            # steady concurrent read pressure (well above pool=15 to keep it busy)
    ONBOARDS = 3           # a few real onboards interleaved (each = bcrypt hash + 2 inserts)

    me_tally: dict = {}
    orgs_tally: dict = {}
    onboard_tally: dict = {}
    errors: list = []
    provisioned: list[str] = []

    def bump(d: dict, key) -> None:
        d[key] = d.get(key, 0) + 1

    async with _client(timeout=120) as c:
        plat_access, _plat_refresh = await platform_login_pair(c)

        # Pre-load baseline.
        t0 = time.perf_counter()
        base = await c.get("/platform/me", headers=bearer(plat_access))
        base_ms = (time.perf_counter() - t0) * 1000
        print(f"(harness) pre-load single GET /platform/me baseline: {base.status_code}  {base_ms:.1f}ms")

        deadline = time.perf_counter() + LOAD_SECONDS
        stop = asyncio.Event()

        async def reader(i: int) -> None:
            # Alternate /platform/me and /platform/orgs to mix read shapes.
            n = 0
            while not stop.is_set():
                try:
                    if n % 2 == 0:
                        r = await c.get("/platform/me", headers=bearer(plat_access))
                        bump(me_tally, r.status_code)
                    else:
                        r = await c.get("/platform/orgs", headers=bearer(plat_access))
                        bump(orgs_tally, r.status_code)
                except BaseException as exc:  # capture, never raise (keep the loop alive)
                    errors.append(f"EXC:{type(exc).__name__}")
                n += 1

        async def onboarder() -> None:
            # Space a few real onboards through the window.
            for _ in range(ONBOARDS):
                if stop.is_set():
                    break
                try:
                    info = await provision_company(c, plat_access, prefix="stress-rec")
                    provisioned.append(info["slug"])
                    bump(onboard_tally, 201)
                except httpx.HTTPStatusError as exc:
                    bump(onboard_tally, exc.response.status_code)
                except BaseException as exc:
                    errors.append(f"ONBOARD-EXC:{type(exc).__name__}")
                await asyncio.sleep(LOAD_SECONDS / (ONBOARDS + 1))

        async def timer() -> None:
            while time.perf_counter() < deadline:
                await asyncio.sleep(0.2)
            stop.set()

        tasks = [asyncio.create_task(reader(i)) for i in range(WORKERS)]
        tasks.append(asyncio.create_task(onboarder()))
        tasks.append(asyncio.create_task(timer()))
        loaded_for = time.perf_counter()
        await asyncio.gather(*tasks, return_exceptions=True)
        actual_load = time.perf_counter() - loaded_for

        total_ok = (
            sum(v for k, v in me_tally.items() if k == 200)
            + sum(v for k, v in orgs_tally.items() if k == 200)
            + sum(v for k, v in onboard_tally.items() if k == 201)
        )
        n_500 = sum(v for k, v in {**me_tally, **orgs_tally, **onboard_tally}.items() if k == 500)
        print(f"(harness) drove sustained mixed load for ~{actual_load:.1f}s")
        print(f"  GET  /platform/me   : {me_tally}")
        print(f"  GET  /platform/orgs : {orgs_tally}")
        print(f"  POST /platform/orgs : {onboard_tally}      (provisioned: {provisioned})")
        print(f"  total successful requests: {total_ok}   500s={n_500}   EXC={len(errors)}")
        if errors:
            print(f"  error samples: {errors[:5]}")

        # Recovery checks immediately after the load loop ends.
        print()
        print("(harness) RECOVERY checks (immediately post-load):")
        h = await c.get("/health")
        print(f"  GET /health        : {h.status_code}  {h.json()}")
        t1 = time.perf_counter()
        me = await c.get("/platform/me", headers=bearer(plat_access))
        me_ms = (time.perf_counter() - t1) * 1000
        verdict = "recovered" if me_ms < base_ms * 4 + 50 else "STILL SLOW"
        print(f"  GET /platform/me   : {me.status_code}  {me_ms:.1f}ms   (baseline was {base_ms:.1f}ms -> {verdict})")
        print()
        print("NOW run the host-side leak check (psql), see the TC-074 .md Harness line.")


asyncio.run(main())
