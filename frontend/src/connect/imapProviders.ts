/**
 * Role: Pure IMAP-provider auto-detection — turn an email address into its IMAP server settings
 *       (host/port/SSL) so the user only types their email, not server details; plus a
 *       provider-specific "how to create an app password" help link.
 * Used by: AddMailboxDrawer (prefill host/port + the help link).
 * Depends on: nothing (pure, leaf — directly unit-tested).
 * Key invariants:
 *   - Known providers (incl. the major DACH free providers) map to their real IMAP host; anything
 *     else falls back to `imap.<domain>:993` SSL — a correct guess for most hosted/business mail,
 *     and the user can still override the prefilled fields.
 *   - Returns null only when the email has no usable domain; callers treat null as "no prefill".
 */

interface ImapSettings {
  host: string;
  port: number;
  useSsl: boolean;
}

/** domain → IMAP host. Major consumer providers + the DACH free providers (gmx/web.de/t-online). */
const KNOWN_HOSTS: Record<string, string> = {
  "gmail.com": "imap.gmail.com",
  "googlemail.com": "imap.gmail.com",
  "outlook.com": "outlook.office365.com",
  "hotmail.com": "outlook.office365.com",
  "hotmail.de": "outlook.office365.com",
  "live.com": "outlook.office365.com",
  "live.de": "outlook.office365.com",
  "office365.com": "outlook.office365.com",
  "yahoo.com": "imap.mail.yahoo.com",
  "yahoo.de": "imap.mail.yahoo.com",
  "icloud.com": "imap.mail.me.com",
  "me.com": "imap.mail.me.com",
  "gmx.de": "imap.gmx.net",
  "gmx.net": "imap.gmx.net",
  "gmx.com": "imap.gmx.net",
  "web.de": "imap.web.de",
  "t-online.de": "secureimap.t-online.de",
  "freenet.de": "mx.freenet.de",
  "mailbox.org": "imap.mailbox.org",
  "posteo.de": "posteo.de",
};

/** Provider → the page where a user generates an app-specific password (2FA usually required). */
const APP_PASSWORD_HELP: Record<string, string> = {
  "imap.gmail.com": "https://myaccount.google.com/apppasswords",
  "outlook.office365.com": "https://account.live.com/proofs/AppPassword",
  "imap.mail.yahoo.com": "https://login.yahoo.com/account/security/app-passwords",
  "imap.mail.me.com": "https://support.apple.com/en-us/102654",
};

/** The lowercased domain of an email address, or null if it has no usable domain part. */
export function emailDomain(email: string): string | null {
  const at = email.lastIndexOf("@");
  if (at < 0) return null;
  const domain = email.slice(at + 1).trim().toLowerCase();
  return domain.length > 0 && domain.includes(".") ? domain : null;
}

/**
 * Detect IMAP settings for an email address.
 *
 * Contract: returns the known host for a recognised provider, else `imap.<domain>:993` SSL as a
 * sensible default; returns null only when the email has no usable domain. Always SSL/993 — the
 * universal modern IMAP default (the user can still override the prefilled fields).
 */
export function detectImapSettings(email: string): ImapSettings | null {
  const domain = emailDomain(email);
  if (domain === null) return null;
  return { host: KNOWN_HOSTS[domain] ?? `imap.${domain}`, port: 993, useSsl: true };
}

/** The app-password help URL for an email's provider, or null if we don't have a specific one. */
export function appPasswordHelpUrl(email: string): string | null {
  const settings = detectImapSettings(email);
  return settings === null ? null : (APP_PASSWORD_HELP[settings.host] ?? null);
}
