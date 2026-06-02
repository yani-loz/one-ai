/**
 * Unit tests for the company admin HTTP client — list / create / update / deactivate
 * against a mocked global fetch (the adapter boundary). localStorage is cleared so a 401
 * has no refresh token to rotate and surfaces cleanly as AuthRequestError.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthRequestError } from "../identity";
import {
  createCompanyUser,
  deactivateCompanyUser,
  listCompanyUsers,
  updateCompanyUser,
} from "./adminClient";
import type { CreateUserRequest } from "./types";

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

const SAMPLE_USER = {
  id: "u-1",
  email: "anna@acme.de",
  full_name: "Anna Schmidt",
  role: "member",
  is_active: true,
  org_id: "org-1",
  created_at: "2026-06-01T10:00:00Z",
};

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("listCompanyUsers", () => {
  it("test_list_returns_parsed_users_on_200", async () => {
    mockFetch(() => jsonResponse(200, [SAMPLE_USER]));

    const users = await listCompanyUsers();

    expect(users).toHaveLength(1);
    expect(users[0]?.email).toBe("anna@acme.de");
    expect(calls[0]?.url).toContain("/users");
    expect(calls[0]?.method).toBe("GET");
  });

  it("test_list_surfaces_401_as_auth_error", async () => {
    mockFetch(() => jsonResponse(401, {}));

    await expect(listCompanyUsers()).rejects.toMatchObject({ status: 401 });
  });

  it("test_list_surfaces_403_as_auth_error", async () => {
    // A 403 (role downgraded — a company_admin never legitimately gets it) surfaces the status
    // so the controller can treat it like 401 and log out.
    mockFetch(() => jsonResponse(403, {}));

    await expect(listCompanyUsers()).rejects.toMatchObject({ status: 403 });
  });

  it("test_list_throws_auth_error_on_500", async () => {
    mockFetch(() => jsonResponse(500, {}));

    await expect(listCompanyUsers()).rejects.toBeInstanceOf(AuthRequestError);
  });
});

describe("createCompanyUser", () => {
  const payload: CreateUserRequest = {
    email: "anna@acme.de",
    full_name: "Anna Schmidt",
    role: "member",
    password: "Sup3r-Secret!",
  };

  it("test_create_posts_payload_and_returns_user", async () => {
    mockFetch(() => jsonResponse(201, SAMPLE_USER));

    const user = await createCompanyUser(payload);

    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.url).toContain("/users");
    expect(calls[0]?.body).toMatchObject({ email: "anna@acme.de", role: "member" });
    expect(user.id).toBe("u-1");
  });

  it("test_create_throws_409_on_duplicate_email", async () => {
    mockFetch(() => jsonResponse(409, { detail: "exists" }));

    await expect(createCompanyUser(payload)).rejects.toMatchObject({ status: 409 });
  });
});

describe("updateCompanyUser", () => {
  it("test_update_patches_the_role_body", async () => {
    mockFetch(() => jsonResponse(200, { ...SAMPLE_USER, role: "company_admin" }));

    const user = await updateCompanyUser("u-1", { role: "company_admin" });

    expect(calls[0]?.method).toBe("PATCH");
    expect(calls[0]?.url).toContain("/users/u-1");
    expect(calls[0]?.body).toEqual({ role: "company_admin" });
    expect(user.role).toBe("company_admin");
  });

  it("test_update_patches_the_is_active_body", async () => {
    mockFetch(() => jsonResponse(200, { ...SAMPLE_USER, is_active: true }));

    await updateCompanyUser("u-1", { is_active: true });

    expect(calls[0]?.body).toEqual({ is_active: true });
  });

  it("test_update_throws_409_on_last_admin", async () => {
    mockFetch(() => jsonResponse(409, { detail: "last admin" }));

    await expect(updateCompanyUser("u-1", { role: "member" })).rejects.toMatchObject({
      status: 409,
    });
  });
});

describe("deactivateCompanyUser", () => {
  it("test_deactivate_sends_delete_and_resolves_on_204", async () => {
    // 204 has no body: json() REJECTS, so the test fails if the code ever parses it.
    mockFetch(
      () =>
        ({
          ok: true,
          status: 204,
          json: () => Promise.reject(new Error("204 No Content has no body — do not parse")),
        }) as Response,
    );

    await expect(deactivateCompanyUser("u-1")).resolves.toBeUndefined();

    expect(calls[0]?.method).toBe("DELETE");
    expect(calls[0]?.url).toContain("/users/u-1");
  });

  it("test_deactivate_throws_409_on_last_admin", async () => {
    mockFetch(() => jsonResponse(409, { detail: "last admin" }));

    await expect(deactivateCompanyUser("u-1")).rejects.toMatchObject({ status: 409 });
  });

  it("test_deactivate_throws_404_on_unknown_user", async () => {
    mockFetch(() => jsonResponse(404, {}));

    await expect(deactivateCompanyUser("nope")).rejects.toMatchObject({ status: 404 });
  });
});
