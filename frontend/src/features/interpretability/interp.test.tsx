import { Profiler, StrictMode, type ReactNode } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { CapturePage, ComparisonPage, InterpretabilityPage } from "./index";

const capture = "smol-lora-1--commands";
const captureBase = `/api/interp/captures/${capture}`;
function token(
  datapoint: number,
  user: string,
  value: number,
  regime: string,
  labels: string[],
  position = 0,
) {
  return {
    datapoint,
    user,
    value,
    regime,
    labels,
    position,
    g: regime.startsWith("+") ? 2 : -2,
    v: regime.endsWith("+") ? Math.abs(value) / 2 : -Math.abs(value) / 2,
    first_token: position === 0,
    before: "user: ",
    token: "Ġlights",
    after: " assistant:",
    bucket: "validation",
  };
}
const openTokens = [
  token(101, "Turn on the kitchen lights", 12, "+/+", ["lights"]),
  token(101, "Turn on the kitchen lights", 11, "+/+", ["lights"], 1),
  token(202, "Set a ten minute timer", 10, "+/+", ["timers"]),
];
// Forty retained positive tokens represent three datapoints. Thirty-nine light
// tokens still give lights 2/3 distinct datapoints, never 39/40 or 40/40.
const positiveTokens = [
  ...Array.from({ length: 37 }, (_, index) =>
    token(
      index === 36 ? 303 : 101,
      index === 36 ? "Dim the desk lamp" : "Repeated light datapoint",
      100 - index,
      "-/-",
      ["lights"],
      index,
    ),
  ),
  ...openTokens,
];
function neuronView(neuron = 433) {
  return {
    neuron,
    topk: 40,
    stats: {
      max: 100,
      open_max: 12,
      min: -5,
      density: 0.1,
      first_token_peak: true,
      echo_top: true,
    },
    gate_open: openTokens,
    positives: positiveTokens,
    negatives: [
      token(404, "Do not unlock the front door", -5, "+/-", ["safety"]),
    ],
    datapoints: {
      gate_open: [101, 202],
      positives: [101, 202, 303],
      negatives: [404],
    },
    regimes: { "+/+": 3, "+/-": 0, "-/+": 0, "-/-": 37 },
    label_share: [
      ["lights", 0.5],
      ["timers", 0.5],
    ],
    label_share_all: [
      ["lights", 2 / 3],
      ["timers", 1 / 3],
    ],
    explanation: null,
    test: null,
  };
}
const captureMeta = {
  name: capture,
  model_id: "smol-lora-1",
  source: "commands",
  rows: 500,
  tokens: 10000,
  layers: [14, 15],
  neurons_per_layer: 1000,
  topk: 40,
  prefix_len: 12,
  layer_stats: {
    "15": {
      max_median: 1,
      first_token_peaks: 2,
      never_open: 3,
      echo_share_mean: 0.2,
    },
  },
};
const captureRows = [
  {
    neuron: 433,
    max: 100,
    open_max: 12,
    min: -5,
    density: 0.1,
    echo_share: 0.925,
    echo_top: true,
    explanation: "Lighting and gate evidence",
  },
  {
    neuron: 434,
    max: 22,
    open_max: 20,
    min: -2,
    density: 0.2,
    echo_share: 0,
    explanation: "Independent second neuron",
  },
];
function captureApi(
  override?: (path: string, signal?: AbortSignal) => Promise<any> | undefined,
) {
  return {
    get: vi.fn((path: string, signal?: AbortSignal) => {
      const custom = override?.(path, signal);
      if (custom) return custom;
      if (path === captureBase) return Promise.resolve(captureMeta);
      if (
        path.startsWith(captureBase + "/layers/") &&
        path.includes("/neurons/")
      ) {
        const n = Number(path.match(/neurons\/(\d+)/)![1]);
        return Promise.resolve(neuronView(n));
      }
      if (path.startsWith(captureBase + "/layers/"))
        return Promise.resolve({ rows: captureRows, total: 2 });
      throw new Error(`Unexpected fixture API ${path}`);
    }),
    post: vi.fn(),
    put: vi.fn(),
    del: vi.fn(),
  };
}
function runtime(api: any) {
  return {
    api,
    user: { id: "alice" },
    project: "home",
    scopeKey: "alice:home",
    route: "",
    evidence: new EvidenceRegistry(),
    overview: { tree: { datasets: [] } },
    toast: vi.fn(),
    confirm: vi.fn(async () => true),
    trackTask: vi.fn(),
    showTasks: vi.fn(),
    navigate: vi.fn(),
    refreshOverview: vi.fn(),
  } as unknown as StudioRuntime;
}
function RoutedStudio({
  studio,
  children,
}: {
  studio: StudioRuntime;
  children: ReactNode;
}) {
  const location = useLocation(),
    route = location.pathname + location.search;
  return (
    <StudioContext.Provider value={{ ...studio, route }}>
      <output aria-label="Current route">{route}</output>
      {children}
    </StudioContext.Provider>
  );
}
function mount(studio: StudioRuntime, entry: string) {
  return render(
    <StrictMode>
      <MemoryRouter initialEntries={[entry]}>
        <RoutedStudio studio={studio}>
          <Routes>
            <Route path="/interp" element={<InterpretabilityPage />} />
            <Route path="/interp/c/:capture" element={<CapturePage />} />
            <Route path="/interp/d/:comparison" element={<ComparisonPage />} />
          </Routes>
        </RoutedStudio>
      </MemoryRouter>
    </StrictMode>,
  );
}
beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("interpretability artifact collections", () => {
  it("sorts numeric datapoints while retaining artifact links, evidence identity and deletion", async () => {
    const captures = [
      {
        name: "small",
        model_id: "model-a",
        source: "commands",
        rows: 2,
        layers: [1],
      },
      {
        name: "large",
        model_id: "model-b",
        source: "commands",
        rows: 100,
        layers: [1, 2],
      },
    ];
    const api = captureApi((path) =>
      path === "/api/interp"
        ? Promise.resolve({ captures, comparisons: [] })
        : undefined,
    );
    const studio = runtime(api);
    mount(studio, "/interp");
    const table = await screen.findByRole("table", {
      name: "Activation captures",
    });
    await within(table).findByRole("link", { name: "large" });
    fireEvent.click(within(table).getByRole("button", { name: "Datapoints" }));
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("small");
    fireEvent.click(within(table).getByRole("button", { name: "Datapoints" }));
    const first = within(table).getAllByRole("row")[1];
    expect(first).toHaveTextContent("large");
    expect(first).toHaveAttribute("data-evidence-label", "Activation capture");
    expect(
      within(first).getByRole("link", { name: "Explore neurons →" }),
    ).toHaveAttribute("href", "/interp/c/large");
    expect(studio.evidence.snapshot()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          label: "Activation capture",
          data: captures[1],
        }),
      ]),
    );
    fireEvent.click(within(first).getByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect(api.del).toHaveBeenCalledWith("/api/interp/captures/large"),
    );
  });
});

