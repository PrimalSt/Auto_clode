import "@testing-library/dom";
import { vi } from "vitest";

// jsdom не умеет matchMedia и ResizeObserver, а Mantine их спрашивает
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(window as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

// Monaco и его воркеры в jsdom не работают: тестам форм хватает заглушки
vi.mock("../shared/monaco/setup", () => ({ EDITOR_OPTIONS: {}, SCENARIO_MODEL_PREFIX: "file:///scenario-", setScenarioSchema: () => {} }));
