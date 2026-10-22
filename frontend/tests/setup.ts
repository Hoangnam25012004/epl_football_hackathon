import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom has no ResizeObserver (the overlay stage measures its box)
class NoResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
globalThis.ResizeObserver ??= NoResizeObserver as unknown as typeof ResizeObserver;

afterEach(() => {
  cleanup();
  try {
    window.localStorage.clear();
  } catch {
    /* jsdom without storage */
  }
});
