/** Unit tests for CompanyCard — renders company metadata, links to the detail screen, and
 *  the created-date fallback ("—" for an unparseable timestamp). Wrapped in MemoryRouter
 *  because the card is a <Link>. Canvas is stubbed to null in setup (BrandMark inert). */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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

function renderCard(company: OrganizationSummary) {
  return render(
    <MemoryRouter>
      <CompanyCard company={company} />
    </MemoryRouter>,
  );
}

describe("CompanyCard", () => {
  it("test_renders_name_slug_and_seats", () => {
    renderCard(BASE);

    expect(screen.getByText("Acme GmbH")).toBeInTheDocument();
    expect(screen.getByText("acme")).toBeInTheDocument();
    expect(screen.getByText(/3 seats/)).toBeInTheDocument();
  });

  it("test_links_to_the_detail_screen", () => {
    renderCard(BASE);

    expect(screen.getByRole("link")).toHaveAttribute("href", "/platform/orgs/org-1");
  });

  it("test_single_seat_is_singular", () => {
    renderCard({ ...BASE, user_count: 1 });

    expect(screen.getByText(/1 seat\b/)).toBeInTheDocument();
  });

  it("test_invalid_created_at_renders_em_dash", () => {
    renderCard({ ...BASE, created_at: "not-a-date" });

    expect(screen.getByText(/—/)).toBeInTheDocument();
  });
});
