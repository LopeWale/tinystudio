import "@testing-library/jest-dom/vitest";

// JSDOM has no layout observer. Component contract tests control geometry explicitly;
// rendered dimensions are verified in a real browser, not by this no-layout shim.
if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as typeof ResizeObserver;
}