describe("capture pages through their rendered user path", () => {
  it("composes real layer controls and selected evidence in the shared analytical slots", async () => {
    mount(runtime(captureApi()), `/interp/c/${capture}?layer=15&neuron=433`);
    const heading = await screen.findByRole("heading", { name: "Neuron 433" });
    const template = screen.getByRole("region", { name: "Capture evidence" });
    expect(template).toHaveAttribute("data-template", "analysis");
    expect(
      template.querySelector(".studio-template__toolbar"),
    ).toContainElement(screen.getByLabelText("Sort neurons"));
    expect(
      template.querySelector(".studio-template__summary"),
    ).toHaveTextContent("shared-prefix tokens excluded");
    expect(
      screen.getByRole("complementary", { name: "Capture evidence details" }),
    ).toContainElement(heading);
    expect(
      template.querySelector(".studio-template__content"),
    ).toContainElement(screen.getByRole("table", { name: "Layer neurons" }));
    fireEvent.change(screen.getByLabelText("Find neuron or explanation"), {
      target: { value: "433" },
    });
    expect(screen.getByLabelText("Current route")).toHaveTextContent("q=433");
    expect(
      screen.getByRole("complementary", { name: "Capture evidence details" }),
    ).toContainElement(screen.getByRole("heading", { name: "Neuron 433" }));
  });

  it("pins exact returned token context and clears it without changing neuron selection", async () => {
    mount(runtime(captureApi()), `/interp/c/${capture}?layer=15&neuron=433`);
    await screen.findByRole("heading", { name: "Neuron 433" });
    fireEvent.click(
      screen.getByRole("button", { name: "Pin datapoint 101 token 1" }),
    );
    const pinned = screen.getByRole("complementary", {
      name: "Pinned token evidence",
    });
    expect(pinned).toHaveTextContent("Pinned token · 1");
    expect(pinned).toHaveTextContent("user: Ġlights assistant:");
    expect(pinned).toHaveTextContent("Activation 11.00");
    expect(pinned).toHaveTextContent("Gate 2.000");
    fireEvent.click(screen.getByRole("button", { name: "Clear pin" }));
    expect(
      screen.queryByRole("complementary", { name: "Pinned token evidence" }),
    ).toBeNull();
    expect(
      screen.getByRole("heading", { name: "Neuron 433" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Pin datapoint 101 token 1" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Negative pole" }));
    expect(
      screen.queryByRole("complementary", { name: "Pinned token evidence" }),
    ).toBeNull();
  });
  it("opens exact L15 N433 with readable examples, signed activation and independent token/datapoint denominators", async () => {
    mount(runtime(captureApi()), `/interp/c/${capture}?layer=15&neuron=433`);
    await screen.findByRole("heading", { name: "Neuron 433" });
    expect(
      screen.getByText("Layer 15 · selected evidence"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Turn on the kitchen lights")).toHaveLength(2);
    expect(
      screen.getByText(
        /3 matching returned examples · retained top-40 tokens per pole/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("First-token peak.")).toBeInTheDocument();
    expect(screen.getByText("Closed-gate echo dominates.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Labels & regimes" }));
    expect(
      screen.getByText(/Counts among 40 retained overall-positive tokens/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Share of 2 distinct retained datapoints/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "lights 50%" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Overall-positive labels" }),
    );
    expect(
      screen.getByText(/Share of 3 distinct retained datapoints/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "lights 67%" }),
    ).toBeInTheDocument();
  });
  it("filters actual pole, regime and labels rather than changing the retained scientific denominator", async () => {
    mount(runtime(captureApi()), `/interp/c/${capture}?layer=15&neuron=433`);
    await screen.findByRole("heading", { name: "Neuron 433" });
    fireEvent.click(screen.getByRole("button", { name: "Negative pole" }));
    expect(
      screen.getByText("Do not unlock the front door"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("tabpanel")).getByText("-5.00"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Set a ten minute timer")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "All positives" }));
    expect(
      screen.getByText(/40 matching returned examples/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Labels & regimes" }));
    fireEvent.click(
      screen.getByRole("button", { name: /Closed \/ negative.*37/ }),
    );
    expect(
      screen.getByText(
        /37 matching returned examples · retained top-40 tokens per pole/,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Set a ten minute timer")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Labels & regimes" }));
    fireEvent.click(screen.getByRole("button", { name: "Gate-open labels" }));
    fireEvent.click(screen.getByRole("button", { name: "timers 50%" }));
    expect(
      screen.getByText(
        /1 matching returned examples · retained top-40 tokens per pole/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Set a ten minute timer")).toBeInTheDocument();
    expect(screen.queryByText("Turn on the kitchen lights")).toBeNull();
  });
  it("uses the overall-positive labels and destination when the gate never opens", async () => {
    const base = neuronView(),
      empty = {
        ...base,
        gate_open: [],
        stats: { ...base.stats, open_max: null },
        datapoints: { ...base.datapoints, gate_open: [] },
        label_share: base.label_share_all,
      };
    mount(
      runtime(
        captureApi((path) =>
          path.includes("/neurons/") ? Promise.resolve(empty) : undefined,
        ),
      ),
      `/interp/c/${capture}?layer=15&neuron=433`,
    );
    await screen.findByRole("heading", { name: "Neuron 433" });
    fireEvent.click(screen.getByRole("button", { name: "Gate open" }));
    expect(
      screen.getByText(/0 matching returned examples/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Labels & regimes" }));
    expect(
      screen.getByText(
        /No gate-open examples; this summary uses the overall-positive pole/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Overall-positive labels" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByText(/Share of 3 distinct retained datapoints/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "lights 67%" }));
    expect(
      screen.getByRole("button", { name: "All positives" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByText(/39 matching returned examples/),
    ).toBeInTheDocument();
  });
  it("links positive label summaries to positive examples even after inspecting negatives", async () => {
    mount(runtime(captureApi()), `/interp/c/${capture}?layer=15&neuron=433`);
    await screen.findByRole("heading", { name: "Neuron 433" });
    fireEvent.click(screen.getByRole("button", { name: "Negative pole" }));
    fireEvent.click(screen.getByRole("tab", { name: "Labels & regimes" }));
    expect(
      screen.getByRole("heading", {
        name: "Labels in strongest overall-positive examples",
      }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "lights 67%" }));
    expect(
      screen.getByRole("button", { name: "All positives" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByText(/39 matching returned examples/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Do not unlock the front door")).toBeNull();
  });
  it("never commits an already-loaded neuron under the next route identity", async () => {
    const commits: { route: string; heading: string | null }[] = [];
    let resolve!: (value: any) => void;
    const pending = new Promise((done) => {
      resolve = done;
    });
    const studio = runtime(
      captureApi((path) =>
        path.includes("/neurons/434?") ? pending : undefined,
      ),
    );
    render(
      <StrictMode>
        <MemoryRouter
          initialEntries={[`/interp/c/${capture}?layer=15&neuron=433`]}
        >
          <RoutedStudio studio={studio}>
            <Profiler
              id="selection"
              onRender={() => {
                commits.push({
                  route: document.querySelector("output")?.textContent || "",
                  heading:
                    document.querySelector(".selected-neuron h2")
                      ?.textContent || null,
                });
              }}
            >
              <Routes>
                <Route path="/interp/c/:capture" element={<CapturePage />} />
              </Routes>
            </Profiler>
          </RoutedStudio>
        </MemoryRouter>
      </StrictMode>,
    );
    await screen.findByRole("heading", { name: "Neuron 433" });
    fireEvent.click(screen.getByRole("button", { name: "Explore neuron 434" }));
    await screen.findByText("Loading neuron 434…");
    expect(
      commits.some(
        (commit) =>
          commit.route.includes("neuron=434") &&
          commit.heading === "Neuron 433",
      ),
    ).toBe(false);
    await act(async () => resolve(neuronView(434)));
    await screen.findByRole("heading", { name: "Neuron 434" });
  });
  it("links row, plot and URL selection to exact current rendered page evidence and cleans registrations on unmount", async () => {
    const studio = runtime(captureApi()),
      view = mount(studio, `/interp/c/${capture}?layer=15`);
    const rowButton = await screen.findByRole("button", {
      name: "Explore neuron 433",
    });
    fireEvent.click(rowButton);
    await screen.findByRole("heading", { name: "Neuron 433" });
    expect(screen.getByLabelText("Current route")).toHaveTextContent(
      "layer=15&neuron=433",
    );
    expect(
      screen.getByRole("button", { name: "Explore neuron 433" }),
    ).toHaveAttribute("aria-current", "true");
    expect(
      screen.getByRole("button", { name: /Explore neuron 433: Token density/ }),
    ).toHaveClass("selected");
    const source = studio.evidence.pick(
      screen.getByRole("button", { name: "Explore neuron 433" }),
    );
    expect(source).toMatchObject({
      label: "Neuron summary",
      project: "home",
      user: "alice",
      data: { neuron: 433, max: 100 },
    });
    expect(source?.route).toContain("neuron=433");
    fireEvent.click(
      screen.getByRole("button", { name: /Explore neuron 434: Token density/ }),
    );
    await screen.findByRole("heading", { name: "Neuron 434" });
    expect(screen.getByLabelText("Current route")).toHaveTextContent(
      "neuron=434",
    );
    expect(
      screen.getByRole("button", { name: "Explore neuron 434" }),
    ).toHaveAttribute("aria-current", "true");
    view.unmount();
    expect(studio.evidence.snapshot()).toEqual([]);
  });
  it("does not replace the second selected neuron with an earlier ignored-abort response", async () => {
    const pending: Array<{
      resolve: (value: any) => void;
      signal?: AbortSignal;
    }> = [];
    const api = captureApi((path, signal) =>
        path.includes("/neurons/433?")
          ? new Promise((resolve) => pending.push({ resolve, signal }))
          : undefined,
      ),
      studio = runtime(api);
    mount(studio, `/interp/c/${capture}?layer=15&neuron=433`);
    await waitFor(() => expect(pending.length).toBeGreaterThan(0));
    fireEvent.click(
      await screen.findByRole("button", { name: "Explore neuron 434" }),
    );
    await screen.findByRole("heading", { name: "Neuron 434" });
    expect(pending.every((request) => request.signal?.aborted)).toBe(true);
    await act(async () => {
      for (const request of pending)
        request.resolve({
          ...neuronView(433),
          explanation: { explanation: "OBSOLETE FIRST RESPONSE" },
        });
    });
    expect(
      screen.getByRole("heading", { name: "Neuron 434" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("OBSOLETE FIRST RESPONSE")).toBeNull();
    expect(
      studio.evidence
        .snapshot()
        .some((record) =>
          JSON.stringify(record.data).includes("OBSOLETE FIRST RESPONSE"),
        ),
    ).toBe(false);
  });
  it("aborts an active selected-neuron request on unmount and never registers its late evidence", async () => {
    const pending: Array<{
      resolve: (value: any) => void;
      signal?: AbortSignal;
    }> = [];
    const api = captureApi((path, signal) =>
        path.includes("/neurons/433?")
          ? new Promise((resolve) => pending.push({ resolve, signal }))
          : undefined,
      ),
      studio = runtime(api);
    const view = mount(studio, `/interp/c/${capture}?layer=15&neuron=433`);
    await waitFor(() => expect(pending.length).toBeGreaterThan(0));
    view.unmount();
    expect(pending.every((request) => request.signal?.aborted)).toBe(true);
    await act(async () => {
      for (const request of pending) request.resolve(neuronView(433));
    });
    expect(studio.evidence.snapshot()).toEqual([]);
    expect(screen.queryByRole("heading", { name: "Neuron 433" })).toBeNull();
  });
});

describe("comparison pages preserve scientific boundary values", () => {
  it("keeps zero and negative correlation selectable, omits undefined coordinates, and links negative token changes to the selected record", async () => {
    const rows = [
      { neuron: 0, change: 2, r: 0, rel_change: 2 },
      { neuron: 1, change: 2, r: null, rel_change: 1 },
      { neuron: 2, change: 2.25, r: -0.75, rel_change: 0.5 },
    ];
    const api = {
        get: vi.fn(async (path: string) => {
          if (path === "/api/interp/diffs/comparison")
            return {
              experiment: "trained/1",
              base: "base",
              source: "commands",
              rows: 3,
              layers: [15],
              neurons_per_layer: 3,
              prefix_len: 0,
            };
          if (path.includes("/neurons/"))
            return {
              neuron: 2,
              change: 2.25,
              r: -0.75,
              rel_change: 0.5,
              datapoints: [
                {
                  datapoint: 404,
                  delta_sum: 4,
                  user: "Keep the security door locked",
                  labels: ["safety"],
                },
              ],
              tokens: [
                {
                  datapoint: 404,
                  position: 2,
                  before: 2,
                  after: -2,
                  context: "locked door",
                },
              ],
            };
          if (path.startsWith("/api/interp/diffs/comparison/layers/15?"))
            return {
              rows,
              summary: {
                median_r: -0.375,
                below_0_9: 2,
                below_0_5: 2,
                constant: 1,
              },
            };
          if (path === "/api/interp") return { captures: [], comparisons: [] };
          throw new Error(`Unexpected fixture API ${path}`);
        }),
      },
      studio = runtime(api);
    mount(studio, "/interp/d/comparison?layer=15");
    expect(
      await screen.findByRole("button", {
        name: /Explore neuron 0: Correlation r 0.00/,
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Explore changed neuron 1" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Explore neuron 1: Correlation/ }),
    ).toBeNull();
    expect(
      screen.getByText("2 plotted · 1 undefined coordinates omitted"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("table", { name: "Changed neurons" })).getByText(
        "—",
      ),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Correlation at most"), {
      target: { value: "0" },
    });
    expect(
      screen.getByRole("button", { name: "Explore changed neuron 0" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Explore changed neuron 2" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Explore changed neuron 1" }),
    ).toBeNull();
    fireEvent.click(
      screen.getByRole("button", {
        name: /Explore neuron 2: Correlation r -0.75/,
      }),
    );
    await screen.findByRole("heading", { name: "Layer 15 · neuron 2" });
    expect(
      screen.getByText("Keep the security door locked"),
    ).toBeInTheDocument();
    expect(screen.getByText("2.00 → -2.00")).toBeInTheDocument();
    expect(screen.getByText("Σ change 4.00")).toBeInTheDocument();
    expect(screen.getByLabelText("Current route")).toHaveTextContent(
      "neuron=2",
    );
    const tokenRecord = studio.evidence
      .snapshot()
      .find((record) => record.label === "Changed token");
    expect(tokenRecord).toMatchObject({
      project: "home",
      user: "alice",
      data: { datapoint: 404, before: 2, after: -2 },
    });
  });
});
