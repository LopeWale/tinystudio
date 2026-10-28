import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { StudioContext } from "../src/app/StudioContext";
import { EvidenceRegistry } from "../src/app/evidence";
import { Shell, resolveAppearance } from "../src/app/App";
vi.mock("../src/features/assistant", () => ({
  AssistantProvider: ({ children }: { children: ReactNode }) => children,
  AssistantWidget: () => null,
  AssistantPage: () => null,
  useAssistantDock: () => ({
    expanded: false,
    open: vi.fn(),
    collapse: vi.fn(),
  }),
}));
afterEach(() => {
  cleanup();
  localStorage.clear();
  sessionStorage.clear();
  document.documentElement.classList.remove("dark");
  vi.unstubAllGlobals();
});
function renderShell(systemDark = true) {
  const listeners = new Set<() => void>();
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      media: query,
      matches: query === "(prefers-color-scheme: dark)" && systemDark,
      addEventListener: (_name: string, listener: () => void) =>
        listeners.add(listener),
      removeEventListener: (_name: string, listener: () => void) =>
        listeners.delete(listener),
    })),
  );
  const runtime = {
    user: { id: "fixture", role: "admin" },
    project: "home",
    projects: [{ slug: "home", name: "Home" }],
    scopeKey: "fixture:home",
    route: "/appearance-fixture",
    evidence: new EvidenceRegistry(),
    showTasks: vi.fn(),
    toast: vi.fn(),
  } as any;
  return {
    ...render(
      <StudioContext.Provider value={runtime}>
        <MemoryRouter initialEntries={["/appearance-fixture"]}>
          <Shell />
        </MemoryRouter>
      </StudioContext.Provider>,
    ),
    listeners,
  };
}
describe("appearance cutover compatibility", () => {
  it.each([
    ["light", "dark", "light"],
    ["dark", "light", "dark"],
    ["system", "light", "system"],
    [null, "dark", "dark"],
    ["invalid", "light", "light"],
    ["invalid", "invalid", "system"],
    [null, null, "system"],
  ])("resolves canonical %s and React %s to %s", (canonical, react, expected) =>
    expect(resolveAppearance(canonical, react)).toBe(expected),
  );
  it("renders native Light instead of a stale React System/dark OS, then synchronizes both keys when changed", () => {
    localStorage.setItem("tinystudio.appearance", "light");
    localStorage.setItem("tinystudio.theme", "system");
    const view = renderShell(true);
    fireEvent.click(screen.getByText("Account").closest("summary")!);
    expect(screen.getByRole("combobox", { name: "Appearance" })).toHaveValue(
      "light",
    );
    expect(document.documentElement).not.toHaveClass("dark");
    expect(localStorage.getItem("tinystudio.theme")).toBe("light");
    fireEvent.change(screen.getByRole("combobox", { name: "Appearance" }), {
      target: { value: "dark" },
    });
    expect(document.documentElement).toHaveClass("dark");
    expect(localStorage.getItem("tinystudio.appearance")).toBe("dark");
    expect(localStorage.getItem("tinystudio.theme")).toBe("dark");
    view.unmount();
    expect(view.listeners.size).toBe(0);
  });
  it.each([null, "unsupported"])(
    "uses a valid React preference when canonical preference is %s",
    (canonical) => {
      if (canonical !== null)
        localStorage.setItem("tinystudio.appearance", canonical);
      localStorage.setItem("tinystudio.theme", "light");
      renderShell(true);
      fireEvent.click(screen.getByText("Account").closest("summary")!);
      expect(screen.getByRole("combobox", { name: "Appearance" })).toHaveValue(
        "light",
      );
      expect(document.documentElement).not.toHaveClass("dark");
      expect(localStorage.getItem("tinystudio.appearance")).toBe("light");
    },
  );
  it("uses System and the OS color scheme when neither stored value is valid", () => {
    localStorage.setItem("tinystudio.appearance", "unsupported");
    localStorage.setItem("tinystudio.theme", "unsupported");
    renderShell(true);
    fireEvent.click(screen.getByText("Account").closest("summary")!);
    expect(screen.getByRole("combobox", { name: "Appearance" })).toHaveValue(
      "system",
    );
    expect(document.documentElement).toHaveClass("dark");
    expect(localStorage.getItem("tinystudio.appearance")).toBe("system");
    expect(localStorage.getItem("tinystudio.theme")).toBe("system");
  });
});
