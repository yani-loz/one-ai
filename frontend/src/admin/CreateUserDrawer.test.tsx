/**
 * Tests for the add-user drawer — submit stays disabled until the form mirrors the backend
 * bounds; a successful create posts the payload and refreshes; a 409 shows the email-taken
 * message; a 401 raises onSessionExpired. Global fetch is mocked at the boundary; localStorage
 * is cleared so a 401 has no refresh token to rotate and surfaces cleanly.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { CreateUserDrawer } from "./CreateUserDrawer";

function mockFetch(status: number, payload: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        json: () => Promise.resolve(payload),
      } as Response),
    ) as unknown as typeof fetch,
  );
}

function fillValidForm(): void {
  fireEvent.change(screen.getByLabelText("Full name"), { target: { value: "Anna Schmidt" } });
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "anna@acme.de" } });
  fireEvent.change(screen.getByLabelText("Initial password"), {
    target: { value: "Sup3r-Secret!" },
  });
}

const noop = (): void => {};

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("CreateUserDrawer", () => {
  it("test_submit_disabled_until_form_is_valid", () => {
    render(
      <CreateUserDrawer open onClose={noop} onCreated={noop} onSessionExpired={noop} />,
    );

    expect(screen.getByRole("button", { name: /Add user/ })).toBeDisabled();

    fillValidForm();

    expect(screen.getByRole("button", { name: /Add user/ })).toBeEnabled();
  });

  it("test_short_password_keeps_submit_disabled", () => {
    render(
      <CreateUserDrawer open onClose={noop} onCreated={noop} onSessionExpired={noop} />,
    );

    fireEvent.change(screen.getByLabelText("Full name"), { target: { value: "Anna" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "anna@acme.de" } });
    fireEvent.change(screen.getByLabelText("Initial password"), { target: { value: "short" } });

    expect(screen.getByRole("button", { name: /Add user/ })).toBeDisabled();
  });

  it("test_invalid_email_keeps_submit_disabled", () => {
    render(
      <CreateUserDrawer open onClose={noop} onCreated={noop} onSessionExpired={noop} />,
    );

    fireEvent.change(screen.getByLabelText("Full name"), { target: { value: "Anna" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "not-an-email" } });
    fireEvent.change(screen.getByLabelText("Initial password"), {
      target: { value: "Sup3r-Secret!" },
    });

    expect(screen.getByRole("button", { name: /Add user/ })).toBeDisabled();
  });

  it("test_generate_fills_a_valid_password_and_enables_submit", () => {
    render(
      <CreateUserDrawer open onClose={noop} onCreated={noop} onSessionExpired={noop} />,
    );
    fireEvent.change(screen.getByLabelText("Full name"), { target: { value: "Anna" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "anna@acme.de" } });
    expect(screen.getByRole("button", { name: /Add user/ })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Generate" }));

    const password = screen.getByLabelText("Initial password") as HTMLInputElement;
    expect(password.value).toHaveLength(16);
    expect(password).toHaveAttribute("type", "text"); // revealed so the admin sees it
    expect(screen.getByRole("button", { name: /Add user/ })).toBeEnabled();
  });

  it("test_show_hide_toggles_password_visibility", () => {
    render(
      <CreateUserDrawer open onClose={noop} onCreated={noop} onSessionExpired={noop} />,
    );
    const password = screen.getByLabelText("Initial password");
    expect(password).toHaveAttribute("type", "password");

    fireEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(password).toHaveAttribute("type", "text");

    fireEvent.click(screen.getByRole("button", { name: "Hide password" }));
    expect(password).toHaveAttribute("type", "password");
  });

  it("test_successful_create_shows_credential_handoff_then_done_refreshes_and_closes", async () => {
    const onCreated = vi.fn();
    const onClose = vi.fn();
    mockFetch(201, {
      id: "u-9",
      email: "anna@acme.de",
      full_name: "Anna Schmidt",
      role: "member",
      is_active: true,
      org_id: "org-1",
      created_at: "2026-06-01T10:00:00Z",
    });
    render(
      <CreateUserDrawer open onClose={onClose} onCreated={onCreated} onSessionExpired={noop} />,
    );

    fillValidForm();
    fireEvent.click(screen.getByRole("button", { name: /Add user/ }));

    // The form is replaced by a hand-off that reveals the email + the password to copy; the
    // list is NOT refreshed yet (the admin still needs the credentials).
    expect(await screen.findByText("anna@acme.de")).toBeInTheDocument();
    expect(screen.getByText("Sup3r-Secret!")).toBeInTheDocument();
    expect(onCreated).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Done" }));

    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("test_duplicate_email_shows_specific_message", async () => {
    mockFetch(409, { detail: "exists" });
    render(
      <CreateUserDrawer open onClose={noop} onCreated={noop} onSessionExpired={noop} />,
    );

    fillValidForm();
    fireEvent.click(screen.getByRole("button", { name: /Add user/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/already exists/i);
  });

  it("test_session_expired_calls_on_session_expired", async () => {
    const onSessionExpired = vi.fn();
    mockFetch(401, {});
    render(
      <CreateUserDrawer
        open
        onClose={noop}
        onCreated={noop}
        onSessionExpired={onSessionExpired}
      />,
    );

    fillValidForm();
    fireEvent.click(screen.getByRole("button", { name: /Add user/ }));

    await waitFor(() => expect(onSessionExpired).toHaveBeenCalledTimes(1));
  });

  it("test_forbidden_role_change_calls_on_session_expired", async () => {
    // A 403 (the admin's role was downgraded mid-session) is handled like a 401: hand off to
    // the parent to log out, never a misleading "couldn't create" message.
    const onSessionExpired = vi.fn();
    mockFetch(403, {});
    render(
      <CreateUserDrawer
        open
        onClose={noop}
        onCreated={noop}
        onSessionExpired={onSessionExpired}
      />,
    );

    fillValidForm();
    fireEvent.click(screen.getByRole("button", { name: /Add user/ }));

    await waitFor(() => expect(onSessionExpired).toHaveBeenCalledTimes(1));
  });
});
