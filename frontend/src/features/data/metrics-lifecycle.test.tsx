import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { MetricsPage } from "./metrics";
beforeEach(() =>
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  ),
);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const metric = {
  name: "Safety",
  kind: "rubric",
  rules: { should: ["Use facts"], should_not: [] },
};
function mount(get: any) {
  const api = { get, post: vi.fn(), put: vi.fn(), del: vi.fn() };
  const studio = {
    api,
    scopeKey: "u:p",
    project: "p",
    route: "/metrics",
    user: { id: "u", role: "member" },
    evidence: new EvidenceRegistry(),
    overview: { tree: { datasets: [] } },
  } as any;
  render(
    <StudioContext.Provider value={studio}>
      <MetricsPage />
    </StudioContext.Provider>,
  );
  return api;
}
it("distinguishes initial permission failure from an empty catalog and retries only the read", async () => {
  const get = vi
    .fn()
    .mockRejectedValueOnce(
      Object.assign(new Error("Metric access denied"), { status: 403 }),
    )
    .mockResolvedValueOnce([metric]);
  const api = mount(get);
  expect(
    await screen.findByRole("heading", { name: "Metrics could not be loaded" }),
  ).toBeInTheDocument();
  expect(screen.queryByText("No metrics yet.")).toBeNull();
  expect(screen.getAllByRole("button", { name: "Retry metrics" })).toHaveLength(
    1,
  );
  expect(screen.queryByText(/0 rubrics/)).toBeNull();
  expect(screen.queryByRole("button", { name: "Refresh metrics" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry metrics" }));
  expect(
    await screen.findByRole("button", { name: "Safety" }),
  ).toBeInTheDocument();
  expect(get).toHaveBeenCalledTimes(2);
  expect(get.mock.calls.every(([path]) => path === "/api/metrics")).toBe(true);
  expect(api.post).not.toHaveBeenCalled();
  expect(api.put).not.toHaveBeenCalled();
  expect(api.del).not.toHaveBeenCalled();
});
it("retains inspected metric rules while a refresh is pending or fails, then recovers through retry", async () => {
  let reject!: (error: Error) => void;
  const get = vi
    .fn()
    .mockResolvedValueOnce([metric])
    .mockImplementationOnce(
      () =>
        new Promise((_resolve, fail) => {
          reject = fail;
        }),
    )
    .mockResolvedValueOnce([metric]);
  const api = mount(get);
  fireEvent.click(await screen.findByRole("button", { name: "Safety" }));
  const inspector = screen.getByRole("complementary", { name: "Safety" });
  fireEvent.click(screen.getByRole("button", { name: "Refresh metrics" }));
  expect(screen.getByRole("button", { name: "Safety" })).toBeInTheDocument();
  expect(screen.getByText("Use facts")).toBeInTheDocument();
  expect(
    within(inspector).getByRole("button", { name: "Edit rules" }),
  ).toBeEnabled();
  expect(
    inspector.querySelector('[data-evidence-label="Metric definition"]'),
  ).toHaveTextContent("Use facts");
  expect(
    screen.getByRole("status", { name: "Metric read status" }),
  ).toHaveTextContent("Refreshing metrics");
  await act(async () => reject(new Error("Metrics offline")));
  expect(await screen.findByRole("alert")).toHaveTextContent("Metrics offline");
  expect(screen.getByRole("complementary", { name: "Safety" })).toBe(inspector);
  expect(
    within(inspector).getByRole("button", { name: "Edit rules" }),
  ).toBeEnabled();
  expect(
    screen.getAllByRole("button", { name: "Refresh metrics" }),
  ).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "Retry metrics" })).toBeNull();
  expect(
    screen.getByRole("table", { name: "Metrics" }).closest(".data-master"),
  ).toHaveAttribute("aria-busy", "false");
  expect(screen.getByText("Use facts")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Refresh metrics" }));
  await waitFor(() => expect(get).toHaveBeenCalledTimes(3));
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  expect(api.post).not.toHaveBeenCalled();
  expect(api.put).not.toHaveBeenCalled();
  expect(api.del).not.toHaveBeenCalled();
});
it("distinguishes a successful empty catalog from no search matches and clears filters without fetching", async () => {
  const get = vi.fn().mockResolvedValue([metric]);
  mount(get);
  await screen.findByRole("button", { name: "Safety" });
  fireEvent.change(screen.getByRole("searchbox", { name: "Find a metric" }), {
    target: { value: "missing" },
  });
  expect(
    screen.getByRole("heading", { name: "No matching metrics" }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Clear metric filters" }));
  expect(screen.getByRole("button", { name: "Safety" })).toBeInTheDocument();
  expect(get).toHaveBeenCalledTimes(1);
});
it("announces an initial pending read before reporting a genuinely empty metric catalog", async () => {
  let resolve!: (rows: unknown[]) => void;
  mount(
    vi.fn(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    ),
  );
  expect(
    screen.getByRole("status", { name: "Metric read status" }),
  ).toHaveTextContent("Loading metrics");
  const table = screen.getByRole("table", { name: "Metrics" });
  expect(table.closest(".data-master")).toHaveAttribute("aria-busy", "true");
  expect(screen.queryByRole("heading", { name: "No metrics yet" })).toBeNull();
  await act(async () => resolve([]));
  expect(
    await screen.findByRole("heading", { name: "No metrics yet" }),
  ).toBeInTheDocument();
  expect(table.closest(".data-master")).toHaveAttribute("aria-busy", "false");
  expect(screen.queryByRole("button", { name: "Retry metrics" })).toBeNull();
});
