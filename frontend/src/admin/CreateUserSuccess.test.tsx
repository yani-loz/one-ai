/**
 * Tests for the create-user success / credential hand-off. The form→success swap keeps the drawer
 * `open`, so the shared focus trap (keyed on `open`) does not re-fire; this panel must re-assert
 * focus to its heading on mount so a keyboard/screen-reader admin is not stranded on <body>.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

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

describe("CreateUserSuccess", () => {
  it("test_focuses_heading_on_mount_to_restore_dialog_focus", () => {
    render(<CreateUserSuccess user={USER} plaintextPassword="Sup3r-Strong!" onDone={() => {}} />);

    // The heading carries focus (restores the trap + announces "added"); the credentials are NOT
    // in a live region, so a screen reader does not auto-read the plaintext password.
    expect(screen.getByRole("heading", { name: /added/i })).toHaveFocus();
  });
});
