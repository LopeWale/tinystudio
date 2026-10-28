import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { Histogram } from "./common";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function mount(props: any) {
  return render(
    <StudioContext.Provider
      value={
        {
          evidence: new EvidenceRegistry(),
          route: "/datasets/example",
          project: "p",
          user: { id: "u" },
        } as any
      }
    >
      <Histogram label="User length" {...props} />
    </StudioContext.Provider>,
  );
}

describe("histogram aggregate truth", () => {
  it("names integer bin membership rather than implying padded edges are observations", () => {
    // core.quality._histogram([42, 49, 58]) uses width 2 and 12 bins.
    mount({
      counts: [1, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0],
      bins: [42, 44, 46, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66],
      integerValues: true,
      unit: "characters",
    });
    expect(screen.getByRole("img").getAttribute("aria-description")).toContain(
      "3 observations",
    );
    expect(
      screen.getByText("42–43", { selector: ".histogram-bin-label" }),
    ).toBeTruthy();
    expect(
      screen.getByText("64–65", { selector: ".histogram-bin-label" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByText("View bin counts"));
    const table = screen.getByRole("table");
    expect(within(table).getByRole("row", { name: "48–49 1" })).toBeTruthy();
    expect(within(table).getByRole("row", { name: "64–65 0" })).toBeTruthy();
    expect(screen.getByText(/Bin ranges include empty intervals/)).toBeTruthy();
  });
  it("renders a degenerate integer histogram as exact value 8 with truthful counts", () => {
    mount({
      counts: [3],
      bins: [8, 9],
      integerValues: true,
      unit: "rounded rating",
    });
    expect(
      screen.getByText("8", { selector: ".histogram-bin-label" }),
    ).toBeTruthy();
    expect(screen.queryByText("8–9")).toBeNull();
    expect(
      screen.getByText("3", { selector: ".histogram-bar-count" }),
    ).toBeTruthy();
  });
  it("keeps continuous evaluation bins, inclusive final endpoint and keyboard selection", () => {
    const select = vi.fn();
    mount({
      counts: [2, 1],
      bins: [0, 50, 100],
      onSelect: select,
      selected: 1,
    });
    const last = screen.getByRole("button", {
      name: /50 ≤ value ≤ 100: 1 observation/,
    });
    expect(last.getAttribute("aria-pressed")).toBe("true");
    fireEvent.keyDown(last, { key: "Enter" });
    expect(select).toHaveBeenCalledWith(1);
    fireEvent.focus(last);
    expect(screen.getByRole("status").textContent).toContain("33.3%");
  });
  it("shows zero and empty states without fabricating observations or bars", () => {
    const view = mount({
      counts: [0, 0],
      bins: [0, 1, 2],
      integerValues: true,
    });
    expect(screen.getByRole("img").getAttribute("aria-description")).toContain(
      "0 observations",
    );
    expect(view.container.querySelectorAll(".histogram-bar")).toHaveLength(0);
    view.unmount();
    mount({ counts: [], bins: [] });
    expect(screen.getByText("No observations available.")).toBeTruthy();
  });
  it("fits a narrow surface without dropping bins or aggregate evidence", () => {
    let resize!: (entries: any[]) => void;
    const disconnect = vi.fn();
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(callback: (entries: any[]) => void) {
          resize = callback;
        }
        observe() {}
        disconnect = disconnect;
      },
    );
    const view = mount({
      counts: [1, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0],
      bins: [42, 44, 46, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66],
      integerValues: true,
      unit: "characters",
    });
    act(() => resize([{ contentRect: { width: 280 } }]));
    expect(screen.getByRole("img")).toHaveAttribute("viewBox", "0 0 280 181");
    expect(view.container.querySelector("svg")!.style.minWidth).toBe("");
    expect(view.container.querySelectorAll(".histogram-bin")).toHaveLength(12);
    expect(view.container.querySelectorAll(".histogram-bar")).toHaveLength(3);
    expect(
      view.container.querySelectorAll(".histogram-bin-label").length,
    ).toBeLessThan(12);
    fireEvent.focus(view.container.querySelectorAll(".histogram-bin")[3]);
    expect(screen.getByRole("status")).toHaveTextContent(
      "48–49: 1 observation (33.3%)",
    );
    fireEvent.click(screen.getByText("View bin counts"));
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(
      13,
    );
    view.unmount();
    expect(disconnect).toHaveBeenCalledOnce();
  });
  it.each([450, 280])(
    "labels every separated populated interval at %ipx before empty bins",
    (width) => {
      let resize!: (entries: any[]) => void;
      vi.stubGlobal(
        "ResizeObserver",
        class {
          constructor(callback: (entries: any[]) => void) {
            resize = callback;
          }
          observe() {}
          disconnect() {}
        },
      );
      // Actual previewQuality user lengths [42,64,72], with all retained backend bins.
      const view = mount({
        counts: [1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0],
        bins: [42, 45, 48, 51, 54, 57, 60, 63, 66, 69, 72, 75, 78],
        integerValues: true,
        unit: "characters",
      });
      act(() => resize([{ contentRect: { width } }]));
      for (const interval of ["42–44", "63–65", "72–74"]) {
        expect(
          screen.getByText(interval, { selector: ".histogram-bin-label" }),
        ).toBeTruthy();
      }
      const bars = view.container.querySelectorAll(".histogram-bar");
      expect(bars).toHaveLength(3);
      for (const bar of bars) {
        const group = bar.parentElement!;
        expect(group.querySelector(".histogram-bin-label")).not.toBeNull();
        // The label belongs to the same bin center as its plotted count.
        expect(
          Number(bar.getAttribute("x")) + Number(bar.getAttribute("width")) / 2,
        ).toBeCloseTo(
          Number(
            group.querySelector(".histogram-bin-label")!.getAttribute("x"),
          ),
          5,
        );
      }
    },
  );
  it("prioritizes the focused interval when adjacent populated labels collide", () => {
    let resize!: (entries: any[]) => void;
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(callback: (entries: any[]) => void) {
          resize = callback;
        }
        observe() {}
        disconnect() {}
      },
    );
    const view = mount({
      counts: [5, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
      bins: [42, 45, 48, 51, 54, 57, 60, 63, 66, 69, 72, 75, 78],
      integerValues: true,
    });
    act(() => resize([{ contentRect: { width: 280 } }]));
    expect(
      screen.getByText("42–44", { selector: ".histogram-bin-label" }),
    ).toBeTruthy();
    expect(
      screen.queryByText("45–47", { selector: ".histogram-bin-label" }),
    ).toBeNull();
    fireEvent.focus(view.container.querySelectorAll(".histogram-bin")[1]);
    expect(
      screen.getByText("45–47", { selector: ".histogram-bin-label" }),
    ).toBeTruthy();
    expect(
      screen.queryByText("42–44", { selector: ".histogram-bin-label" }),
    ).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent(
      "45–47: 1 observation (16.7%)",
    );
    expect(view.container.querySelectorAll(".histogram-bar")).toHaveLength(2);
  });
  it("reports malformed aggregate input instead of drawing a misleading chart", () => {
    mount({ counts: [1, 2], bins: [0, 10] });
    expect(
      screen.getByText(
        "Histogram unavailable: bin ranges and counts do not match.",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
  });
});
