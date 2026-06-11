/**
 * Tests for the create-user success / credential hand-off. The form→success swap keeps the drawer
 * `open`, so the shared focus trap (keyed on `open`) does not re-fire; this panel must re-assert
 * focus to its heading on mount so a keyboard/screen-reader admin is not stranded on <body>.
 * The copy affordance must only claim "Copied" after a CONFIRMED clipboard write — a missing
 * Clipboard API (plain-HTTP host) or a rejected write shows a manual-copy fallback instead.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { CreateUserSuccess } from "./CreateUserSuccess";
import type { CompanyUser } from "./types";

const USER: CompanyUser = {
  id: "u-1",
  email: "anna@acme.de",
  full_name: "Anna Schmidt",
  role: "member",
  is_active: true,
  org_id: "org-1",
  created_at: "2026-06-01T10:00:00Z",
};

/** Install (or remove, with undefined) a stub Clipboard API on jsdom's navigator. */
function stubClipboard(writeText: (() => Promise<void>) | undefined): void {
  Object.defineProperty(navigator, "clipboard", {
    value: writeText === undefined ? undefined : { writeText },
    configurable: true,
  });
}

afterEach(() => stubClipboard(undefined));

describe("CreateUserSuccess", () => {
  it("test_focuses_heading_on_mount_to_restore_dialog_focus", () => {
    render(<CreateUserSuccess user={USER} plaintextPassword="Sup3r-Strong!" onDone={() => {}} />);

    // The heading carries focus (restores the trap + announces "added"); the credentials are NOT
    // in a live region, so a screen reader does not auto-read the plaintext password.
    expect(screen.getByRole("heading", { name: /added/i })).toHaveFocus();
  });

  it("test_copy_confirmed_write_shows_copied", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    stubClipboard(writeText);
    render(<CreateUserSuccess user={USER} plaintextPassword="Sup3r-Strong!" onDone={() => {}} />);

    fireEvent.click(screen.getAllByRole("button", { name: "Copy" })[1]); // the password row

    expect(await screen.findByText("Copied")).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith("Sup3r-Strong!");
  });

  it("test_copy_rejected_write_shows_manual_fallback_not_copied", async () => {
    stubClipboard(vi.fn(() => Promise.reject(new Error("denied"))));
    render(<CreateUserSuccess user={USER} plaintextPassword="Sup3r-Strong!" onDone={() => {}} />);

    fireEvent.click(screen.getAllByRole("button", { name: "Copy" })[1]);

    expect(await screen.findByText(/Copy failed — select the text manually/)).toBeInTheDocument();
    expect(screen.queryByText("Copied")).not.toBeInTheDocument();
  });

  it("test_copy_missing_clipboard_shows_manual_fallback_not_copied", async () => {
    stubClipboard(undefined); // plain-HTTP LAN host: no Clipboard API at all
    render(<CreateUserSuccess user={USER} plaintextPassword="Sup3r-Strong!" onDone={() => {}} />);

    fireEvent.click(screen.getAllByRole("button", { name: "Copy" })[1]);

    expect(await screen.findByText(/Copy failed — select the text manually/)).toBeInTheDocument();
    expect(screen.queryByText("Copied")).not.toBeInTheDocument();
  });
});
