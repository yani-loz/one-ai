/**
 * Role: Integration tests for the login flow — renders LoginPage inside the App
 *       router shell with a real AuthProvider, covering render, dev-credential fill,
 *       successful login routing home, failed login messaging, and logout clearing.
 * Used by: vitest (pnpm test).
 * Depends on: a mocked global fetch (adapter boundary), real AuthProvider/router.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import { AuthProvider } from ".";
import { clearSession } from "./authClient";

interface FetchCall {
  url: string;
  body: Record<string, unknown> | null;
}

function recordedCall(input: RequestInfo | URL, init?: RequestInit): FetchCall {
  const body =
    typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : null;
  return { url: String(input), body };
}

const DEMO_USER = {
  id: "u1",
  email: "admin@demo.oneai",
  full_name: "Demo Admin",
  role: "company_admin",
  org_id: "org-1",
  org_name: "One AI Demo GmbH",
};

const DEV_DEMO_ADMIN_EMAIL = "dev-admin@example.test";
const DEV_DEMO_ADMIN_PASSWORD = "admin-test-password";
const DEV_DEMO_MEMBER_EMAIL = "dev-member@example.test";
const DEV_DEMO_MEMBER_PASSWORD = "member-test-password";

let fetchMock: ReturnType<typeof vi.fn>;

/** Default router: a failing /auth/me so bootstrap resolves to unauthenticated. */
function renderApp() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  clearSession();
  vi.stubEnv("VITE_DEV_DEMO_ADMIN_EMAIL", DEV_DEMO_ADMIN_EMAIL);
  vi.stubEnv("VITE_DEV_DEMO_ADMIN_PASSWORD", DEV_DEMO_ADMIN_PASSWORD);
  vi.stubEnv("VITE_DEV_DEMO_MEMBER_EMAIL", DEV_DEMO_MEMBER_EMAIL);
  vi.stubEnv("VITE_DEV_DEMO_MEMBER_PASSWORD", DEV_DEMO_MEMBER_PASSWORD);
  fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    // Default: health probe ok, everything else rejected (unauthenticated bootstrap).
    if (String(input).includes("/health")) {
      return {
        ok: true,
        status: 200,
        json: () => Promise.resolve({ service: "One AI", version: "0.1.0", database: "reachable" }),
      } as Response;
    }
    return { ok: false, status: 401, json: () => Promise.resolve({}) } as Response;
  });
  vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  clearSession();
});

