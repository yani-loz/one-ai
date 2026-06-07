/**
 * Tests for the company-admin console page — load/empty/error/retry/401, search, the add-user
 * drawer, and the mutation flows (deactivate behind a confirm, last-admin 409 surfaced, inline
 * role change, and self-demotion → logout). Global fetch is mocked at the boundary, branching
 * on URL + method; the session is injected via a controlled AuthContext (a company_admin).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AuthContext, type AuthContextValue } from "../identity/authContext";
import type { AuthUser } from "../identity";
import { AdminConsolePage } from "./AdminConsolePage";

interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | null;
}

let calls: Call[];

function installFetch(handler: (call: Call) => Response): void {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const call: Call = {
        url: String(input),
        method: init?.method ?? "GET",
        body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
      };
      // The page embeds <SupportInbox/>, which fetches /support-access on mount. Keep it
      // deterministically empty (and out of `calls`) so each test exercises only the console.
      if (call.url.includes("/support-access")) {
        return Promise.resolve(json(200, []));
      }
      calls.push(call);
      return Promise.resolve(handler(call));
    }) as unknown as typeof fetch,
  );
}

function json(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
  } as Response;
}

const ADMIN: AuthUser = {
  id: "admin-1",
  email: "admin@acme.de",
  full_name: "Anna Admin",
  role: "company_admin",
  org_id: "org-1",
  org_name: "Acme GmbH",
};

const MEMBER_ROW = {
  id: "u-2",
  email: "bob@acme.de",
  full_name: "Bob Member",
  role: "member",
  is_active: true,
  org_id: "org-1",
  created_at: "2026-06-01T10:00:00Z",
};

const ADMIN_ROW = {
  id: "admin-1",
  email: "admin@acme.de",
  full_name: "Anna Admin",
  role: "company_admin",
  is_active: true,
  org_id: "org-1",
  created_at: "2026-05-01T10:00:00Z",
};

const DEACTIVATED_ROW = {
  id: "u-3",
  email: "carol@acme.de",
  full_name: "Carol Gone",
  role: "member",
  is_active: false,
  org_id: "org-1",
  created_at: "2026-06-02T10:00:00Z",
};

function renderPage(logout = vi.fn()) {
  const value: AuthContextValue = {
    user: ADMIN,
    status: "authenticated",
    login: vi.fn(),
    platformLogin: vi.fn(),
    logout,
  };
  render(
    <AuthContext.Provider value={value}>
      <MemoryRouter>
        <AdminConsolePage />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
  return { logout };
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("AdminConsolePage", () => {
  it("test_renders_user_list_after_load", async () => {
    installFetch(() => json(200, [ADMIN_ROW, MEMBER_ROW]));
    renderPage();

    expect(await screen.findByText("Bob Member")).toBeInTheDocument();
    expect(screen.getByText("Anna Admin")).toBeInTheDocument();
    // The embedded support inbox is empty here — no bogus grant rows leak into the console.
    expect(screen.queryByText(/has access/i)).not.toBeInTheDocument();
  });

  it("test_empty_list_shows_add_first_hint", async () => {
    installFetch(() => json(200, []));
    renderPage();

    expect(await screen.findByText(/No users yet/i)).toBeInTheDocument();
  });

  it("test_load_error_then_retry_loads_list", async () => {
    let failNext = true;
    installFetch(() => (failNext ? json(500, {}) : json(200, [MEMBER_ROW])));
    renderPage();

    expect(await screen.findByText(/Couldn't load your users/i)).toBeInTheDocument();

    failNext = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Bob Member")).toBeInTheDocument();
  });

  it("test_401_on_load_logs_out", async () => {
    installFetch(() => json(401, {}));
    const { logout } = renderPage();

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
  });

  it("test_search_filters_the_list", async () => {
    installFetch(() => json(200, [ADMIN_ROW, MEMBER_ROW]));
    renderPage();
    await screen.findByText("Bob Member");

    fireEvent.change(screen.getByLabelText("Search users"), { target: { value: "bob" } });

    expect(screen.getByText("Bob Member")).toBeInTheDocument();
    expect(screen.queryByText("Anna Admin")).not.toBeInTheDocument();
  });

  it("test_add_user_button_opens_the_drawer", async () => {
    installFetch(() => json(200, [ADMIN_ROW]));
    renderPage();
    await screen.findByText("Anna Admin");

    fireEvent.click(screen.getByRole("button", { name: /Add user/ }));

    expect(screen.getByRole("dialog", { name: "Add a user" })).toBeInTheDocument();
  });

  it("test_deactivate_member_confirms_then_calls_delete", async () => {
    installFetch((call) =>
      call.method === "DELETE" ? json(204, null) : json(200, [ADMIN_ROW, MEMBER_ROW]),
    );
    renderPage();
    await screen.findByText("Bob Member");

    const row = screen.getByText("Bob Member").closest("li") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Deactivate" }));

    const dialog = await screen.findByRole("dialog", { name: /Deactivate Bob Member/ });
    fireEvent.click(within(dialog).getByRole("button", { name: "Deactivate" }));

    await waitFor(() =>
      expect(calls.some((call) => call.method === "DELETE" && call.url.includes("/users/u-2"))).toBe(
        true,
      ),
    );
  });

  it("test_last_admin_deactivate_shows_409_in_dialog", async () => {
    installFetch((call) =>
      call.method === "DELETE" ? json(409, { detail: "last admin" }) : json(200, [MEMBER_ROW]),
    );
    renderPage();
    await screen.findByText("Bob Member");

    const row = screen.getByText("Bob Member").closest("li") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Deactivate" }));
    const dialog = await screen.findByRole("dialog", { name: /Deactivate Bob Member/ });
    fireEvent.click(within(dialog).getByRole("button", { name: "Deactivate" }));

    expect(await within(dialog).findByText(/at least one administrator/i)).toBeInTheDocument();
  });

  it("test_inline_role_change_patches_the_role", async () => {
    installFetch((call) =>
      call.method === "PATCH"
        ? json(200, { ...MEMBER_ROW, role: "company_admin" })
        : json(200, [ADMIN_ROW, MEMBER_ROW]),
    );
    renderPage();
    await screen.findByText("Bob Member");

    fireEvent.change(screen.getByLabelText("Change role for Bob Member"), {
      target: { value: "company_admin" },
    });

    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.method === "PATCH" &&
            call.url.includes("/users/u-2") &&
            call.body?.role === "company_admin",
        ),
      ).toBe(true),
    );
  });

  it("test_self_demotion_confirms_then_logs_out", async () => {
    installFetch((call) =>
      call.method === "PATCH" ? json(200, { ...ADMIN_ROW, role: "member" }) : json(200, [ADMIN_ROW]),
    );
    const { logout } = renderPage();
    await screen.findByText("Anna Admin");

    fireEvent.change(screen.getByLabelText("Change role for Anna Admin"), {
      target: { value: "member" },
    });

    const dialog = await screen.findByRole("dialog", { name: /Remove your own admin access/ });
    fireEvent.click(within(dialog).getByRole("button", { name: "Make me a member" }));

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
  });

  it("test_403_on_load_logs_out", async () => {
    installFetch(() => json(403, {}));
    const { logout } = renderPage();

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
  });

  it("test_self_deactivation_confirms_then_logs_out", async () => {
    installFetch((call) => (call.method === "DELETE" ? json(204, null) : json(200, [ADMIN_ROW])));
    const { logout } = renderPage();
    await screen.findByText("Anna Admin");

    const row = screen.getByText("Anna Admin").closest("li") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Deactivate" }));
    const dialog = await screen.findByRole("dialog", { name: /Deactivate your own account/ });
    fireEvent.click(within(dialog).getByRole("button", { name: "Deactivate" }));

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
  });

  it("test_inline_role_change_last_admin_shows_notice", async () => {
    installFetch((call) =>
      call.method === "PATCH"
        ? json(409, { detail: "last admin" })
        : json(200, [ADMIN_ROW, MEMBER_ROW]),
    );
    renderPage();
    await screen.findByText("Bob Member");

    fireEvent.change(screen.getByLabelText("Change role for Bob Member"), {
      target: { value: "company_admin" },
    });

    expect(await screen.findByText(/at least one administrator/i)).toBeInTheDocument();
  });

  it("test_reactivate_deactivated_user_patches_is_active_true", async () => {
    // The only state-changing console action with no prior page-level test — and the only one
    // that renders the inactive-row UI (Deactivated badge + Reactivate) at all.
    installFetch((call) =>
      call.method === "PATCH"
        ? json(200, { ...DEACTIVATED_ROW, is_active: true })
        : json(200, [ADMIN_ROW, DEACTIVATED_ROW]),
    );
    renderPage();
    await screen.findByText("Carol Gone");

    const row = screen.getByText("Carol Gone").closest("li") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Reactivate" }));

    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.method === "PATCH" &&
            call.url.includes("/users/u-3") &&
            call.body?.is_active === true,
        ),
      ).toBe(true),
    );
  });

  it("test_inline_role_change_keeps_rows_visible_no_skeleton_flash", async () => {
    // M1: the post-mutation refetch is silent — the existing rows stay mounted, never flashing to
    // skeletons. Hold the reload GET open and assert Bob's row is still on screen meanwhile (the
    // old loud reload would have unmounted the list to skeleton placeholders here).
    let releaseReload: () => void = () => {};
    let getCount = 0;
    installFetch((call) => {
      if (call.method === "PATCH") return json(200, { ...MEMBER_ROW, role: "company_admin" });
      getCount += 1;
      if (getCount === 1) return json(200, [ADMIN_ROW, MEMBER_ROW]);
      // The 2nd GET is the post-mutation reload — keep it pending until we release it.
      return new Promise<Response>((resolve) => {
        releaseReload = () => resolve(json(200, [ADMIN_ROW, MEMBER_ROW]));
      }) as unknown as Response;
    });
    renderPage();
    await screen.findByText("Bob Member");

    fireEvent.change(screen.getByLabelText("Change role for Bob Member"), {
      target: { value: "company_admin" },
    });

    // While the reload GET is still pending, the row must remain rendered (no skeleton swap).
    await waitFor(() => expect(getCount).toBe(2));
    expect(screen.getByText("Bob Member")).toBeInTheDocument();

    releaseReload();
    await waitFor(() => expect(screen.getByText("Bob Member")).toBeInTheDocument());
  });
});
