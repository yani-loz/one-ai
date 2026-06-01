/** Unit tests for CompanyCard — renders company metadata, and the created-date fallback
 *  ("—" for an unparseable timestamp rather than "Invalid Date"). Canvas is stubbed to
 *  null in test setup, so the BrandMark crest renders inertly. */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CompanyCard } from "./CompanyCard";
import type { OrganizationSummary } from "./types";

const BASE: OrganizationSummary = {
  id: "org-1",
  name: "Acme GmbH",
  slug: "acme",
  status: "active",
  user_count: 3,
  created_at: "2026-06-01T10:00:00Z",
};

describe("CompanyCard", () => {
  it("test_renders_name_slug_and_seats", () => {
    render(<CompanyCard company={BASE} />);

    expect(screen.getByText("Acme GmbH")).toBeInTheDocument();
    expect(screen.getByText("acme")).toBeInTheDocument();
    expect(screen.getByText(/3 seats/)).toBeInTheDocument();
  });

  it("test_single_seat_is_singular", () => {
    render(<CompanyCard company={{ ...BASE, user_count: 1 }} />);

    expect(screen.getByText(/1 seat\b/)).toBeInTheDocument();
  });

  it("test_invalid_created_at_renders_em_dash", () => {
    render(<CompanyCard company={{ ...BASE, created_at: "not-a-date" }} />);

    expect(screen.getByText(/—/)).toBeInTheDocument();
  });
});
