import { useEffect } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { ReplayViews } from "./replay";
import { runSelectorLabel } from "./index";
import type { LabRun } from "./definition";
const mounted = vi.hoisted(() => vi.fn());
vi.mock("./trend-workbench", () => ({
  TrendWorkbench: ({ controller }: any) => {
    useEffect(() => {
      mounted();
    }, []);
    return (
      <output data-testid="selected-row">
        {controller.selected?.identity.rowIndex}
      </output>
    );
  },
}));
vi.mock("./graph", () => ({ DefinitionGraph: () => null }));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  mounted.mockClear();
});
const run: LabRun = {
  id: "run",
  definition_id: "definition",
  state: "succeeded",
};
const records = Array.from({ length: 8 }, (_, index) => ({
  time: index,
  available_at: index,
  value: index,
  current: index * 2,
  units: "V",
  field_units: { current: "A" },
  episode_id: "episode",
}));
const studio = {
  scopeKey: "project",
  project: "project",
  route: "/lab",
  user: { id: "user" },
  api: { get: vi.fn().mockResolvedValue(undefined) },
  evidence: new EvidenceRegistry(),
} as unknown as StudioRuntime;
function workspace(active: boolean) {
  return (
    <StudioContext.Provider value={studio}>
      <ReplayViews
        records={records}
        rawRecords={[]}
        events={[]}
        run={run}
        active={active}
      />
    </StudioContext.Provider>
  );
}
it("keeps the chart mounted and the exact selected row when opening and closing the observation inspector", () => {
  render(workspace(true));
  expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Next frame" }));
  expect(screen.getByTestId("selected-row")).toHaveTextContent("1");
  fireEvent.click(screen.getByRole("button", { name: "Inspect observation" }));
  expect(
    screen.getByRole("complementary", { name: "Selected observation" }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Close observation" }));
  expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  expect(screen.getByTestId("selected-row")).toHaveTextContent("1");
  for (const name of [
    "State / topology",
    "Dataset coverage",
    "Representation inspector",
    "Episode replay",
  ]) {
    fireEvent.click(screen.getByRole("button", { name: /Evidence view/ }));
    fireEvent.click(screen.getByRole("option", { name }));
    expect(screen.getByTestId("selected-row")).toHaveTextContent("1");
  }
  expect(mounted).toHaveBeenCalledTimes(1);
});
it("pauses hidden playback and returns to the same frame without resuming", () => {
  vi.useFakeTimers();
  const view = render(workspace(true));
  fireEvent.click(screen.getByRole("button", { name: "Play replay" }));
  act(() => {
    vi.advanceTimersByTime(150);
  });
  expect(screen.getByTestId("selected-row")).toHaveTextContent("1");
  view.rerender(workspace(false));
  act(() => {
    vi.advanceTimersByTime(600);
  });
  expect(screen.getByTestId("selected-row")).toHaveTextContent("1");
  view.rerender(workspace(true));
  act(() => {
    vi.advanceTimersByTime(600);
  });
  expect(screen.getByTestId("selected-row")).toHaveTextContent("1");
  expect(screen.getByRole("button", { name: "Play replay" })).toBeEnabled();
});
it("disambiguates shortened run IDs even when their last eight characters collide", () => {
  const first = { ...run, id: "first-run-with-shared-12345678" };
  const second = { ...run, id: "second-run-with-shared-12345678" };
  const rows = [first, second];
  expect(runSelectorLabel(first, rows)).not.toEqual(
    runSelectorLabel(second, rows),
  );
  expect(runSelectorLabel(first, rows)).toContain(
    "first-run-with-shared-12345678".slice(-23),
  );
  expect(runSelectorLabel(run, [run])).toBe("run · succeeded");
});
