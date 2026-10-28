import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { Shell } from "../app/App";
import { StudioContext } from "../app/StudioContext";
import { EvidenceRegistry } from "../app/evidence";
const sceneUpdate = vi.hoisted(() => vi.fn());
// jsdom cannot create WebGL. Keep the route, replay, controller, chart and
// figure construction real, and capture only their renderer boundary.
vi.mock("../features/lab/spatial-runtime", () => ({
  requireWebGL: vi.fn(),
  loadSpatialRuntime: vi.fn(async () => ({})),
  SpatialScene: class {
    constructor(
      _host: HTMLElement,
      _runtime: unknown,
      events: { ready: () => void },
    ) {
      events.ready();
    }
    update = sceneUpdate;
    dispose() {}
  },
}));
vi.mock("../features/assistant", () => ({
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
  vi.unstubAllGlobals();
});
it("opens a retained run through normal Laboratory navigation and inspects its exact 3D samples", async () => {
  const user = userEvent.setup();
  sceneUpdate.mockClear();
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  const run = {
    id: "signal-replay-run",
    definition_id: "signal-definition",
    state: "succeeded",
    result: {
      artifacts: [
        {
          id: "retained-derived",
          name: "filtered.jsonl",
          role: "transformed_dataset",
        },
        {
          id: "retained-original",
          name: "observed.jsonl",
          role: "normalized_original",
        },
      ],
    },
  };
  const processed = [
    {
      time: 0.125,
      available_at: 0.25,
      value: -1.25,
      current: 0.0625,
      units: "V",
      field_units: { current: "A" },
      episode_id: "episode-1",
      source_record_id: "sensor-1",
    },
    {
      time: 0.375,
      available_at: 0.5,
      value: 0.125,
      current: -0.375,
      units: "V",
      field_units: { current: "A" },
      episode_id: "episode-1",
      source_record_id: "sensor-2",
    },
  ];
  const original = processed.map((row, index) => ({
    ...row,
    value: index === 0 ? -2.75 : 0.3125,
  }));
  const api = {
    get: vi.fn(async (path: string) => {
      if (path === "/api/lab/catalog") return { adapters: [] };
      if (path === "/api/lab/targets") return [{ id: "local" }];
      if (path === "/api/lab/runs") return [run];
      if (path === `/api/lab/runs/${run.id}`) return run;
      if (path.includes("/events?")) return { events: [], next_after: 0 };
      if (path.includes("/records?")) {
        const artifact = new URL(path, "http://studio.test").searchParams.get(
          "artifact",
        );
        if (artifact === "filtered.jsonl") return { records: processed };
        if (artifact === "observed.jsonl") return { records: original };
        throw new Error(`Unexpected record artifact: ${artifact}`);
      }
      return [];
    }),
  };
  const studio = {
    api,
    user: { id: "alice", role: "admin" },
    project: "alpha",
    projects: [{ slug: "alpha", name: "Alpha" }],
    scopeKey: "alice:alpha",
    route: "/lab",
    evidence: new EvidenceRegistry(),
  } as any;
  render(
    <StudioContext.Provider value={studio}>
      <MemoryRouter initialEntries={["/missing"]}>
        <Shell />
      </MemoryRouter>
    </StudioContext.Provider>,
  );
  fireEvent.click(
    within(
      screen.getByRole("navigation", { name: "Main navigation" }),
    ).getByRole("link", { name: "Laboratory" }),
  );
  // Scope failures to route content so a pending lazy module is distinguishable
  // from navigation or a rendering error even when the shell DOM is large.
  const workspace = within(
    screen.getByRole("main", { name: "Workspace content" }),
  );
  expect(
    await workspace.findByRole("heading", { name: "Laboratory" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Save experiment" }),
  ).toBeInTheDocument();
  expect(
    within(
      screen.getByRole("navigation", { name: "Main navigation" }),
    ).getByRole("link", { name: "Laboratory" }),
  ).toHaveAttribute("aria-current", "page");
  const runsTab = workspace.getByRole("tab", { name: "Runs" });
  runsTab.focus();
  await user.keyboard("{Enter}");
  expect(runsTab).toHaveAttribute("aria-selected", "true");
  await user.click(workspace.getByRole("button", { name: /Retained run/ }));
  await user.click(
    await screen.findByRole("option", {
      name: "signal-replay-run · succeeded",
    }),
  );
  const trends = within(
    await workspace.findByRole("region", { name: "Interactive signal trends" }),
  );
  expect(trends.getByRole("button", { name: "3D graph" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await user.click(trends.getByText("Graph settings"));
  expect(
    trends.getByRole("button", { name: "Event / sample time X axis" }),
  ).toBeInTheDocument();
  expect(
    trends.getByRole("button", { name: "value Y axis" }),
  ).toBeInTheDocument();
  expect(
    trends.getByRole("button", { name: "current Z axis" }),
  ).toBeInTheDocument();
  await waitFor(() => expect(sceneUpdate).toHaveBeenCalled());
  expect(sceneUpdate.mock.lastCall![0].data[0]).toMatchObject({
    x: [0.125, 0.375],
    y: [-1.25, 0.125],
    z: [0.0625, -0.375],
    customdata: [
      '["signal-replay-run","retained-derived",0]',
      '["signal-replay-run","retained-derived",1]',
    ],
  });
  await user.click(trends.getByText("Exact values and source links"));
  const values = within(
    trends.getByRole("table", { name: "Trend sample values" }),
  );
  expect(values.getByText("value: -1.25 V")).toBeInTheDocument();
  expect(values.getByText("current: -0.375 A")).toBeInTheDocument();
  await user.click(
    values.getByRole("button", { name: "Select derived row 2" }),
  );
  expect(
    values.getByRole("button", { name: "Select derived row 2" }).closest("tr"),
  ).toHaveAttribute("aria-selected", "true");
  const selected = within(
    workspace.getByRole("complementary", { name: "Selected record evidence" }),
  );
  expect(selected.getByText("0.125 V")).toBeInTheDocument();
  expect(selected.getByText("sensor-2")).toBeInTheDocument();
  expect(sceneUpdate.mock.lastCall![0].data[1]).toMatchObject({
    x: [0.375],
    y: [0.125],
    z: [-0.375],
    customdata: ['["signal-replay-run","retained-derived",1]'],
  });
  await user.click(
    trends.getByRole("button", { name: /Source \/ unit group$/ }),
  );
  await user.click(
    await screen.findByRole("option", { name: /Original.*2 points/ }),
  );
  expect(sceneUpdate.mock.lastCall![0].data[0]).toMatchObject({
    x: [0.125, 0.375],
    y: [-2.75, 0.3125],
    z: [0.0625, -0.375],
    customdata: [
      '["signal-replay-run","retained-original",0]',
      '["signal-replay-run","retained-original",1]',
    ],
  });
  expect(values.getByText("value: -2.75 V")).toBeInTheDocument();
  expect(api.get).toHaveBeenCalledWith(
    `/api/lab/runs/${run.id}/records?offset=0&limit=1000&artifact=filtered.jsonl`,
    expect.any(AbortSignal),
  );
  expect(api.get).toHaveBeenCalledWith(
    `/api/lab/runs/${run.id}/records?offset=0&limit=1000&artifact=observed.jsonl`,
    expect.any(AbortSignal),
  );
});
