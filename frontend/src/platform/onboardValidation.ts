/**
 * Role: Pure validation helpers for the onboard form — slug derivation from a company
 *       name, and a field-bounds guard mirroring the backend Pydantic validators.
 * Used by: platform/OnboardCompanyDrawer.tsx.
 * Depends on: nothing (pure functions, no React/DOM).
 * Key invariants:
 *   - slugify always returns a string matching ^[a-z0-9-]*$ (possibly empty), ≤100 chars.
 *   - isOnboardFormValid mirrors the server bounds as a cheap pre-submit convenience; the
 *     backend re-validates and remains the authority (this is defense-in-depth, not it).
 */

/** Derive a URL-safe slug from a company name: lowercase, dashes, valid leading char. */
export function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+/, "")
    .replace(/-+$/, "")
    .slice(0, 100);
}

/**
 * True when the whole onboard form satisfies the backend field bounds: org + admin name
 * non-empty, slug matches ^[a-z0-9][a-z0-9-]*$ (≤100), a syntactically valid email, and
 * password 8..128 chars. Email is included so an empty/malformed address cannot pass the
 * guard and return a confusing 422 mapped to a connectivity error.
 */
export function isOnboardFormValid(
  orgName: string,
  orgSlug: string,
  adminName: string,
  adminEmail: string,
  password: string,
): boolean {
  return (
    orgName.trim().length > 0 &&
    /^[a-z0-9][a-z0-9-]*$/.test(orgSlug) &&
    orgSlug.length <= 100 &&
    adminName.trim().length > 0 &&
    /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(adminEmail.trim()) &&
    password.length >= 8 &&
    password.length <= 128
  );
}
