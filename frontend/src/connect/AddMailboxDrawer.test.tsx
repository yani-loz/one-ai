/**
 * Tests for the connect-mailbox drawer — the most security-sensitive FE surface (it carries an
 * app password). Covers: create-THEN-test sequencing (the result panel reflects the real
 * verification), the 409/503/generic error mapping, 401/403 → onSessionExpired, every close path
 * inert while a submit is in flight, the late-resolution guard (a parent force-close mid-flight
 * still fires onConnected — the connection exists server-side — without ghosting the result
 * panel), the created-but-test-failed close still refreshing the list, and focus re-assertion on
 * the result panel. Global fetch is mocked at the boundary; localStorage is cleared so a 401 has
 * no refresh token to rotate and surfaces cleanly.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { AddMailboxDrawer } from "./AddMailboxDrawer";

interface Call {
  url: string;
  method: string;
  body: string | null;
}

let calls: Call[];

function installFetch(handler: (call: Call) => Response | Promise<Response>): void {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const call: Call = {
        url: String(input),
        method: init?.method ?? "GET",
        body: typeof init?.body === "string" ? init.body : null,
      };
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

const CONNECTION = {
  id: "c-1",
  org_id: "org-1",
  connector_type: "imap",
  display_name: "anna@gmail.com",
  auth_method: "app_password",
  username: "anna@gmail.com",
  host: "imap.gmail.com",
  port: 993,
  use_ssl: true,
  status: "configured",
  is_enabled: true,
  disabled_at: null,
  last_checked_at: null,
  last_error: null,
  created_at: "2026-06-07T09:00:00Z",
  sync_status: "idle",
  synced_count: 0,
  total_count: null,
  last_synced_at: null,
  last_sync_error: null,
};

interface DrawerHandlers {
  onClose: ReturnType<typeof vi.fn>;
  onConnected: ReturnType<typeof vi.fn>;
  onSessionExpired: ReturnType<typeof vi.fn>;
}

function makeHandlers(): DrawerHandlers {
  return { onClose: vi.fn(), onConnected: vi.fn(), onSessionExpired: vi.fn() };
}

function renderDrawer(handlers: DrawerHandlers = makeHandlers()) {
  const view = render(<AddMailboxDrawer open {...handlers} />);
  return { handlers, view };
}

/** Fill email (host/port auto-detect from gmail.com) + app password so submit enables. */
function fillValidForm(): void {
  fireEvent.change(screen.getByLabelText("Email address"), {
    target: { value: "anna@gmail.com" },
  });
  fireEvent.change(screen.getByLabelText("App password"), {
    target: { value: "abcd efgh ijkl mnop" },
  });
}

