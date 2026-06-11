/**
 * Tests for the onboard success / credential hand-off. The form→success swap keeps the drawer
 * `open`, so the shared focus trap (keyed on `open`) does not re-fire; this panel must re-assert
 * focus to its heading on mount (same pattern as admin/CreateUserSuccess) so keyboard handling
 * survives exactly while credentials are on screen. The copy affordance must only claim "Copied"
 * after a CONFIRMED clipboard write — missing/denied clipboards show a manual-copy fallback.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { OnboardSuccess } from "./OnboardSuccess";
import type { OnboardedCompany } from "./types";

const COMPANY: OnboardedCompany = {
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

/** Install (or remove, with undefined) a stub Clipboard API on jsdom's navigator. */
function stubClipboard(writeText: (() => Promise<void>) | undefined): void {
  Object.defineProperty(navigator, "clipboard", {
    value: writeText === undefined ? undefined : { writeText },
    configurable: true,
  });
}

afterEach(() => stubClipboard(undefined));

describe("OnboardSuccess", () => {
  it("test_focuses_heading_on_mount_to_restore_dialog_focus", () => {
    render(
      <OnboardSuccess company={COMPANY} plaintextPassword="Sup3r-Secret!" onDone={() => {}} />,
    );

    // The heading carries focus (restores the trap + announces the result); the credentials are
    // NOT in a live region, so a screen reader does not auto-read the plaintext password.
    expect(screen.getByRole("heading", { name: /is live/i })).toHaveFocus();
  });

  it("test_copy_confirmed_write_shows_copied", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    stubClipboard(writeText);
    render(
      <OnboardSuccess company={COMPANY} plaintextPassword="Sup3r-Secret!" onDone={() => {}} />,
    );

    fireEvent.click(screen.getAllByRole("button", { name: "Copy" })[1]); // the password row

    expect(await screen.findByText("Copied")).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith("Sup3r-Secret!");
  });

  it("test_copy_rejected_write_shows_manual_fallback_not_copied", async () => {
    stubClipboard(vi.fn(() => Promise.reject(new Error("denied"))));
    render(
      <OnboardSuccess company={COMPANY} plaintextPassword="Sup3r-Secret!" onDone={() => {}} />,
    );

    fireEvent.click(screen.getAllByRole("button", { name: "Copy" })[1]);

    expect(await screen.findByText(/Copy failed — select the text manually/)).toBeInTheDocument();
    expect(screen.queryByText("Copied")).not.toBeInTheDocument();
  });

  it("test_copy_missing_clipboard_shows_manual_fallback_not_copied", async () => {
    stubClipboard(undefined); // plain-HTTP LAN host: no Clipboard API at all
    render(
      <OnboardSuccess company={COMPANY} plaintextPassword="Sup3r-Secret!" onDone={() => {}} />,
    );

    fireEvent.click(screen.getAllByRole("button", { name: "Copy" })[1]);

    expect(await screen.findByText(/Copy failed — select the text manually/)).toBeInTheDocument();
    expect(screen.queryByText("Copied")).not.toBeInTheDocument();
  });
});
