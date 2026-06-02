/**
 * Role: Cryptographically-strong password generator for the create-user flow — produces a
 *       value that satisfies the backend bounds and avoids visually ambiguous characters.
 * Used by: CreateUserDrawer.tsx (the "Generate" affordance).
 * Depends on: the Web Crypto API (globalThis.crypto.getRandomValues) — no libraries.
 * Key invariants:
 *   - Uses crypto.getRandomValues (NEVER Math.random) — these are real credentials.
 *   - Output is pure ASCII and 16 chars by default, so it is always within isValidPassword
 *     bounds (8..128 chars AND <= 72 bytes), and it is guaranteed to contain a lowercase,
 *     uppercase, digit, and symbol so it also passes a future complexity policy.
 *   - Visually ambiguous glyphs (0/O, 1/l/I) are excluded so the value survives being read
 *     aloud or transcribed during hand-off.
 */

const LOWER = "abcdefghijkmnpqrstuvwxyz"; // no 'l'
const UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"; // no 'I', 'O'
const DIGITS = "23456789"; // no '0', '1'
const SYMBOLS = "!@#$%^&*-_=+?";
const ALL = LOWER + UPPER + DIGITS + SYMBOLS;
const DEFAULT_LENGTH = 16;

/** Uniform random int in [0, maxExclusive) from the CSPRNG, rejection-sampled to drop modulo bias. */
function secureRandomInt(maxExclusive: number): number {
  const buffer = new Uint32Array(1);
  const limit = Math.floor(0x1_0000_0000 / maxExclusive) * maxExclusive;
  let value: number;
  do {
    globalThis.crypto.getRandomValues(buffer);
    value = buffer[0];
  } while (value >= limit);
  return value % maxExclusive;
}

/** Pick one random character from a character set. */
function pick(set: string): string {
  return set[secureRandomInt(set.length)];
}

/**
 * Generate a strong random password.
 *
 * Contract: returns a `length`-char (default 16) ASCII password containing at least one
 * lowercase, uppercase, digit, and symbol, shuffled (Fisher–Yates over the CSPRNG) so the
 * guaranteed characters are not in fixed positions. The result always satisfies isValidPassword.
 */
export function generatePassword(length: number = DEFAULT_LENGTH): string {
  const chars = [pick(LOWER), pick(UPPER), pick(DIGITS), pick(SYMBOLS)];
  while (chars.length < length) {
    chars.push(pick(ALL));
  }
  for (let i = chars.length - 1; i > 0; i--) {
    const j = secureRandomInt(i + 1);
    [chars[i], chars[j]] = [chars[j], chars[i]];
  }
  return chars.join("");
}
