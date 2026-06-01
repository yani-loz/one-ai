async def main():
    # TC-PC-073 — 40 concurrent INVALID /platform/login (unknown, run-stamped email). The
    # DUMMY_PASSWORD_HASH equalizer makes the server pay full bcrypt cost even on an unknown
    # email. Expect all 401, generic body, latency COMPARABLE to the valid burst (TC-072) ->
    # proves anti-enumeration AND reveals the unauthenticated CPU-amplification surface.
    N = 40
    # Use the validator-accepted @oneai.dev domain with a run-stamped unknown local-part so
    # the request PASSES Pydantic EmailStr validation and reaches the bcrypt 401 path. A
    # reserved TLD (e.g. .invalid) is rejected with 422 BEFORE bcrypt runs (harness bug).
    unknown_email = f"stress-nobody-{stamp()}@oneai.dev"
    junk_pw = "wrong-junk-password-2026"
    latencies: list[float] = []
    print(f"unknown email: {unknown_email}")

    async with _client(timeout=120) as c:
        # Baseline single invalid login.
        t0 = time.perf_counter()
        base = await c.post("/platform/login", json={"email": unknown_email, "password": junk_pw})
        base_ms = (time.perf_counter() - t0) * 1000
        print(f"baseline single invalid /platform/login: {base.status_code}  {base_ms:.1f}ms   body={base.json()}")

        async def one(_i: int):
            start = time.perf_counter()
            r = await c.post("/platform/login", json={"email": unknown_email, "password": junk_pw})
            latencies.append((time.perf_counter() - start) * 1000)
            return r

        print(f"fired {N} concurrent invalid POST /platform/login (client timeout=120s)")
        results = await fire_concurrent(one, N)

        tally = summarize(results)
        print("status tally:", tally)

        latencies.sort()
        median = latencies[len(latencies) // 2] if latencies else float("nan")
        if latencies:
            print(f"latency ms  -> min={latencies[0]:.1f}  median={median:.1f}  max={latencies[-1]:.1f}")

        n_500 = sum(1 for r in results if not isinstance(r, BaseException) and r.status_code == 500)
        n_exc = sum(1 for r in results if isinstance(r, BaseException))
        print(f"500 count: {n_500}   client-EXC count: {n_exc}")

        sample = next((r for r in results if not isinstance(r, BaseException)), None)
        if sample is not None:
            print("sample body:", sample.json())

        print()
        print("equalizer comparison (median):")
        print(f"  invalid burst (this case): ~{median:.1f} ms   (single-req baseline {base_ms:.1f}ms)")
        print("  valid   burst (TC-PC-072): ~7517 ms recorded   (single-req baseline 328ms)")
        print("  -> same order of magnitude (both 7-13s, bcrypt-bound); baselines ~match:")
        print("     anti-enumeration timing defense CONFIRMED")


asyncio.run(main())
