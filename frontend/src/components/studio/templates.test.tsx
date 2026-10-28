import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Button } from "./primitives";
import { FilterMenu, SegmentedControl } from "../affectui/controls";
import {
  CollectionTemplate,
  DetailTemplate,
  AnalysisTemplate,
  RunTemplate,
  ConversationTemplate,
  SettingsTemplate,
  SetupTemplate,
} from "./templates";

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("requestAnimationFrame", () => 0);
  vi.stubGlobal("cancelAnimationFrame", () => {});
  vi.stubGlobal("CSS", { escape: (value: string) => value });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AffectUI reusable template contracts", () => {
  it.each([
    ["collection", CollectionTemplate],
    ["detail", DetailTemplate],
    ["analysis", AnalysisTemplate],
    ["run", RunTemplate],
    ["conversation", ConversationTemplate],
    ["settings", SettingsTemplate],
    ["setup", SetupTemplate],
  ] as const)("retains every %s slot in semantic order", (kind, Template) => {
    render(
      <Template
        label="Workspace"
        className="consumer"
        summary={<p>Real summary</p>}
        toolbar={<button>Filter records</button>}
        inspector={<button>Inspect record</button>}
      >
        <button>Open record</button>
      </Template>,
    );
    const root = screen.getByRole("region", { name: "Workspace" });
    expect(root).toHaveAttribute("data-template", kind);
    expect(root).toHaveClass("consumer");
    expect(
      screen.getByRole("complementary", { name: "Workspace details" }),
    ).toContainElement(screen.getByRole("button", { name: "Inspect record" }));
    expect(
      Array.from(root.querySelectorAll("button")).map((el) => el.textContent),
    ).toEqual(["Filter records", "Open record", "Inspect record"]);
    expect(screen.getByText("Real summary")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Inspect record" }),
    ).not.toHaveAttribute("tabindex", "-1");
  });
  it("omits absent slots without adding empty panels", () => {
    const { container } = render(
      <CollectionTemplate>
        <p>Records</p>
      </CollectionTemplate>,
    );
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(container.querySelector(".studio-template__toolbar")).toBeNull();
    expect(container.querySelector(".studio-template__summary")).toBeNull();
  });
});

