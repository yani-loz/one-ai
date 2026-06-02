/**
 * Role: Pure client-side validators for the create-user form, mirroring the backend
 *       Pydantic bounds so the form disables submit before a request can 422.
 * Used by: CreateUserDrawer.tsx (gates the submit button + per-field hints).
 * Depends on: nothing (pure — no React, no network).
 * Key invariants:
 *   - These mirror backend/app/identity/schemas/user_schemas.py: full_name 1..200 chars
 *     with no control characters (SafeName); password 8..128 chars AND <= 72 UTF-8 BYTES
 *     (BcryptPassword — bcrypt 5.x raises above 72 bytes). The BYTE bound is the one that
 *     bites on accented/emoji input well under 128 characters.
 *   - Client validation is a UX nicety, NEVER the security boundary — the backend
 *     re-validates every field. Keep these in sync with the schema when bounds change.
 */

/** bcrypt 5.x raises (not truncates) above this many UTF-8 bytes — the hard password cap. */
const MAX_PASSWORD_BYTES = 72;
const MIN_PASSWORD_CHARS = 8;
const MAX_PASSWORD_CHARS = 128;
const MAX_NAME_CHARS = 200;
const C0_CONTROL_CEILING = 0x20; // code points below this are C0 control characters
const DEL_CODE_POINT = 0x7f;

/** UTF-8 byte length of a string (a multibyte char can exceed 72 bytes under 72 chars). */
function utf8ByteLength(value: string): number {
  return new TextEncoder().encode(value).length;
}

/** Unicode code-point count — matches Python len() (NOT String.length, which counts UTF-16
 *  units, so an astral char like an emoji would otherwise count as 2 and diverge from the
 *  backend's Pydantic min_length/max_length). */
function codePointLength(value: string): number {
  return [...value].length;
}

/** True if the string contains a C0 control character or DEL (matches backend SafeName). */
function hasControlCharacter(value: string): boolean {
  for (const character of value) {
    const codePoint = character.codePointAt(0) ?? 0;
    if (codePoint < C0_CONTROL_CEILING || codePoint === DEL_CODE_POINT) {
      return true;
    }
  }
  return false;
}

/**
 * Validate an email address with a deliberately conservative shape check.
 *
 * Contract: returns true for `local@domain.tld`-shaped input (the backend's EmailStr is the
 * authority; this only gates the button so an obviously-malformed address can't be submitted).
 */
export function isValidEmail(email: string): boolean {
  const trimmed = email.trim();
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed);
}

/**
 * Validate a full name: 1..200 characters and no NUL/C0 control characters or DEL.
 *
 * Contract: mirrors the backend SafeName validator — a control character would 422 server-side.
 */
export function isValidName(name: string): boolean {
  const trimmed = name.trim();
  const length = codePointLength(trimmed);
  if (length < 1 || length > MAX_NAME_CHARS) {
    return false;
  }
  return !hasControlCharacter(trimmed);
}

/**
 * Validate a password: 8..128 characters AND at most 72 UTF-8 bytes.
 *
 * Contract: mirrors the backend BcryptPassword bounds; the byte cap (not the char cap) is
 * the one that catches accented/emoji passwords that are short in characters but long in bytes.
 */
export function isValidPassword(password: string): boolean {
  const length = codePointLength(password);
  if (length < MIN_PASSWORD_CHARS || length > MAX_PASSWORD_CHARS) {
    return false;
  }
  return utf8ByteLength(password) <= MAX_PASSWORD_BYTES;
}

/**
 * Whether the whole create-user form is valid and may be submitted.
 *
 * Contract: true only when email, name, and password all pass. `role` is a closed union,
 * so any selectable value is valid — it is not re-checked here.
 */
export function isCreateUserFormValid(
  email: string,
  fullName: string,
  password: string,
): boolean {
  return isValidEmail(email) && isValidName(fullName) && isValidPassword(password);
}
