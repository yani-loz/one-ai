/** Unit tests for the agent insignia spec generator — determinism + the rank grammar. */
import { describe, expect, it } from "vitest";

import { generateApexInsignia, generateInsignia, seedFromString } from "./generateInsignia";

describe("generateInsignia", () => {
  it("test_same_inputs_produce_identical_spec", () => {
    expect(generateInsignia(11, "retrieval", 3)).toEqual(generateInsignia(11, "retrieval", 3));
  });

  it("test_different_seeds_produce_different_specs", () => {
    expect(generateInsignia(11, "retrieval", 3)).not.toEqual(generateInsignia(12, "retrieval", 3));
  });

  it("test_branch_and_rank_are_preserved", () => {
    const spec = generateInsignia(5, "action", 2);
    expect(spec.branch).toBe("action");
    expect(spec.rank).toBe(2);
  });

  it("test_symmetry_in_three_to_six", () => {
    for (let seed = 0; seed < 40; seed++) {
      const { symmetry } = generateInsignia(seed, "analysis", 1);
      expect(symmetry).toBeGreaterThanOrEqual(3);
      expect(symmetry).toBeLessThanOrEqual(6);
    }
  });

  it("test_rank_two_plus_always_has_outer_nodes_and_core", () => {
    for (let seed = 0; seed < 40; seed++) {
      const spec = generateInsignia(seed, "retrieval", 2);
      expect(spec.hasOuterNodes).toBe(true);
      expect(spec.hasCore).toBe(true);
    }
  });

  it("test_rank_three_uses_web_connection", () => {
    for (let seed = 0; seed < 40; seed++) {
      expect(generateInsignia(seed, "orchestrator", 3).connection).toBe("web");
    }
  });
});

describe("seedFromString", () => {
  it("test_same_string_yields_same_seed", () => {
    const id = "00000000-0000-0000-0000-000000000001";
    expect(seedFromString(id)).toBe(seedFromString(id));
  });

  it("test_different_strings_yield_different_seeds", () => {
    expect(seedFromString("alice")).not.toBe(seedFromString("bob"));
  });

  it("test_seed_is_a_non_negative_32_bit_integer", () => {
    const seed = seedFromString("any-user-id");
    expect(Number.isInteger(seed)).toBe(true);
    expect(seed).toBeGreaterThanOrEqual(0);
    expect(seed).toBeLessThanOrEqual(0xffffffff);
  });
});

describe("generateApexInsignia", () => {
  it("test_apex_is_a_grand_six_fold_orchestrator", () => {
    const apex = generateApexInsignia(42);

    expect(apex.branch).toBe("orchestrator");
    expect(apex.rank).toBe(3);
    expect(apex.symmetry).toBe(6);
    expect(apex.connection).toBe("web");
    expect(apex.hasCore).toBe(true);
    expect(apex.hasOuterNodes).toBe(true);
  });
});
