import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

function mockFetch(impl: () => Promise<unknown>): void {
  // `unknown` + double-cast is the canonical way to stand a vi.fn() in for the
  // structural `fetch` global without re-declaring its full overloaded signature.
  vi.stubGlobal("fetch", vi.fn(impl) as unknown as typeof fetch);
}

describe("App", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the One AI brand while the health probe is pending", () => {
    mockFetch(() => new Promise(() => {}));

    render(<App />);

    expect(screen.getByText("One AI")).toBeInTheDocument();
    expect(screen.getByText("One Company. One AI.")).toBeInTheDocument();
  });

  it("shows online status when the backend health probe succeeds", async () => {
    mockFetch(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ service: "One AI", version: "0.1.0", database: "reachable" }),
      }),
    );

    render(<App />);

    await waitFor(() =>
      expect(screen.getByTestId("health-detail")).toHaveTextContent("DB reachable"),
    );
  });

  it("shows offline status when the backend is unreachable", async () => {
    mockFetch(() => Promise.reject(new Error("network down")));

    render(<App />);

    await waitFor(() =>
      expect(screen.getByTestId("health-detail")).toHaveTextContent("unreachable"),
    );
  });
});
