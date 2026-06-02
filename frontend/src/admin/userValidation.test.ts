/** Unit tests for the create-user client-side validators (mirror the backend bounds). */
import { describe, expect, it } from "vitest";

import {
  isCreateUserFormValid,
  isValidEmail,
  isValidName,
  isValidPassword,
} from "./userValidation";

describe("isValidEmail", () => {
  it("test_well_formed_email_is_valid", () => {
    expect(isValidEmail("anna@acme.de")).toBe(true);
  });

  it("test_email_is_trimmed_before_checking", () => {
    expect(isValidEmail("  anna@acme.de  ")).toBe(true);
  });

  it("test_email_without_at_is_invalid", () => {
    expect(isValidEmail("anna.acme.de")).toBe(false);
  });

  it("test_email_without_domain_dot_is_invalid", () => {
    expect(isValidEmail("anna@acme")).toBe(false);
  });
});

describe("isValidName", () => {
  it("test_normal_name_is_valid", () => {
    expect(isValidName("Anna Schmidt")).toBe(true);
  });

  it("test_empty_or_whitespace_name_is_invalid", () => {
    expect(isValidName("")).toBe(false);
    expect(isValidName("   ")).toBe(false);
  });

  it("test_name_at_200_chars_is_valid_but_201_is_not", () => {
    expect(isValidName("a".repeat(200))).toBe(true);
    expect(isValidName("a".repeat(201))).toBe(false);
  });

  it("test_name_with_control_character_is_invalid", () => {
    expect(isValidName("Anna" + String.fromCharCode(1) + "Schmidt")).toBe(false);
  });

  it("test_name_length_counts_astral_code_points_not_utf16_units", () => {
    // Each astral emoji is ONE code point (matching Python len()), though 2 UTF-16 units. 200
    // code points is valid; 201 is over the SafeName max — String.length would miscount both.
    const emoji = String.fromCodePoint(0x1f600);
    expect(isValidName(emoji.repeat(200))).toBe(true);
    expect(isValidName(emoji.repeat(201))).toBe(false);
  });
});

describe("isValidPassword", () => {
  it("test_password_under_8_chars_is_invalid", () => {
    expect(isValidPassword("short")).toBe(false);
  });

  it("test_password_at_8_chars_is_valid", () => {
    expect(isValidPassword("12345678")).toBe(true);
  });

  it("test_password_over_128_chars_is_invalid", () => {
    expect(isValidPassword("a".repeat(129))).toBe(false);
  });

  it("test_multibyte_password_at_72_bytes_is_valid", () => {
    // 36 × U+00E9 (é) = 36 chars but 72 UTF-8 bytes — exactly the bcrypt byte cap.
    expect(isValidPassword("é".repeat(36))).toBe(true);
  });

  it("test_multibyte_password_over_72_bytes_is_invalid", () => {
    // 37 × é = 74 bytes (well under 128 chars) — the char check would miss this; the byte
    // check must catch it.
    expect(isValidPassword("é".repeat(37))).toBe(false);
  });

  it("test_astral_emoji_password_under_8_code_points_is_invalid", () => {
    // 4 astral emoji = 4 code points (backend len() == 4 → 422) but 8 UTF-16 units, so a naive
    // String.length check would wrongly pass it. The gate must count code points.
    expect(isValidPassword(String.fromCodePoint(0x1f600).repeat(4))).toBe(false);
  });
});

describe("isCreateUserFormValid", () => {
  it("test_all_fields_valid_returns_true", () => {
    expect(isCreateUserFormValid("anna@acme.de", "Anna", "Sup3r-Secret!")).toBe(true);
  });

  it("test_any_invalid_field_returns_false", () => {
    expect(isCreateUserFormValid("bad-email", "Anna", "Sup3r-Secret!")).toBe(false);
    expect(isCreateUserFormValid("anna@acme.de", "", "Sup3r-Secret!")).toBe(false);
    expect(isCreateUserFormValid("anna@acme.de", "Anna", "short")).toBe(false);
  });
});
