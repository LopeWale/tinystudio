import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { EvaluationPage } from "./evaluations";
import { FinetuningPage } from "./training";
import { MetricsPage } from "./metrics";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { TrainingChart } from "./training-chart";
import { useAnalyticalResource } from "./analytical-resource";
afterEach(cleanup);
const studio = (get = vi.fn()) =>
  ({
    api: { get },
    scopeKey: "user:project",
    project: "project",
    user: { id: "user" },
    route: "/finetuning/job",
    evidence: new EvidenceRegistry(),
  }) as any;
const runs = [
  {
    number: 1,
    loss: [
      { step: 0, loss: 2 },
      { step: 100 },
      { step: 200, loss: 0, eval_loss: 0.25 },
    ],
    checkpoints: [200],
  },
  {
    number: 2,
    loss: [
      { step: 0, loss: 1.5 },
      { step: 100, loss: 0.7 },
    ],
    checkpoints: [],
  },
];
const jobRows = [
  {
    job: { name: "alpha", snapshot: "shared", goal: "Extract fields" },
    experiments: [
      {
        number: 1,
        state: "succeeded",
        config: { base_model: "model-a", training_type: "lora", epochs: 3 },
        metrics: { eval_loss: 0 },
        train_loss: 0.5,
      },
    ],
  },
  {
    job: { name: "beta", snapshot: "different" },
    experiments: [
      {
        number: 1,
        state: "pending",
        config: { base_model: "model-b", training_type: "full", epochs: 2 },
      },
      {
        number: 2,
        state: "pending",
        config: { base_model: "model-b", training_type: "lora", epochs: 1 },
      },
    ],
  },
];
function trainingDirectory() {
  const get = vi.fn(async (path: string) =>
    path === "/api/jobs"
      ? jobRows
      : path === "/api/autopilot"
        ? []
        : jobRows.find((item) => path === `/api/jobs/${item.job.name}`),
  );
  const view = render(
    <StudioContext.Provider value={studio(get)}>
      <FinetuningPage />
    </StudioContext.Provider>,
  );
  return { ...view, get };
}
describe("operational training directory", () => {
  it("uses the shared fill table with search, state and comparison in its fixed command row", async () => {
    const view = trainingDirectory();
    const table = await screen.findByRole("table", { name: "Finetuning jobs" });
    const root = table.closest(".data-table")!;
    expect(root).toHaveAttribute("data-table-layout", "fill");
    expect(
      view.container.querySelector(".training-collection-page"),
    ).toHaveClass("collection-page");
    expect(root.querySelector(".data-table")).toBeNull();
    const commands = within(
      screen.getByRole("group", { name: "Finetuning jobs table controls" }),
    );
    expect(
      commands.getByRole("searchbox", { name: "Find a training job" }),
    ).toBeInTheDocument();
    expect(commands.getByLabelText("Run state")).toBeInTheDocument();
    expect(
      commands.getByRole("button", { name: "Compare selected (0)" }),
    ).toBeDisabled();
    expect(screen.getByLabelText("Finetuning jobs rows per page")).toHaveValue(
      "fit",
    );
    expect(root.querySelector(".data-table__footer")?.parentElement).toBe(root);
  });
  it("keeps optional autopilot actions reachable in their bounded disclosure", async () => {
    const get = vi.fn(async (path: string) =>
      path === "/api/jobs"
        ? jobRows
        : path === "/api/autopilot"
          ? [{ name: "cycle-a", dataset: "source", cycles: 1 }]
          : [],
    );
    render(
      <StudioContext.Provider value={studio(get)}>
        <FinetuningPage />
      </StudioContext.Provider>,
    );
    const summary = await screen.findByText("Autopilot (1)");
    expect(summary.closest("details")?.open).toBe(false);
    fireEvent.click(summary);
    expect(summary.closest("details")?.open).toBe(true);
    const table = screen.getByRole("table", { name: "Autopilot" });
    expect(table.closest(".data-table")).toHaveAttribute(
      "data-table-layout",
      "fill",
    );
    expect(
      within(table).getByRole("button", { name: "Next cycle" }),
    ).toBeInTheDocument();
    fireEvent.click(within(table).getByRole("button", { name: "History" }));
    await screen.findByRole("dialog", { name: "Autopilot · cycle-a" });
    expect(get).toHaveBeenCalledWith("/api/autopilot/cycle-a");
  });
  it("inspects a whole row and expands its actual experiment evidence", async () => {
    const { get } = trainingDirectory();
    const row = await screen.findByRole("row", { name: "Training job alpha" });
    fireEvent.click(within(row).getByText("shared"));
    const inspector = screen.getByRole("complementary", {
      name: "Job inspector alpha",
    });
    await waitFor(() =>
      expect(get).toHaveBeenCalledWith(
        "/api/jobs/alpha",
        expect.any(AbortSignal),
      ),
    );
    const summary = within(inspector).getByText("Experiment 1 · succeeded");
    expect(summary.closest("details")?.open).toBe(false);
    fireEvent.click(summary);
    expect(summary.closest("details")?.open).toBe(true);
    expect(within(inspector).getByText("model-a")).toBeTruthy();
    expect(
      within(inspector).getByText("Latest evaluation loss").nextElementSibling
        ?.textContent,
    ).toBe("0");
    expect(
      within(inspector)
        .getByRole("link", { name: "Open run workspace →" })
        .getAttribute("href"),
    ).toBe("#/finetuning/alpha");
  });
  it("supports keyboard row inspection and Escape restores the triggering focus", async () => {
    trainingDirectory();
    const row = await screen.findByRole("row", { name: "Training job beta" });
    row.focus();
    fireEvent.keyDown(row, { key: "Enter" });
    expect(
      screen.getByRole("complementary", { name: "Job inspector beta" }),
    ).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(
      screen.queryByRole("complementary", { name: "Job inspector beta" }),
    ).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(row));
  });
  it("keeps check selection, direct workspace links, and inspection independent", async () => {
    const { get } = trainingDirectory();
    const row = await screen.findByRole("row", { name: "Training job alpha" });
    fireEvent.click(
      within(row).getByRole("checkbox", {
        name: "Select alpha for comparison",
      }),
    );
    expect(screen.queryByRole("complementary")).toBeNull();
    fireEvent.click(within(row).getByRole("link", { name: "alpha" }));
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(get.mock.calls.some(([path]) => path === "/api/jobs/alpha")).toBe(
      false,
    );
    fireEvent.click(screen.getByRole("button", { name: "Inspect beta" }));
    expect(
      screen.getByRole("complementary", { name: "Job inspector beta" }),
    ).toBeTruthy();
    expect(
      (
        screen.getByRole("checkbox", {
          name: "Select alpha for comparison",
        }) as HTMLInputElement
      ).checked,
    ).toBe(true);
  });
  it("sorts by real experiment counts and compares checked jobs across collection filters", async () => {
    trainingDirectory();
    await screen.findByRole("row", { name: "Training job alpha" });
    fireEvent.click(screen.getByRole("button", { name: "Experiments" }));
    const table = screen.getByRole("table", { name: "Finetuning jobs" });
    expect(
      within(table).getAllByRole("row")[1].getAttribute("aria-label"),
    ).toBe("Training job alpha");
    fireEvent.click(screen.getByRole("button", { name: "Experiments" }));
    expect(
      within(table).getAllByRole("row")[1].getAttribute("aria-label"),
    ).toBe("Training job beta");
    expect(
      within(table)
        .getByRole("columnheader", { name: "Experiments" })
        .getAttribute("aria-sort"),
    ).toBe("descending");
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select alpha for comparison" }),
    );
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select beta for comparison" }),
    );
    fireEvent.change(
      screen.getByRole("searchbox", { name: "Find a training job" }),
      { target: { value: "alpha" } },
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Compare selected (2)" }),
    );
    const comparison = screen.getByRole("complementary", {
      name: "Compare selected jobs",
    });
    expect(
      within(comparison).getByText(
        /Snapshots differ; loss values are not directly comparable/,
      ),
    ).toBeTruthy();
    expect(within(comparison).getByText("Experiment 1: 0")).toBeTruthy();
    expect(
      within(comparison).getByText("Experiment 2: Not recorded"),
    ).toBeTruthy();
    expect(
      within(comparison).getByRole("link", { name: "Open beta" }),
    ).toBeTruthy();
  });
});
describe("analytical evidence workspace", () => {
  it("pins exact real steps, preserves zero, and reports missing comparison evidence", () => {
    const inspect = vi.fn();
    render(
      <StudioContext.Provider value={studio()}>
        <TrainingChart experiments={runs} onInspect={inspect} />
      </StudioContext.Provider>,
    );
    const point = screen.getByRole("button", {
      name: "Experiment 1, step 200, train loss 0",
    });
    fireEvent.keyDown(point, { key: "Enter" });
    expect(screen.getByText("Step 200 / matching evidence")).toBeTruthy();
    const table = screen.getByRole("table", { name: "Loss at selected step" });
    expect(within(table).getByText("0.0000")).toBeTruthy();
    expect(within(table).getByText("0.2500")).toBeTruthy();
    expect(within(table).getAllByText("No sample")).toHaveLength(2);
    expect(within(table).getByText("Step 200")).toBeTruthy();
    fireEvent.click(
      within(table).getByRole("button", { name: "Experiment 2" }),
    );
    expect(inspect).toHaveBeenCalledWith(2);
    fireEvent.click(screen.getByRole("button", { name: "Clear pin" }));
    expect(
      screen.queryByRole("table", { name: "Loss at selected step" }),
    ).toBeNull();
  });
  it("does not draw through absent loss and applies zoom only on explicit command", () => {
    const view = render(
      <StudioContext.Provider value={studio()}>
        <TrainingChart experiments={runs} onInspect={() => {}} />
      </StudioContext.Provider>,
    );
    const paths = view.container.querySelectorAll("svg path");
    // The first run has no train loss at step 100; connecting 0 to 200 would invent continuity.
    expect(paths[0].getAttribute("d")).not.toContain("L");
    fireEvent.change(screen.getByLabelText("From step"), {
      target: { value: "100" },
    });
    fireEvent.change(screen.getByLabelText("To step"), {
      target: { value: "200" },
    });
    expect(screen.getByText(/All logged steps/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Apply zoom" }));
    expect(screen.getByText(/Local view: steps 100–200/)).toBeTruthy();
    expect(
      screen.queryByRole("button", {
        name: "Experiment 1, step 0, train loss 2",
      }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reset zoom" }));
    expect(
      screen.getByRole("button", {
        name: "Experiment 1, step 0, train loss 2",
      }),
    ).toBeTruthy();
  });
  it("keeps pinned step identity while runs are hidden and restored", () => {
    render(
      <StudioContext.Provider value={studio()}>
        <TrainingChart experiments={runs} onInspect={() => {}} />
      </StudioContext.Provider>,
    );
    fireEvent.change(screen.getByLabelText("Inspect logged step"), {
      target: { value: "200" },
    });
    const toggles = screen.getAllByRole("button", { name: "Experiment 1" });
    fireEvent.click(toggles[0]);
    expect(screen.getByText("Step 200 / matching evidence")).toBeTruthy();
    expect(
      within(
        screen.getByRole("table", { name: "Loss at selected step" }),
      ).getAllByText("No sample"),
    ).toHaveLength(2);
    fireEvent.click(screen.getAllByRole("button", { name: "Experiment 1" })[0]);
    expect(
      within(
        screen.getByRole("table", { name: "Loss at selected step" }),
      ).getByText("0.0000"),
    ).toBeTruthy();
  });
  it("clears a case inspector when its model filter changes", async () => {
    const get = vi.fn(async () => ({
      summary: {
        snapshot: "v1",
        datapoints: 1,
        state: "succeeded",
        models: ["candidate", "baseline"],
        metrics: ["exact"],
        leaderboard: [],
      },
      rows: [
        {
          datapoint: 0,
          model: "candidate",
          metric: "exact",
          score: 1,
          user: "Original case",
          reference: "yes",
          prediction: "yes",
        },
      ],
    }));
    render(
      <StudioContext.Provider value={studio(get)}>
        <MemoryRouter initialEntries={["/evaluations/trial"]}>
          <Routes>
            <Route path="/evaluations/:name" element={<EvaluationPage />} />
          </Routes>
        </MemoryRouter>
      </StudioContext.Provider>,
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Inspect score for datapoint 1, candidate",
      }),
    );
    expect(
      screen.getByRole("complementary", { name: "Evaluation datapoint 1" }),
    ).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Compare model"), {
      target: { value: "baseline" },
    });
    expect(
      screen.queryByRole("complementary", { name: "Evaluation datapoint 1" }),
    ).toBeNull();
    expect(screen.getByText("Case inspector")).toBeTruthy();
  });
  it("inspects actual metric rules beside the searchable catalog before editing", async () => {
    const context = {
      ...studio(
        vi.fn(async () => [
          {
            name: "Safety",
            kind: "rubric",
            description: "Policy rules",
            rules: {
              should: ["Use the supplied facts"],
              should_not: ["Invent a source"],
            },
          },
        ]),
      ),
      overview: { tree: { datasets: [] } },
    };
    render(
      <StudioContext.Provider value={context}>
        <MetricsPage />
      </StudioContext.Provider>,
    );
    const metric = await screen.findByRole("button", { name: "Safety" });
    const table = screen.getByRole("table", { name: "Metrics" });
    expect(table.closest(".data-table")).toHaveAttribute(
      "data-table-layout",
      "fill",
    );
    expect(screen.getByLabelText("Metrics rows per page")).toHaveValue("fit");
    expect(screen.queryByRole("complementary")).toBeNull();
    const controls = within(
      screen.getByRole("group", { name: "Metrics table controls" }),
    );
    expect(
      controls.getByRole("searchbox", { name: "Find a metric" }),
    ).toBeInTheDocument();
    expect(controls.getByLabelText("Metric kind")).toBeInTheDocument();
    fireEvent.click(metric);
    const inspector = screen.getByRole("complementary", { name: "Safety" });
    const scroll = inspector.querySelector(".analytical-inspector-scroll")!;
    expect(scroll).toBeInTheDocument();
    fireEvent.scroll(scroll, { target: { scrollTop: 12 } });
    expect(
      (scroll.parentElement as HTMLElement).style.getPropertyValue(
        "--analytical-inspector-fade",
      ),
    ).toBe("0.5");
    fireEvent.scroll(scroll, { target: { scrollTop: 0 } });
    expect(
      (scroll.parentElement as HTMLElement).style.getPropertyValue(
        "--analytical-inspector-fade",
      ),
    ).toBe("0");
    expect(within(inspector).getByText("Use the supplied facts")).toBeTruthy();
    expect(within(inspector).getByText("Invent a source")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(
      within(inspector).getByRole("button", { name: "Edit rules" }),
    );
    expect(screen.getByRole("dialog", { name: "Safety" })).toBeTruthy();
    expect(
      (
        screen.getByLabelText(
          "Should follow (one per line)",
        ) as HTMLTextAreaElement
      ).value,
    ).toBe("Use the supplied facts");
  });
  it("retains last evidence during failed refresh and never across project scope", async () => {
    let fail: (error: Error) => void = () => {};
    const get = vi
      .fn()
      .mockResolvedValueOnce({ name: "retained-run" })
      .mockImplementation(
        () =>
          new Promise((_, reject) => {
            fail = reject;
          }),
      );
    function Harness() {
      const r = useAnalyticalResource<{ name: string }>("/api/jobs/run");
      return (
        <>
          <p>{r.data?.name || "No evidence"}</p>
          <p>{r.stale ? "Stale" : "Current"}</p>
          {r.error && <p>{r.error.message}</p>}
          <button onClick={r.reload}>Refresh</button>
        </>
      );
    }
    const context = studio(get);
    const view = render(
      <StudioContext.Provider value={context}>
        <Harness />
      </StudioContext.Provider>,
    );
    await screen.findByText("retained-run");
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(screen.getByText("retained-run")).toBeTruthy();
    await act(async () => fail(Error("Disconnected")));
    expect(screen.getByText("Stale")).toBeTruthy();
    expect(screen.getByText("Disconnected")).toBeTruthy();
    view.rerender(
      <StudioContext.Provider
        value={{ ...context, scopeKey: "user:another-project" }}
      >
        <Harness />
      </StudioContext.Provider>,
    );
    expect(screen.queryByText("retained-run")).toBeNull();
  });
});