describe("AffectUI action compatibility", () => {
  it("keeps native button defaults, classes, accessible names and events", () => {
    const onClick = vi.fn();
    render(
      <Button
        className="consumer-action"
        aria-label="Create project"
        onClick={onClick}
      >
        Create
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Create project" });
    expect(button).toHaveAttribute("type", "button");
    expect(button).toHaveClass("ui-buttonc", "consumer-action");
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });
  it("preserves submit semantics and prevents disabled and loading actions", () => {
    const onClick = vi.fn();
    const { rerender } = render(
      <Button type="submit" variant="danger" disabled onClick={onClick}>
        Delete
      </Button>,
    );
    let button = screen.getByRole("button", { name: "Delete" });
    expect(button).toHaveAttribute("type", "submit");
    expect(button).toHaveAttribute("data-variant", "destructive");
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
    rerender(
      <Button loading onClick={onClick}>
        Delete
      </Button>,
    );
    button = screen.getByRole("button", { name: "Working…" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
    rerender(<Button onClick={onClick}>Delete</Button>);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onClick).toHaveBeenCalledOnce();
  });
});

const groups = [
  {
    id: "status",
    label: "Status",
    options: [
      { id: "ready", label: "Ready" },
      { id: "failed", label: "Failed" },
    ],
  },
];
describe("controlled filter and segmented ownership", () => {
  it("reports selections without changing externally owned filter values, and clears through callback", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <FilterMenu groups={groups} value={{}} onChange={onChange} />,
    );
    const ready = screen.getByRole("checkbox", { name: "Ready" });
    fireEvent.click(ready);
    expect(onChange).toHaveBeenLastCalledWith({ status: ["ready"] });
    expect(ready).toHaveAttribute("aria-checked", "false");
    rerender(
      <FilterMenu
        groups={groups}
        value={{ status: ["ready"] }}
        onChange={onChange}
      />,
    );
    expect(screen.getByRole("checkbox", { name: "Ready" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(onChange).toHaveBeenLastCalledWith({});
    fireEvent.click(screen.getByRole("button", { name: /Status/ }));
    expect(screen.queryByRole("checkbox", { name: "Ready" })).toBeNull();
  });
  it("supports keyboard filter focus and selection and tolerates no groups", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <FilterMenu groups={groups} value={{}} onChange={onChange} />,
    );
    const group = screen.getByRole("button", { name: /Status/ });
    fireEvent.focus(group);
    fireEvent.keyDown(group, { key: "ArrowDown" });
    expect(screen.getByRole("checkbox", { name: "Ready" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: " " });
    expect(onChange).toHaveBeenLastCalledWith({ status: ["ready"] });
    rerender(<FilterMenu groups={[]} value={{}} onChange={onChange} />);
    const emptyList = screen
      .getByText("Filters")
      .closest(".ui-fmc")!
      .querySelector(".ui-fmc__groups")!;
    fireEvent.keyDown(emptyList, { key: "End" });
    fireEvent.keyDown(emptyList, { key: "ArrowDown" });
    expect(screen.queryByRole("checkbox")).toBeNull();
  });
  it("restores a filter tab stop after focused options disappear and groups repopulate without stealing outside focus", () => {
    const onChange = vi.fn();
    const content = (visibleGroups: typeof groups) => (
      <>
        <button>Outside control</button>
        <FilterMenu groups={visibleGroups} value={{}} onChange={onChange} />
      </>
    );
    const { rerender } = render(content(groups));
    fireEvent.keyDown(screen.getByRole("button", { name: /Status/ }), {
      key: "End",
    });
    expect(screen.getByRole("checkbox", { name: "Failed" })).toHaveFocus();
    rerender(content([{ ...groups[0], options: [groups[0].options[0]] }]));
    const group = screen.getByRole("button", { name: /Status/ });
    expect(group).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(group, { key: "ArrowDown" });
    expect(screen.getByRole("checkbox", { name: "Ready" })).toHaveFocus();
    const outside = screen.getByRole("button", { name: "Outside control" });
    outside.focus();
    rerender(content([]));
    expect(outside).toHaveFocus();
    rerender(content(groups));
    expect(outside).toHaveFocus();
    expect(screen.getByRole("button", { name: /Status/ })).toHaveAttribute(
      "tabindex",
      "0",
    );
  });
  it("emits one controlled selection per touch tap while retaining the supplied value", () => {
    class TestPointerEvent extends MouseEvent {
      pointerId: number;
      pointerType: string;
      constructor(
        type: string,
        init: MouseEventInit & {
          pointerId?: number;
          pointerType?: string;
        } = {},
      ) {
        super(type, init);
        this.pointerId = init.pointerId ?? 1;
        this.pointerType = init.pointerType ?? "touch";
      }
    }
    vi.stubGlobal("PointerEvent", TestPointerEvent);
    const onChange = vi.fn();
    render(
      <SegmentedControl
        aria-label="View"
        value="table"
        options={[
          { value: "table", label: "Table" },
          { value: "chart", label: "Chart" },
        ]}
        onChange={onChange}
      />,
    );
    const table = screen.getByRole("radio", { name: "Table" });
    const chart = screen.getByRole("radio", { name: "Chart" });
    vi.spyOn(table, "getBoundingClientRect").mockReturnValue({
      left: 0,
      right: 100,
      top: 0,
      bottom: 32,
      width: 100,
      height: 32,
      x: 0,
      y: 0,
      toJSON() {},
    });
    vi.spyOn(chart, "getBoundingClientRect").mockReturnValue({
      left: 100,
      right: 200,
      top: 0,
      bottom: 32,
      width: 100,
      height: 32,
      x: 100,
      y: 0,
      toJSON() {},
    });
    const touch = {
      pointerId: 7,
      pointerType: "touch",
      button: 0,
      clientX: 150,
    };
    fireEvent.pointerDown(chart, touch);
    fireEvent.pointerMove(chart, { ...touch, clientX: 152 });
    fireEvent.pointerUp(chart, { ...touch, clientX: 152 });
    fireEvent.click(chart, { detail: 1 });
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenLastCalledWith("chart");
    expect(table).toHaveAttribute("aria-checked", "true");
    // A rejected/deferred controlled update must still allow a later gesture.
    fireEvent.pointerDown(chart, touch);
    fireEvent.pointerUp(chart, touch);
    fireEvent.click(chart, { detail: 1 });
    expect(onChange).toHaveBeenCalledTimes(2);
    // Keyboard and assistive-technology activation have no pointer click detail.
    fireEvent.click(chart, { detail: 0 });
    expect(onChange).toHaveBeenCalledTimes(3);
    // Dragging the selected pill may cross a segment repeatedly within one gesture.
    fireEvent.pointerDown(table, { ...touch, clientX: 50 });
    fireEvent.pointerMove(chart, touch);
    fireEvent.pointerMove(chart, { ...touch, clientX: 152 });
    fireEvent.pointerUp(chart, touch);
    fireEvent.click(chart, { detail: 1 });
    expect(onChange).toHaveBeenCalledTimes(4);
    expect(table).toHaveAttribute("aria-checked", "true");
  });
  it("skips disabled segments and preserves controlled selected state", () => {
    const onChange = vi.fn();
    const options = [
      { value: "table", label: "Table" },
      { value: "map", label: "Map", disabled: true },
      { value: "chart", label: "Chart" },
    ];
    const { rerender } = render(
      <SegmentedControl
        aria-label="View"
        options={options}
        value="table"
        onChange={onChange}
      />,
    );
    const table = screen.getByRole("radio", { name: "Table" });
    fireEvent.keyDown(table, { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith("chart");
    expect(screen.getByRole("radio", { name: "Chart" })).toHaveFocus();
    expect(table).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "Map" })).toBeDisabled();
    rerender(
      <SegmentedControl
        aria-label="View"
        options={options}
        value="chart"
        onChange={onChange}
      />,
    );
    expect(screen.getByRole("radio", { name: "Chart" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });
  it("reverses horizontal segment navigation in RTL and retains an enabled tab stop", () => {
    const onChange = vi.fn();
    render(
      <div style={{ direction: "rtl" }}>
        <SegmentedControl
          aria-label="View"
          value="b"
          options={[
            { value: "a", label: "A", disabled: true },
            { value: "b", label: "B" },
            { value: "c", label: "C" },
          ]}
          onChange={onChange}
        />
      </div>,
    );
    const b = screen.getByRole("radio", { name: "B" });
    expect(b).toHaveAttribute("tabindex", "0");
    // jsdom does not inherit computed direction; give the actual keyboard region its resolved browser direction.
    screen.getByRole("radiogroup").style.direction = "rtl";
    fireEvent.keyDown(b, { key: "ArrowLeft" });
    expect(onChange).toHaveBeenLastCalledWith("c");
  });
  it("keeps an enabled segment reachable when the controller selects a disabled option", () => {
    render(
      <SegmentedControl
        aria-label="View"
        value="a"
        options={[
          { value: "a", label: "A", disabled: true },
          { value: "b", label: "B" },
        ]}
        onChange={() => {}}
      />,
    );
    expect(screen.getByRole("radio", { name: "A" })).toHaveAttribute(
      "tabindex",
      "-1",
    );
    expect(screen.getByRole("radio", { name: "B" })).toHaveAttribute(
      "tabindex",
      "0",
    );
  });
  it("preserves an explicitly supplied busy state outside loading", () => {
    render(<Button aria-busy="true">Refresh</Button>);
    expect(screen.getByRole("button", { name: "Refresh" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
  });
});
