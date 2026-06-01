/** Tests for the dialog-a11y hook — focus moves in on activate, Escape closes, Tab and
 *  Shift+Tab wrap within the container, and focus is restored on deactivate. Drives the
 *  container's native keydown listener with fireEvent (deterministic, no jsdom tab order). */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useDialogA11y } from "./useDialogA11y";

function Harness({ active, onClose }: { active: boolean; onClose: () => void }): React.JSX.Element {
  const ref = useDialogA11y<HTMLDivElement>(active, onClose);
  return (
    <div ref={ref} data-testid="dialog">
      <button type="button">first</button>
      <button type="button">middle</button>
      <button type="button">last</button>
    </div>
  );
}

describe("useDialogA11y", () => {
  it("test_focus_moves_to_first_focusable_on_activate", () => {
    render(<Harness active onClose={vi.fn()} />);

    expect(screen.getByText("first")).toHaveFocus();
  });

  it("test_escape_calls_on_close", () => {
    const onClose = vi.fn();
    render(<Harness active onClose={onClose} />);

    fireEvent.keyDown(screen.getByTestId("dialog"), { key: "Escape" });

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("test_tab_at_last_wraps_to_first", () => {
    render(<Harness active onClose={vi.fn()} />);
    screen.getByText("last").focus();

    fireEvent.keyDown(screen.getByText("last"), { key: "Tab" });

    expect(screen.getByText("first")).toHaveFocus();
  });

  it("test_shift_tab_at_first_wraps_to_last", () => {
    render(<Harness active onClose={vi.fn()} />);
    screen.getByText("first").focus();

    fireEvent.keyDown(screen.getByText("first"), { key: "Tab", shiftKey: true });

    expect(screen.getByText("last")).toHaveFocus();
  });

  it("test_focus_restored_to_prior_element_on_deactivate", () => {
    const { rerender } = render(
      <>
        <button type="button">outside</button>
        <Harness active={false} onClose={vi.fn()} />
      </>,
    );
    screen.getByText("outside").focus();

    rerender(
      <>
        <button type="button">outside</button>
        <Harness active onClose={vi.fn()} />
      </>,
    );
    expect(screen.getByText("first")).toHaveFocus();

    rerender(
      <>
        <button type="button">outside</button>
        <Harness active={false} onClose={vi.fn()} />
      </>,
    );
    expect(screen.getByText("outside")).toHaveFocus();
  });
});
