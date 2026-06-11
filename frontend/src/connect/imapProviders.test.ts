/**
 * Unit tests for the pure IMAP-provider auto-detection — known providers (incl. DACH), the
 * imap.<domain> fallback, and the app-password help link.
 */
import { describe, expect, it } from "vitest";

import { appPasswordHelpUrl, detectImapSettings, emailDomain } from "./imapProviders";

describe("emailDomain", () => {
  it("test_lowercases_the_domain", () => {
    expect(emailDomain("Anna@Acme.DE")).toBe("acme.de");
  });

  it("test_returns_null_without_a_dotted_domain", () => {
    expect(emailDomain("anna")).toBeNull();
    expect(emailDomain("anna@localhost")).toBeNull();
  });
});

describe("detectImapSettings", () => {
  it("test_maps_a_known_consumer_provider", () => {
    expect(detectImapSettings("x@gmail.com")).toEqual({
      host: "imap.gmail.com",
      port: 993,
      useSsl: true,
    });
  });

  it("test_maps_a_dach_free_provider", () => {
    expect(detectImapSettings("x@gmx.de")?.host).toBe("imap.gmx.net");
    expect(detectImapSettings("x@web.de")?.host).toBe("imap.web.de");
  });

  it("test_falls_back_to_imap_subdomain_for_business_mail", () => {
    expect(detectImapSettings("x@acme-corp.de")).toEqual({
      host: "imap.acme-corp.de",
      port: 993,
      useSsl: true,
    });
  });

  it("test_returns_null_when_no_domain", () => {
    expect(detectImapSettings("not-an-email")).toBeNull();
  });
});

describe("appPasswordHelpUrl", () => {
  it("test_returns_a_help_link_for_a_known_provider", () => {
    expect(appPasswordHelpUrl("x@gmail.com")).toContain("apppasswords");
  });

  it("test_returns_null_for_a_provider_without_a_specific_guide", () => {
    expect(appPasswordHelpUrl("x@acme-corp.de")).toBeNull();
  });
});
