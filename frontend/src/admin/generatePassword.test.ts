/** Unit tests for the strong-password generator (crypto-strong, in-bounds, unambiguous). */
import { describe, expect, it } from "vitest";

import { generatePassword } from "./generatePassword";
import { isValidPassword } from "./userValidation";

const SAMPLES = 25;

describe("generatePassword", () => {
  it("test_default_length_is_16", () => {
    expect(generatePassword()).toHaveLength(16);
  });

  it("test_respects_requested_length", () => {
    expect(generatePassword(24)).toHaveLength(24);
  });

  it("test_output_always_satisfies_isValidPassword", () => {
    for (let i = 0; i < SAMPLES; i++) {
      expect(isValidPassword(generatePassword())).toBe(true);
    }
  });

  it("test_includes_lower_upper_digit_and_symbol", () => {
    for (let i = 0; i < SAMPLES; i++) {
      const pw = generatePassword();
      expect(pw).toMatch(/[a-z]/);
      expect(pw).toMatch(/[A-Z]/);
      expect(pw).toMatch(/[2-9]/);
      expect(pw).toMatch(/[!@#$%^&*_=+?-]/);
    }
  });

  it("test_excludes_visually_ambiguous_characters", () => {
    for (let i = 0; i < SAMPLES; i++) {
      expect(generatePassword()).not.toMatch(/[0O1lI]/);
    }
  });

  it("test_successive_calls_differ", () => {
    expect(generatePassword()).not.toBe(generatePassword());
  });
});