describe("LoginPage", () => {
  it("test_login_renders_form_and_dev_credentials_panel_in_dev_build", async () => {
    // The panel is gated on import.meta.env.DEV, which is true under vitest — so it must
    // render here. The production (DEV=false) branch is enforced by Vite's static
    // replacement + dead-code elimination and is not reachable from a vitest run.
    renderApp();

    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByText("Dev test accounts")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Demo admin/ })).toBeInTheDocument();
  });

  it("test_dev_credential_button_fills_the_form", async () => {
    const user = userEvent.setup();
    renderApp();

    await user.click(await screen.findByRole("button", { name: /Demo member/ }));

    expect(screen.getByLabelText<HTMLInputElement>("Email").value).toBe(DEV_DEMO_MEMBER_EMAIL);
    expect(screen.getByLabelText<HTMLInputElement>("Password").value).toBe(DEV_DEMO_MEMBER_PASSWORD);
  });

  it("test_platform_toggle_switches_to_platform_login_endpoint_and_lands_on_console", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const { url } = recordedCall(input, init);
      if (url.includes("/platform/login")) {
        return {
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({ access_token: "pa", refresh_token: "pr", token_type: "bearer" }),
        } as Response;
      }
      // After login, AuthProvider resolves the real identity via GET /platform/me.
      if (url.includes("/platform/me")) {
        return {
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({ id: "pa-1", email: "super@ethera.ai", full_name: "Super" }),
        } as Response;
      }
      // The console loads the company list on mount — return an empty fleet.
      if (url.includes("/platform/orgs")) {
        return { ok: true, status: 200, json: () => Promise.resolve([]) } as Response;
      }
      return { ok: false, status: 401, json: () => Promise.resolve({}) } as Response;
    });

    renderApp();
    await user.click(await screen.findByRole("button", { name: "Sign in as platform admin" }));
    await user.type(screen.getByLabelText("Email"), "super@ethera.ai");
    await user.type(screen.getByLabelText("Password"), "pw");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    // A platform admin lands on the console, not the company home.
    expect(await screen.findByRole("heading", { name: "Companies" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/platform/login"))).toBe(
      true,
    );
  });

  it("test_platform_login_succeeds_but_me_fails_clears_session_and_stays_on_login", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const { url } = recordedCall(input, init);
      if (url.includes("/platform/login")) {
        return {
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({ access_token: "pa", refresh_token: "pr", token_type: "bearer" }),
        } as Response;
      }
      if (url.includes("/platform/me")) {
        return { ok: false, status: 500, json: () => Promise.resolve({}) } as Response;
      }
      if (url.includes("/platform/logout")) {
        return { ok: true, status: 204, json: () => Promise.resolve({}) } as Response;
      }
      return { ok: false, status: 401, json: () => Promise.resolve({}) } as Response;
    });

    renderApp();
    await user.click(await screen.findByRole("button", { name: "Sign in as platform admin" }));
    await user.type(screen.getByLabelText("Email"), "super@ethera.ai");
    await user.type(screen.getByLabelText("Password"), "pw");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    // /platform/me failed after login -> stay on login with a connectivity message (not a
    // misleading bad-password), tear down the half-open session (logout called + storage
    // cleared), and never navigate to the console.
    expect(await screen.findByRole("alert")).toHaveTextContent(/Couldn't reach the server/);
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Companies" })).not.toBeInTheDocument();
    // The half-open session is torn down: logout was called to revoke the in-memory token.
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("/platform/logout")),
    ).toBe(true);
  });

  it("test_successful_company_login_routes_to_home_with_greeting", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/auth/login")) {
        return {
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              access_token: "a1",
              refresh_token: "r1",
              token_type: "bearer",
              user: DEMO_USER,
            }),
        } as Response;
      }
      if (url.includes("/health")) {
        return {
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({ service: "One AI", version: "0.1.0", database: "reachable" }),
        } as Response;
      }
      return { ok: false, status: 401, json: () => Promise.resolve({}) } as Response;
    });

    renderApp();
    await user.click(await screen.findByRole("button", { name: /Demo admin/ }));
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(screen.getByText("Demo Admin")).toBeInTheDocument());
    // The health probe resolves after navigation; wait for at least one detail node
    // (page-transition overlap can briefly mount two) to show the reachable DB.
    await waitFor(() =>
      expect(
        screen
          .getAllByTestId("health-detail")
          .some((node) => node.textContent?.includes("DB reachable")),
      ).toBe(true),
    );
  });

  it("test_failed_login_shows_generic_error_and_stays_on_login", async () => {
    const user = userEvent.setup();
    renderApp(); // default mock rejects /auth/login with 401

    await user.type(await screen.findByLabelText("Email"), "admin@demo.oneai");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password.");
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
  });

  it("test_network_error_shows_connectivity_message_not_wrong_password", async () => {
    // A transport failure (API down / mid hot-reload) must NOT read as "wrong password".
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/auth/login")) {
        throw new TypeError("Failed to fetch");
      }
      return { ok: false, status: 401, json: () => Promise.resolve({}) } as Response;
    });

    renderApp();
    await user.click(await screen.findByRole("button", { name: /Demo admin/ }));
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't reach the server");
    expect(screen.queryByText("Invalid email or password.")).not.toBeInTheDocument();
  });

  it("test_logout_clears_session_and_returns_to_login", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/auth/login")) {
        return {
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              access_token: "a1",
              refresh_token: "r1",
              token_type: "bearer",
              user: DEMO_USER,
            }),
        } as Response;
      }
      if (url.includes("/auth/logout")) {
        return { ok: true, status: 204, json: () => Promise.resolve({}) } as Response;
      }
      if (url.includes("/health")) {
        return {
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({ service: "One AI", version: "0.1.0", database: "reachable" }),
        } as Response;
      }
      return { ok: false, status: 401, json: () => Promise.resolve({}) } as Response;
    });

    renderApp();
    await user.click(await screen.findByRole("button", { name: /Demo admin/ }));
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByText("Demo Admin")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Log out" }));

    await waitFor(() => expect(screen.getByText("Sign in to your workspace")).toBeInTheDocument());
    // Logout revoked the session server-side (httpOnly cookie cleared by /auth/logout).
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("/auth/logout")),
    ).toBe(true);
  });
});
