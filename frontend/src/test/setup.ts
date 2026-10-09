import "@testing-library/jest-dom/vitest";

// jsdom does not implement ResizeObserver, which Recharts' ResponsiveContainer
// requires. A no-op observer is enough for the tests: the charts are declared
// present and their data tables are asserted instead of their pixel geometry.
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

if (!("ResizeObserver" in globalThis)) {
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver = ResizeObserverStub;
}
