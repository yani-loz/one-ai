// Vitest setup: register jest-dom matchers (toBeInTheDocument, toHaveTextContent…).
import "@testing-library/jest-dom";

// jsdom has no Canvas 2D implementation; stub getContext to null so canvas-based
// components (BrandMark / AgentInsignia) take their graceful "no context" path instead of logging
// jsdom's noisy "not implemented" error on every render.
HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext;
