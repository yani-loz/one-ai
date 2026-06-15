/**
 * Unit tests for the platform HTTP client — list + onboard against a mocked global
 * fetch (the adapter boundary). localStorage is cleared so a 401 has no refresh token to
 * rotate and surfaces cleanly as AuthRequestError.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthRequestError } from "../identity";
import {
  getOrganization,
  listOrganizations,
  onboardCompany,
  updateOrganizationLegalHold,
  updateOrganizationStatus,
} from "./platformClient";
import type { OnboardCompanyRequest } from "./types";

interface Recorded {
  url: string;
  method: string;
  body: Record<string, unknown> | null;
}

let calls: Recorded[];

function mockFetch(handler: (recorded: Recorded) => Response): void {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const recorded: Recorded = {
        url: String(input),
        method: init?.method ?? "GET",
        body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
      };
      calls.push(recorded);
      return Promise.resolve(handler(recorded));
    }) as unknown as typeof fetch,
  );
}

function jsonResponse(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
  } as Response;
}

const SAMPLE_ORG = {
  id: "org-1",
  name: "Acme GmbH",
  slug: "acme",
  status: "active",
  user_count: 3,
  created_at: "2026-06-01T10:00:00Z",
};

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("listOrganizations", () => {
  it("test_list_returns_parsed_metadata_on_200", async () => {
    mockFetch(() => jsonResponse(200, [SAMPLE_ORG]));

    const orgs = await listOrganizations();

    expect(orgs).toHaveLength(1);
    expect(orgs[0]?.slug).toBe("acme");
    expect(calls[0]?.url).toContain("/platform/orgs");
  });

  it("test_list_throws_auth_error_on_500", async () => {
    mockFetch(() => jsonResponse(500, {}));

    await expect(listOrganizations()).rejects.toBeInstanceOf(AuthRequestError);
  });

  it("test_list_surfaces_401_as_auth_error", async () => {
    // No session is established (no platformLogin), so the in-memory platform refresh token is
    // null and the company cookie path 401s too — the 401 surfaces cleanly as
    // AuthRequestError(401) (the caller logs out).
    mockFetch(() => jsonResponse(401, {}));

    await expect(listOrganizations()).rejects.toMatchObject({ status: 401 });
  });
});

describe("onboardCompany", () => {
  const payload: OnboardCompanyRequest = {
    org_name: "Acme GmbH",
    org_slug: "acme",
    admin_full_name: "Anna Schmidt",
    admin_email: "anna@acme.de",
    admin_password: "Sup3r-Secret!",
  };

  it("test_onboard_posts_payload_and_returns_result", async () => {
    mockFetch(() =>
      jsonResponse(201, {
        organization: SAMPLE_ORG,
        admin: {
          id: "u-1",
          email: "anna@acme.de",
          full_name: "Anna Schmidt",
          role: "company_admin",
          org_id: "org-1",
          created_at: "2026-06-01T10:00:00Z",
        },
      }),
    );

    const result = await onboardCompany(payload);

    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.url).toContain("/platform/orgs");
    expect(calls[0]?.body).toMatchObject({ org_slug: "acme", admin_email: "anna@acme.de" });
    expect(result.organization.id).toBe("org-1");
    expect(result.admin.email).toBe("anna@acme.de");
  });

  it("test_onboard_throws_409_on_duplicate", async () => {
    mockFetch(() => jsonResponse(409, { detail: "slug taken" }));

    await expect(onboardCompany(payload)).rejects.toMatchObject({ status: 409 });
  });
});

describe("organization detail + lifecycle", () => {
  const DETAIL = {
    id: "org-1",
    name: "Acme",
    slug: "acme",
    status: "active",
    user_count: 3,
    legal_hold: false,
    created_at: "2026-06-01T10:00:00Z",
  };

  it("test_get_organization_returns_detail", async () => {
    mockFetch(() => jsonResponse(200, DETAIL));

    const org = await getOrganization("org-1");

    expect(calls[0]?.url).toContain("/platform/orgs/org-1");
    expect(org.legal_hold).toBe(false);
  });

  it("test_get_organization_unknown_throws_404", async () => {
    mockFetch(() => jsonResponse(404, {}));

    await expect(getOrganization("nope")).rejects.toMatchObject({ status: 404 });
  });

  it("test_update_status_patches_the_status_body", async () => {
    mockFetch(() => jsonResponse(200, { ...DETAIL, status: "suspended" }));

    const org = await updateOrganizationStatus("org-1", "suspended");

    expect(calls[0]?.method).toBe("PATCH");
    expect(calls[0]?.url).toContain("/platform/orgs/org-1/status");
    expect(calls[0]?.body).toEqual({ status: "suspended" });
    expect(org.status).toBe("suspended");
  });

  it("test_update_legal_hold_patches_the_flag", async () => {
    mockFetch(() => jsonResponse(200, { ...DETAIL, legal_hold: true }));

    const org = await updateOrganizationLegalHold("org-1", true);

    expect(calls[0]?.method).toBe("PATCH");
    expect(calls[0]?.url).toContain("/platform/orgs/org-1/legal-hold");
    expect(calls[0]?.body).toEqual({ legal_hold: true });
    expect(org.legal_hold).toBe(true);
  });
});