function submit(): void {
  fireEvent.click(screen.getByRole("button", { name: /Connect mailbox/ }));
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("AddMailboxDrawer", () => {
  it("test_submit_creates_then_tests_and_shows_live_result", async () => {
    installFetch((call) =>
      call.url.includes("/test")
        ? json(200, { ...CONNECTION, status: "connected" })
        : json(201, CONNECTION),
    );
    const { handlers } = renderDrawer();
    fillValidForm();

    submit();

    // Create first, THEN test the stored connection — the panel shows the real verification.
    expect(await screen.findByText("✓ Connected")).toBeInTheDocument();
    expect(calls).toHaveLength(2);
    expect(calls[0].method).toBe("POST");
    expect(calls[0].url).toMatch(/\/connectors$/);
    expect(calls[0].body).toContain('"username":"anna@gmail.com"');
    expect(calls[0].body).toContain('"host":"imap.gmail.com"');
    expect(calls[1].method).toBe("POST");
    expect(calls[1].url).toMatch(/\/connectors\/c-1\/test$/);
    // The list refreshes only when the result panel is dismissed.
    expect(handlers.onConnected).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(handlers.onConnected).toHaveBeenCalledTimes(1);
    expect(handlers.onClose).toHaveBeenCalledTimes(1);
  });

  it("test_result_panel_reasserts_focus_to_status_line", async () => {
    installFetch((call) =>
      call.url.includes("/test")
        ? json(200, { ...CONNECTION, status: "connected" })
        : json(201, CONNECTION),
    );
    renderDrawer();
    fillValidForm();

    submit();

    // The form→result swap keeps the drawer open (the trap keyed on `open` doesn't re-fire), so
    // the panel must pull focus back in — otherwise Escape/Tab die exactly on the result screen.
    const status = await screen.findByText("✓ Connected");
    expect(status).toHaveFocus();
  });

  it("test_duplicate_mailbox_409_shows_already_connected_message", async () => {
    installFetch(() => json(409, { detail: "duplicate" }));
    renderDrawer();
    fillValidForm();

    submit();

    expect(await screen.findByRole("alert")).toHaveTextContent(/already connected/i);
  });

  it("test_missing_server_key_503_shows_support_message", async () => {
    installFetch(() => json(503, { detail: "no key" }));
    renderDrawer();
    fillValidForm();

    submit();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /isn't configured to store credentials/i,
    );
  });

  it("test_unexpected_failure_shows_generic_message", async () => {
    installFetch(() => json(500, {}));
    renderDrawer();
    fillValidForm();

    submit();

    expect(await screen.findByRole("alert")).toHaveTextContent(/Couldn't connect/);
  });

  it("test_session_expired_401_calls_on_session_expired", async () => {
    installFetch(() => json(401, {}));
    const { handlers } = renderDrawer();
    fillValidForm();

    submit();

    await waitFor(() => expect(handlers.onSessionExpired).toHaveBeenCalledTimes(1));
  });

  it("test_forbidden_403_calls_on_session_expired", async () => {
    installFetch(() => json(403, {}));
    const { handlers } = renderDrawer();
    fillValidForm();

    submit();

    await waitFor(() => expect(handlers.onSessionExpired).toHaveBeenCalledTimes(1));
  });

  it("test_close_paths_ignored_while_submitting_then_result_still_appears", async () => {
    let resolveCreate!: (response: Response) => void;
    installFetch((call) =>
      call.url.includes("/test")
        ? json(200, { ...CONNECTION, status: "connected" })
        : new Promise<Response>((resolve) => (resolveCreate = resolve)),
    );
    const { handlers } = renderDrawer();
    fillValidForm();
    submit();

    // Mid-submit: the ✕ is disabled and every close path (✕, backdrop, Escape) is inert —
    // closing here would hide a connection being created server-side (a re-submit then 409s).
    const closeButton = screen.getByRole("button", { name: "Close" });
    expect(closeButton).toBeDisabled();
    fireEvent.click(closeButton);
    fireEvent.click(screen.getByRole("dialog").previousElementSibling as Element); // backdrop
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(handlers.onClose).not.toHaveBeenCalled();

    resolveCreate(json(201, CONNECTION));

    // The result panel appears (no wiped state) and closing works again, refreshing the list.
    expect(await screen.findByText("✓ Connected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(handlers.onConnected).toHaveBeenCalledTimes(1);
    expect(handlers.onClose).toHaveBeenCalledTimes(1);
  });

  it("test_late_create_after_parent_force_close_fires_on_connected_without_ghost", async () => {
    let resolveCreate!: (response: Response) => void;
    installFetch((call) =>
      call.url.includes("/test")
        ? json(200, { ...CONNECTION, status: "connected" })
        : new Promise<Response>((resolve) => (resolveCreate = resolve)),
    );
    const handlers = makeHandlers();
    const { view } = renderDrawer(handlers);
    fillValidForm();
    submit();

    // The parent force-closes the drawer mid-flight, then the create resolves late. The
    // connection exists server-side → onConnected must still fire so the list shows it…
    view.rerender(<AddMailboxDrawer open={false} {...handlers} />);
    resolveCreate(json(201, CONNECTION));
    await waitFor(() => expect(handlers.onConnected).toHaveBeenCalledTimes(1));
    // …and the now-pointless live test is skipped (only the create POST went out).
    expect(calls.filter((call) => call.url.includes("/test"))).toHaveLength(0);

    // Reopening must show a fresh form, never a ghost result panel.
    view.rerender(<AddMailboxDrawer open {...handlers} />);
    expect(screen.queryByText("✓ Connected")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Connect a mailbox" })).toBeInTheDocument();
  });

  it("test_created_but_test_failed_close_still_refreshes_list", async () => {
    installFetch((call) =>
      call.url.includes("/test") ? json(500, {}) : json(201, CONNECTION),
    );
    const { handlers } = renderDrawer();
    fillValidForm();

    submit();

    // The create reached the server but the live test errored → the form shows the generic
    // message; closing must STILL refresh the list (the mailbox exists, a re-submit would 409).
    expect(await screen.findByRole("alert")).toHaveTextContent(/Couldn't connect/);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(handlers.onConnected).toHaveBeenCalledTimes(1);
    expect(handlers.onClose).toHaveBeenCalledTimes(1);
  });
});
