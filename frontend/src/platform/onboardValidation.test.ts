/** Unit tests for the onboard form validators — slug derivation and the field-bounds
 *  guard (mirrors the backend Pydantic bounds: slug pattern, email shape, password 8..128). */
import { describe, expect, it } from "vitest";

import { isOnboardFormValid, slugify } from "./onboardValidation";

describe("slugify", () => {
  it("test_lowercases_and_dashes_non_alphanumerics", () => {
    expect(slugify("Müller Maschinenbau GmbH")).toBe("m-ller-maschinenbau-gmbh");
  });

  it("test_strips_leading_and_trailing_dashes", () => {
    expect(slugify("  --Acme!!  ")).toBe("acme");
  });

  it("test_caps_length_at_100", () => {
    expect(slugify("a".repeat(150)).length).toBe(100);
  });
});

describe("isOnboardFormValid", () => {
  const valid = (): Parameters<typeof isOnboardFormValid> => [
    "Acme GmbH",
    "acme",
    "Anna Schmidt",
    "anna@acme.de",
    "Sup3r-Secret!",
  ];

  it("test_accepts_a_fully_valid_form", () => {
    expect(isOnboardFormValid(...valid())).toBe(true);
  });

  it("test_rejects_slug_with_leading_dash", () => {
    const args = valid();
    args[1] = "-bad";
    expect(isOnboardFormValid(...args)).toBe(false);
  });

  it("test_rejects_empty_or_malformed_email", () => {
    const blank = valid();
    blank[3] = "";
    expect(isOnboardFormValid(...blank)).toBe(false);

    const malformed = valid();
    malformed[3] = "not-an-email";
    expect(isOnboardFormValid(...malformed)).toBe(false);
  });

  it("test_rejects_password_under_eight_chars", () => {
    const args = valid();
    args[4] = "short";
    expect(isOnboardFormValid(...args)).toBe(false);
  });
});
