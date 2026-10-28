import { StrictMode } from "react";
import {
  MemoryRouter,
  Routes,
  Route,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  cleanup,
  within,
} from "@testing-library/react";
import { afterEach, it, expect, vi } from "vitest";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { Evidence } from "../../components/studio/primitives";
import { AssistantProvider, AssistantWidget, AssistantPage } from "./index";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function studio(route: string) {
  return {
    route,
    project: "home",
    scopeKey: "alice:home",
    user: { id: "alice" },
    evidence: new EvidenceRegistry(),
    api: {
      get: vi.fn(async (path: string) =>
        path.endsWith("/models")
          ? {
              anthropic: {
                available: true,
                models: [{ id: "claude-opus-5-5" }],
              },
            }
          : path.endsWith("/config")
            ? { accept: ".txt", max_bytes: 1024 }
            : {
                revision: 1,
                history: [],
                provider: "anthropic",
                model: "claude-opus-5-5",
              },
      ),
    },
    toast: vi.fn(),
    confirm: vi.fn(),
    navigate: vi.fn(),
  } as any;
}
it("collapses the omnipresent dock for picking on a phone and restores the same draft, textarea, caret and focus after select/Escape/cancel", async () => {
  vi.stubGlobal("innerWidth", 390);
  vi.stubGlobal("innerHeight", 844);
  vi.stubGlobal("matchMedia", () => ({
    matches: true,
    addEventListener() {},
    removeEventListener() {},
  }));
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  const runtime = studio("/evaluations/review");
  const row = { datapoint: 42, score: 0.875 };
  render(
    <StrictMode>
      <MemoryRouter initialEntries={["/evaluations/review"]}>
        <StudioContext.Provider value={runtime}>
          <AssistantProvider>
            <main>
              <Evidence label="Evaluation result" data={row}>
                <button type="button">Pick evaluation result 42</button>
              </Evidence>
            </main>
            <AssistantWidget />
          </AssistantProvider>
        </StudioContext.Provider>
      </MemoryRouter>
    </StrictMode>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Open assistant" }));
  await screen.findByText(/Saved · revision 1/);
  const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
  fireEvent.change(textarea, {
    target: { value: "Review this evidence without losing my draft" },
  });
  textarea.focus();
  textarea.setSelectionRange(7, 11);
  fireEvent.click(screen.getByRole("button", { name: "Pick page data" }));
  expect(screen.getByRole("button", { name: "Cancel pick" })).toHaveAttribute(
    "aria-expanded",
    "false",
  );
  expect(textarea.isConnected).toBe(true);
  expect(
    screen.getByRole("button", { name: "Cancel pick" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Pick evaluation result 42" }),
  ).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "Pick evaluation result 42" }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Open assistant" }),
    ).toHaveAttribute("aria-expanded", "true"),
  );
  expect(screen.getByText("Picked: Evaluation result")).toBeInTheDocument();
  expect(screen.getByLabelText("Message")).toBe(textarea);
  expect(textarea.value).toBe("Review this evidence without losing my draft");
  expect(document.activeElement).toBe(textarea);
  expect([textarea.selectionStart, textarea.selectionEnd]).toEqual([7, 11]);
  fireEvent.click(screen.getByRole("button", { name: "Pick page data" }));
  fireEvent.keyDown(document, { key: "Escape" });
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Open assistant" }),
    ).toHaveAttribute("aria-expanded", "true"),
  );
  expect(document.activeElement).toBe(textarea);
  expect([textarea.selectionStart, textarea.selectionEnd]).toEqual([7, 11]);
  fireEvent.click(screen.getByRole("button", { name: "Pick page data" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel pick" }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Open assistant" }),
    ).toHaveAttribute("aria-expanded", "true"),
  );
  expect(screen.getByLabelText("Message")).toBe(textarea);
  expect(document.activeElement).toBe(textarea);
  expect(textarea.value).toBe("Review this evidence without losing my draft");
});
it("honors the user's omnipresent-assistant requirement: legacy /assistant returns to research where dock evidence remains pickable", async () => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  const runtime = studio("/assistant");
  let routeNavigate!: ReturnType<typeof useNavigate>;
  const navigate = vi.fn((hash: string) =>
    routeNavigate(hash.replace(/^#/, "")),
  );
  function CurrentWorkspace() {
    const location = useLocation();
    routeNavigate = useNavigate();
    return (
      <StudioContext.Provider
        value={{ ...runtime, route: location.pathname, navigate }}
      >
        <AssistantProvider>
          <Routes>
            <Route path="/assistant" element={<AssistantPage />} />
            <Route
              path="/evaluations"
              element={
                <main>
                  <h1>Evaluation research</h1>
                  <Evidence label="Research evaluation" data={{ score: 0.875 }}>
                    <button type="button">Research evidence row</button>
                  </Evidence>
                </main>
              }
            />
          </Routes>
          <AssistantWidget />
        </AssistantProvider>
      </StudioContext.Provider>
    );
  }
  render(
    <MemoryRouter initialEntries={["/assistant"]}>
      <CurrentWorkspace />
    </MemoryRouter>,
  );
  expect(
    await screen.findByRole("heading", { name: "Evaluation research" }),
  ).toBeInTheDocument();
  expect(navigate).toHaveBeenCalledWith("#/evaluations");
  expect(
    screen.queryByRole("button", { name: "Return to research to pick data" }),
  ).toBeNull();
  expect(screen.getAllByLabelText("Message")).toHaveLength(1);
  await screen.findByText(/Saved · revision 1/);
  fireEvent.click(screen.getByRole("button", { name: "Pick page data" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Research evidence row" }),
  );
  expect(
    await screen.findByText("Picked: Research evaluation"),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Open assistant" }),
  ).toHaveAttribute("aria-expanded", "true");
});

it("keeps one mounted floating composer across close, page navigation and Escape without trapping page interaction", async () => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  const runtime = studio("/datasets");
  function RoutedWorkspace() {
    const location = useLocation(),
      navigate = useNavigate();
    return (
      <StudioContext.Provider value={{ ...runtime, route: location.pathname }}>
        <AssistantProvider>
          <main>
            <h1>{location.pathname}</h1>
            <button onClick={() => navigate("/models")}>
              Open models page
            </button>
          </main>
          <AssistantWidget />
        </AssistantProvider>
      </StudioContext.Provider>
    );
  }
  render(
    <MemoryRouter initialEntries={["/datasets"]}>
      <RoutedWorkspace />
    </MemoryRouter>,
  );
  const launcher = screen.getByRole("button", { name: "Open assistant" });
  expect(launcher).toHaveAttribute("aria-expanded", "false");
  expect(launcher).toHaveClass("assistant-launcher");
  expect(
    screen.queryByRole("complementary", { name: "Workspace assistant" }),
  ).toBeNull();
  fireEvent.click(launcher);
  await screen.findByText(/Saved · revision 1/);
  const panel = screen.getByRole("complementary", {
    name: "Workspace assistant",
  });
  expect(panel).not.toHaveAttribute("aria-modal");
  expect(screen.queryByRole("dialog")).toBeNull();
  const textarea = screen.getByRole("textbox", {
    name: "Message",
  }) as HTMLTextAreaElement;
  expect(within(panel).queryByRole("button", { name: "Provider" })).toBeNull();
  fireEvent.change(textarea, {
    target: { value: "Keep my question across pages" },
  });
  textarea.focus();
  textarea.setSelectionRange(5, 9);
  fireEvent.click(screen.getByRole("button", { name: "Close assistant" }));
  expect(document.activeElement).toBe(launcher);
  expect(textarea.isConnected).toBe(true);
  expect(panel).toHaveAttribute("hidden");
  fireEvent.click(screen.getByRole("button", { name: "Open models page" }));
  await screen.findByRole("heading", { name: "/models" });
  expect(launcher).toBe(screen.getByRole("button", { name: "Open assistant" }));
  fireEvent.click(launcher);
  expect(screen.getByRole("textbox", { name: "Message" })).toBe(textarea);
  expect(textarea).toHaveValue("Keep my question across pages");
  expect([textarea.selectionStart, textarea.selectionEnd]).toEqual([5, 9]);
  expect(document.activeElement).toBe(textarea);
  expect(screen.getByText("Context · /models")).toBeInTheDocument();
  // Page controls remain reachable while the nonmodal card is expanded.
  const pageButton = screen.getByRole("button", { name: "Open models page" });
  pageButton.focus();
  expect(document.activeElement).toBe(pageButton);
  expect(launcher).toHaveAttribute("aria-expanded", "true");
  textarea.focus();
  fireEvent.keyDown(textarea, { key: "Escape" });
  expect(launcher).toHaveAttribute("aria-expanded", "false");
  expect(document.activeElement).toBe(launcher);
  expect(textarea.isConnected).toBe(true);
});
