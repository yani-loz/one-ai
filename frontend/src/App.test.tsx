/**
 * Role: Route-shell tests — verifies that an unauthenticated visit to "/" redirects
 *       to /login, and that the login screen renders inside the App router shell.
 * Used by: vitest (pnpm test).
 * Depends on: a live AuthProvider (no router context comes from App itself; tests
 *   supply MemoryRouter) and a mocked fetch boundary so bootstrap resolves offline.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { AuthProvider } from "./identity";

function mockFetch(impl: () => Promise<unknown>): void {
  vi.stubGlobal("fetch", vi.fn(impl) as unknown as typeof fetch);
}

function renderAppAt(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("App route shell", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("renders the login screen on the public /login route", async () => {
    mockFetch(() => Promise.reject(new Error("offline")));

    renderAppAt("/login");

    expect(await screen.findByRole("heading", { name: "One AI" })).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
  });

  it("redirects an unauthenticated visit to / onto the login screen", async () => {
    mockFetch(() => Promise.reject(new Error("offline")));

    renderAppAt("/");

    await waitFor(() => expect(screen.getByLabelText("Password")).toBeInTheDocument());
    expect(screen.getByText("Sign in to your workspace")).toBeInTheDocument();
  });
});
