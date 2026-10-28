import { Profiler } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { NeuronMap } from "./plots";
afterEach(cleanup);
const initial = [
  { neuron: 7, x: 0.2, y: 14 },
  { neuron: 9, x: 0.4, y: 8 },
];
function plot(
  rows: typeof initial,
  selected: number | null = 9,
  onCommit?: () => void,
) {
  const runtime = {
    api: {},
    project: "fixture",
    user: { id: "reviewer" },
    route: "/interp/d/fixture?layer=0",
    scopeKey: "reviewer:fixture",
    evidence: new EvidenceRegistry(),
  } as any;
  return (
    <StudioContext.Provider value={runtime}>
      <Profiler id="map" onRender={() => onCommit?.()}>
        <NeuronMap
          label="Current comparison"
          rows={rows}
          selected={selected}
          onSelect={vi.fn()}
          xLabel="Correlation"
          yLabel="Change"
        />
      </Profiler>
    </StudioContext.Provider>
  );
}
const focused = () =>
  screen.getByRole("button", {
    name: "Explore neuron 7: Correlation 0.20, Change 14.00",
  });
describe("plot caption uses only current scientific records", () => {
  it("immediately removes the focused measurement when loading supplies empty rows", () => {
    const view = render(plot(initial));
    fireEvent.focus(focused());
    expect(screen.getByText("N7 · 0.20 · 14.00")).toBeInTheDocument();
    const commits: string[] = [];
    view.rerender(
      plot([], 9, () =>
        commits.push(
          document.querySelector(".plot-caption")?.textContent ?? "",
        ),
      ),
    );
    expect(screen.getByText("0 plotted")).toBeInTheDocument();
    expect(screen.getByText("No finite values")).toBeInTheDocument();
    expect(screen.queryByText("N7 · 0.20 · 14.00")).not.toBeInTheDocument();
    expect(commits.length).toBeGreaterThan(0);
    expect(
      commits.every(
        (caption) =>
          caption.includes("No finite values") && !caption.includes("N7"),
      ),
    ).toBe(true);
  });
  it("falls back to a current selected point when filtering removes the focused neuron", () => {
    const view = render(plot(initial));
    fireEvent.focus(focused());
    view.rerender(plot([{ neuron: 9, x: 0.4, y: 8 }]));
    expect(screen.getByText("1 plotted")).toBeInTheDocument();
    expect(screen.getByText("N9 · 0.40 · 8.00")).toBeInTheDocument();
    expect(screen.queryByText("N7 · 0.20 · 14.00")).not.toBeInTheDocument();
  });
  it("reads updated measurements from new rows even when a neuron ID and DOM node are reused", () => {
    const view = render(plot(initial));
    const dot = focused();
    fireEvent.focus(dot);
    view.rerender(
      plot([
        { neuron: 7, x: 0.7, y: 31 },
        { neuron: 9, x: 0.4, y: 8 },
      ]),
    );
    expect(
      screen.getByRole("button", {
        name: "Explore neuron 7: Correlation 0.70, Change 31.00",
      }),
    ).toBe(dot);
    expect(screen.getByText("N7 · 0.70 · 31.00")).toBeInTheDocument();
    expect(screen.queryByText("N7 · 0.20 · 14.00")).not.toBeInTheDocument();
  });
  it("does not prefer an old inspection over a newly selected identity", () => {
    const view = render(plot(initial));
    fireEvent.focus(focused());
    view.rerender(plot([...initial, { neuron: 11, x: 0.8, y: 42 }], 11));
    expect(screen.getByText("N11 · 0.80 · 42.00")).toBeInTheDocument();
  });
});
