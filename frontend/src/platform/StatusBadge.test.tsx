/** Unit tests for StatusBadge — known labels, case-insensitivity, and the unknown-status
 *  fallback invariant (an unrecognised backend status renders "Unknown", never throws). */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it("test_known_status_renders_its_label", () => {
    render(<StatusBadge status="suspended" />);

    expect(screen.getByText("Suspended")).toBeInTheDocument();
  });

  it("test_status_is_matched_case_insensitively", () => {
    render(<StatusBadge status="ACTIVE" />);

    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("test_unknown_status_falls_back_to_neutral_label", () => {
    render(<StatusBadge status="archived" />);

    expect(screen.getByText("Unknown")).toBeInTheDocument();
  });
});
