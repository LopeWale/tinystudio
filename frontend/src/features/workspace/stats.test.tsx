import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { ComponentActionRegistry } from "../../controllers/componentActions";
import { ComponentActionsProvider } from "../assistant/actions";
import { StatsPage, requestAxis } from "./stats";

afterEach(cleanup);
const fixture = {
  requests: 10,
  prompt_tokens: 0,
  completion_tokens: 42,
  models_loaded: [],
  training_seconds: 0,
  datasets: 0,
  datapoints: 0,
  snapshots: 2,
  jobs: 1,
  experiments: {},
  evaluations: 0,
  scored_traces: 0,
  requests_per_day: [
    ["2026-10-20", 3],
    ["2026-10-24", 7],
  ],
  requests_per_model: [["org/model", 10]],
};
function setup(data: any = fixture, error?: string) {
  const get = vi.fn(async () => {
    if (error) throw new Error(error);
    return data;
  });
  const studio = {
    api: { get },
    project: "fixture",
    scopeKey: "user:fixture",
    user: { id: "user" },
    route: "/stats",
    evidence: new EvidenceRegistry(),
  } as any;
  const registry = new ComponentActionRegistry();
  registry.setScope({ user: "user", project: "fixture", route: "/stats" });
  const tree = () => (
    <StudioContext.Provider value={studio}>
      <ComponentActionsProvider registry={registry}>
        <StatsPage />
      </ComponentActionsProvider>
    </StudioContext.Provider>
  );
  const view = render(tree());
  return { studio, registry, tree, view, get };
}
describe("Stats request chart", () => {
  it.each([
    [0, 2, [0, 1, 2]],
    [1, 2, [0, 1, 2]],
    [2, 2, [0, 1, 2]],
    [3, 4, [0, 2, 4]],
    [7, 8, [0, 4, 8]],
    [8, 8, [0, 4, 8]],
    [9, 10, [0, 5, 10]],
    [101, 200, [0, 100, 200]],
  ])(
    "uses a truthful integer axis for maximum %s",
    (maximum, ceiling, ticks) => {
      expect(requestAxis(maximum as number)).toEqual({ ceiling, ticks });
    },
  );
  it("preserves counts and date gaps, exposes precise selection, and supports arrow keys and Escape", async () => {
    const { view } = setup();
    const first = await screen.findByRole("button", {
      name: "2026-10-20: 3 requests",
    });
    const second = screen.getByRole("button", {
      name: "2026-10-24: 7 requests",
    });
    const chart = screen.getByRole("group", { name: "Requests per day" });
    expect(within(chart).getByText("4")).toBeInTheDocument();
    expect(within(chart).getByText("8")).toBeInTheDocument();
    expect(view.container.querySelectorAll(".stats-bar")).toHaveLength(2);
    expect(first).toHaveAttribute("tabindex", "0");
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(second).toHaveFocus();
    expect(second).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("7 requests")).toBeInTheDocument();
    fireEvent.keyDown(second, { key: "Home" });
    expect(first).toHaveFocus();
    expect(screen.getByText("3 requests")).toBeInTheDocument();
    fireEvent.keyDown(first, { key: "Escape" });
    expect(first).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("button", { name: "Clear day" })).toBeNull();
  });
  it("draws 3 and 7 against the same eight-request ceiling and preserves a zero-height count", async () => {
    const { view } = setup({
      ...fixture,
      requests_per_day: [["2026-10-19", 0], ...fixture.requests_per_day],
    });
    await screen.findByRole("button", { name: "2026-10-24: 7 requests" });
    const bars = view.container.querySelectorAll(".stats-bar");
    expect(bars[0]).toHaveAttribute("height", "0");
    expect(bars[0]).toHaveAttribute("y", "180");
    expect(bars[1]).toHaveAttribute("height", "61.5");
    expect(bars[1]).toHaveAttribute("y", "118.5");
    expect(bars[2]).toHaveAttribute("height", "143.5");
    expect(bars[2]).toHaveAttribute("y", "36.5");
  });
  it("restores focus to the selected bar when Clear day is removed", async () => {
    setup();
    const day = await screen.findByRole("button", {
      name: "2026-10-24: 7 requests",
    });
    fireEvent.click(day);
    const clear = screen.getByRole("button", { name: "Clear day" });
    clear.focus();
    fireEvent.click(clear);
    expect(day).toHaveFocus();
    expect(day).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("button", { name: "Clear day" })).toBeNull();
  });
  it("identifies invalid-only aggregates without claiming no requests exist", async () => {
    setup({
      ...fixture,
      requests_per_day: [["bad", -1]],
      requests_per_model: [["bad", "7"]],
    });
    expect(
      await screen.findByText("No valid daily request counts are available."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No valid model request counts are available."),
    ).toBeInTheDocument();
    expect(screen.queryByText("No model requests yet.")).toBeNull();
    expect(
      screen.queryByText(
        "No requests yet. Serve a model in the Playground or API.",
      ),
    ).toBeNull();
  });
  it("discloses partially invalid daily and model aggregates while retaining valid counts", async () => {
    setup({
      ...fixture,
      requests_per_day: [...fixture.requests_per_day, ["bad", NaN]],
      requests_per_model: [...fixture.requests_per_model, ["bad", -1]],
    });
    expect(
      await screen.findByRole("button", { name: "2026-10-24: 7 requests" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Some daily request counts are invalid and could not be displayed.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Some model request counts are invalid and could not be displayed.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("org/model")).toBeInTheDocument();
  });
  it("reserves no-requests copy for genuinely empty aggregates", async () => {
    setup({
      ...fixture,
      requests: 0,
      requests_per_day: [],
      requests_per_model: [],
    });
    expect(
      await screen.findByText(
        "No requests yet. Serve a model in the Playground or API.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("No model requests yet.")).toBeInTheDocument();
    expect(screen.queryByText(/invalid and could not/)).toBeNull();
  });
  it("shares the same day selection with the assistant and resets it on project scope changes", async () => {
    const { registry, studio, view, tree } = setup();
    await screen.findByRole("button", { name: "2026-10-20: 3 requests" });
    const action = registry
      .inspect()
      .actions.find((action) => action.id === "stats.day")!;
    await act(async () => {
      await registry.execute(action.ref, "2026-10-24", false);
    });
    expect(
      screen.getByRole("button", { name: "2026-10-24: 7 requests" }),
    ).toHaveAttribute("aria-pressed", "true");
    studio.scopeKey = "user:another";
    view.rerender(tree());
    expect(
      await screen.findByRole("button", { name: "2026-10-24: 7 requests" }),
    ).toHaveAttribute("aria-pressed", "false");
  });
  it("distinguishes missing evidence from known zero and empty model/day aggregates", async () => {
    setup({
      ...fixture,
      requests: undefined,
      requests_per_day: undefined,
      requests_per_model: [],
      datasets: undefined,
    });
    await screen.findByText("Daily request counts are unavailable.");
    expect(screen.getByText("No model requests yet.")).toBeInTheDocument();
    const totals = screen.getByLabelText("Serving totals");
    expect(within(totals).getByText("—")).toBeInTheDocument();
    expect(within(totals).getByText("42")).toBeInTheDocument();
    const inventory = screen.getByLabelText("Workspace totals");
    expect(within(inventory).getByText("—")).toBeInTheDocument();
    expect(within(inventory).getAllByText("0").length).toBeGreaterThan(0);
    expect(screen.getByText("Models loaded now: none")).toBeInTheDocument();
  });
  it("retains the received counts and selection after a failed refresh", async () => {
    const { get } = setup();
    const day = await screen.findByRole("button", {
      name: "2026-10-24: 7 requests",
    });
    fireEvent.click(day);
    get.mockRejectedValueOnce(new Error("Refresh failed"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh statistics" }));
    expect(
      await screen.findByText(
        "Refresh failed · showing last received statistics",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "2026-10-24: 7 requests" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("42")).toBeInTheDocument();
  });
  it("reports failed loading without fabricated totals", async () => {
    setup(undefined, "Stats unavailable");
    expect(
      await screen.findByText("Statistics could not be loaded."),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Serving totals")).toBeNull();
  });
});
