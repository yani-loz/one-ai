/**
 * Tests for the generic confirm modal — render, the dismiss paths (Cancel button + backdrop
 * click, the latter exercising the pointer-events pass-through fix), the confirm action, the
 * busy-disabled state, and the error message. Portaled to document.body, so screen queries it.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { ConfirmDialog } from "./ConfirmDialog";

function setup(overrides: Partial<React.ComponentProps<typeof ConfirmDialog>> = {}) {
  const props = {
    open: true,
    title: "Deactivate Bob?",
    message: "They lose access immediately.",
    confirmLabel: "Deactivate",
    tone: "danger" as const,
    busy: false,
    errorMessage: null,
    onConfirm: vi.fn(),
    onCancel: vi.fn(),
    ...overrides,
  };
  render(<ConfirmDialog {...props} />);
  return props;
}

describe("ConfirmDialog", () => {
  it("test_renders_title_and_message_when_open", () => {
    setup();

    expect(screen.getByRole("dialog", { name: "Deactivate Bob?" })).toBeInTheDocument();
    expect(screen.getByText("They lose access immediately.")).toBeInTheDocument();
  });

  it("test_confirm_button_calls_on_confirm", () => {
    const props = setup();

    fireEvent.click(screen.getByRole("button", { name: "Deactivate" }));

    expect(props.onConfirm).toHaveBeenCalledTimes(1);
  });

  it("test_cancel_button_calls_on_cancel", () => {
    const props = setup();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(props.onCancel).toHaveBeenCalledTimes(1);
  });

  it("test_backdrop_click_calls_on_cancel", () => {
    const props = setup();

    fireEvent.click(screen.getByTestId("confirm-backdrop"));

    expect(props.onCancel).toHaveBeenCalledTimes(1);
  });

  it("test_busy_disables_the_confirm_button", () => {
    setup({ busy: true });

    expect(screen.getByRole("button", { name: /Working/ })).toBeDisabled();
  });

  it("test_error_message_is_shown", () => {
    setup({ errorMessage: "Must keep one administrator." });

    expect(screen.getByRole("alert")).toHaveTextContent("Must keep one administrator.");
  });
});
