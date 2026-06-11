/**
 * Tests for the onboard drawer — slug auto-suggestion (and that it stops after a manual
 * edit), client-side validation gating, the success/credential hand-off + copy, the
 * duplicate vs generic error messages, the close-without-onboard path, the session-expiry
 * (401) → logout path, close paths inert while a submit is in flight, and the
 * late-resolution guard (a parent force-close mid-flight refreshes the list without
 * ghosting the hand-off). Onboarding hits a mocked /platform/orgs POST.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OnboardCompanyDrawer } from "./OnboardCompanyDrawer";

function jsonResponse(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
  } as Response;
}

const ONBOARDED = {
  organization: {
    id: "org-9",
    name: "Acme Corp",
    slug: "acme-corp",
    status: "active",
    user_count: 1,
    created_at: "2026-06-01T10:00:00Z",
  },
  admin: {
    id: "u-9",
    email: "anna@acme.de",
    full_name: "Anna Schmidt",
    role: "company_admin",
    org_id: "org-9",
    created_at: "2026-06-01T10:00:00Z",
  },
};

interface DrawerHandlers {
  onClose: ReturnType<typeof vi.fn>;
  onOnboarded: ReturnType<typeof vi.fn>;
  onSessionExpired: ReturnType<typeof vi.fn>;
}

function renderDrawer(): DrawerHandlers {
  const handlers: DrawerHandlers = {
    onClose: vi.fn(),
    onOnboarded: vi.fn(),
    onSessionExpired: vi.fn(),
  };
  render(<OnboardCompanyDrawer open {...handlers} />);
  return handlers;
}

async function fillForm(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.type(screen.getByLabelText("Company name"), "Acme Corp");
  await user.type(screen.getByLabelText("Admin full name"), "Anna Schmidt");
  await user.type(screen.getByLabelText("Admin email"), "anna@acme.de");
  await user.type(screen.getByLabelText("Temporary password"), "Sup3r-Secret!");
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("OnboardCompanyDrawer", () => {
  it("test_slug_auto_suggests_from_company_name", async () => {
    const user = userEvent.setup();
    renderDrawer();

    await user.type(screen.getByLabelText("Company name"), "Acme Corp");

    expect(screen.getByLabelText<HTMLInputElement>("Slug").value).toBe("acme-corp");
  });

  it("test_slug_stops_auto_suggesting_after_manual_edit", async () => {
    const user = userEvent.setup();
    renderDrawer();

    await user.type(screen.getByLabelText("Company name"), "Acme Corp");
    const slug = screen.getByLabelText<HTMLInputElement>("Slug");
    await user.clear(slug);
    await user.type(slug, "custom-slug");
    await user.type(screen.getByLabelText("Company name"), " International");

    expect(screen.getByLabelText<HTMLInputElement>("Slug").value).toBe("custom-slug");
  });

  it("test_invalid_slug_keeps_submit_disabled", async () => {
    const user = userEvent.setup();
    renderDrawer();

    await fillForm(user);
    const slug = screen.getByLabelText("Slug");
    await user.clear(slug);
    await user.type(slug, "-bad-leading-dash");

    expect(screen.getByRole("button", { name: "Onboard company" })).toBeDisabled();
  });

  it("test_short_password_keeps_submit_disabled", async () => {
    const user = userEvent.setup();
    renderDrawer();

    await fillForm(user);
    const password = screen.getByLabelText("Temporary password");
    await user.clear(password);
    await user.type(password, "short");

    expect(screen.getByRole("button", { name: "Onboard company" })).toBeDisabled();
  });

  it("test_successful_onboard_shows_credentials_and_refreshes_on_done", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(201, ONBOARDED))));

    const handlers = renderDrawer();
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Onboard company" }));

    expect(await screen.findByText(/is live/)).toBeInTheDocument();
    expect(screen.getByText("anna@acme.de")).toBeInTheDocument();
    expect(screen.getByText("Sup3r-Secret!")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(handlers.onOnboarded).toHaveBeenCalled();
    expect(handlers.onClose).toHaveBeenCalled();
  });

  it("test_copy_button_copies_credential_and_toggles_label", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn();
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(201, ONBOARDED))));

    renderDrawer();
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Onboard company" }));
    await screen.findByText(/is live/);

    await user.click(screen.getAllByRole("button", { name: "Copy" })[0]);

    expect(writeText).toHaveBeenCalledWith("anna@acme.de");
    // "Copied" only appears after the (now-awaited) clipboard write confirms.
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("test_duplicate_slug_or_email_shows_specific_message", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(409, { detail: "taken" }))));

    renderDrawer();
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Onboard company" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/already taken/));
  });

  it("test_non_duplicate_failure_shows_generic_message_not_already_exists", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(500, {}))));

    renderDrawer();
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Onboard company" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/Couldn't complete onboarding/),
    );
    expect(screen.queryByText(/already taken/)).not.toBeInTheDocument();
  });

  it("test_session_expiry_during_submit_triggers_logout", async () => {
    // No stored refresh token → the 401 refresh attempt fails and surfaces as a 401, so
    // the drawer fires onSessionExpired (mirroring how the company list handles a 401).
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(401, {}))));

    const handlers = renderDrawer();
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Onboard company" }));

    await waitFor(() => expect(handlers.onSessionExpired).toHaveBeenCalled());
  });

  it("test_close_paths_ignored_while_submitting_then_handoff_still_appears", async () => {
    const user = userEvent.setup();
    let resolveOnboard!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>((resolve) => (resolveOnboard = resolve))),
    );
    const handlers = renderDrawer();
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Onboard company" }));

    // Mid-submit: the ✕ is disabled and every close path (✕, backdrop, Escape) is inert —
    // closing here would wipe the one-time password while the onboard may still succeed.
    const closeButton = screen.getByRole("button", { name: "Close" });
    expect(closeButton).toBeDisabled();
    fireEvent.click(closeButton);
    fireEvent.click(screen.getByRole("dialog").previousElementSibling as Element); // backdrop
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(handlers.onClose).not.toHaveBeenCalled();

    resolveOnboard(jsonResponse(201, ONBOARDED));

    // The hand-off appears (no wiped state) and closing works again, still refreshing the list.
    expect(await screen.findByText(/is live/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(handlers.onOnboarded).toHaveBeenCalledTimes(1);
    expect(handlers.onClose).toHaveBeenCalledTimes(1);
  });

  it("test_late_success_after_parent_force_close_refreshes_without_ghost_panel", async () => {
    const user = userEvent.setup();
    let resolveOnboard!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>((resolve) => (resolveOnboard = resolve))),
    );
    const handlers: DrawerHandlers = {
      onClose: vi.fn(),
      onOnboarded: vi.fn(),
      onSessionExpired: vi.fn(),
    };
    const view = render(<OnboardCompanyDrawer open {...handlers} />);
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Onboard company" }));

    // The parent force-closes the drawer mid-flight, then the onboard resolves late. The company
    // exists server-side → the list must refresh…
    view.rerender(<OnboardCompanyDrawer open={false} {...handlers} />);
    resolveOnboard(jsonResponse(201, ONBOARDED));
    await waitFor(() => expect(handlers.onOnboarded).toHaveBeenCalledTimes(1));

    // …but reopening must show a fresh form, never a ghost hand-off with a blank password.
    view.rerender(<OnboardCompanyDrawer open {...handlers} />);
    expect(screen.queryByText(/is live/)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Onboard a company" })).toBeInTheDocument();
  });

  it("test_closing_without_onboarding_does_not_refresh", async () => {
    const user = userEvent.setup();

    const handlers = renderDrawer();
    await user.click(screen.getByRole("button", { name: "Close" }));

    expect(handlers.onClose).toHaveBeenCalled();
    expect(handlers.onOnboarded).not.toHaveBeenCalled();
  });
});
