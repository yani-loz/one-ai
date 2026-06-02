/** Tests for the role pill — known roles render their label; an unknown value falls back. */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RoleBadge } from "./RoleBadge";

describe("RoleBadge", () => {
  it("test_company_admin_renders_administrator_label", () => {
    render(<RoleBadge role="company_admin" />);

    expect(screen.getByText("Administrator")).toBeInTheDocument();
  });

  it("test_member_renders_member_label", () => {
    render(<RoleBadge role="member" />);

    expect(screen.getByText("Member")).toBeInTheDocument();
  });

  it("test_unknown_role_falls_back_to_unknown", () => {
    render(<RoleBadge role="superuser" />);

    expect(screen.getByText("Unknown")).toBeInTheDocument();
  });
});
