import { ComponentActionsProvider } from "../../features/assistant/actions";
import { ComponentActionRegistry } from "../../controllers/componentActions";
import { StudioContext } from "../../app/StudioContext";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import {
  DataTable,
  AdvancedTable,
  SimpleTable,
  type TableColumn,
} from "./data-table";
type Row = { id: string; name: string; amount: number; status: string };
const rows: Row[] = Array.from({ length: 32 }, (_, index) => ({
  id: String(index),
  name: `Record ${index}`,
  amount: index,
  status: index % 2 ? "failed" : "ready",
}));
function fixture(onOpen = vi.fn()) {
  const columns: TableColumn<Row>[] = [
    {
      id: "name",
      label: "Name",
      value: (row) => row.name,
      render: (row) => <a href={`/records/${row.id}`}>{row.name}</a>,
    },
    { id: "amount", label: "Amount", value: (row) => row.amount },
    { id: "status", label: "Status", value: (row) => row.status },
    {
      id: "actions",
      label: "Actions",
      value: () => null,
      sortable: false,
      filterable: false,
      hideable: false,
      render: (row) => (
        <button onClick={() => onOpen(row.id)}>Open {row.id}</button>
      ),
    },
  ];
  return { columns, onOpen, getRowId: (row: Row) => row.id };
}
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
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
describe("reusable typed table variants", () => {
  it("sorts actual numeric values while retaining links, callbacks and row attributes", () => {
    const f = fixture();
    render(
      <DataTable
        label="Records"
        rows={rows}
        {...f}
        rowProps={(row) => ({
          "aria-label": `Row ${row.id}`,
          onDoubleClick: () => f.onOpen(`row:${row.id}`),
        })}
      />,
    );
    const table = screen.getByRole("table", { name: "Records" });
    fireEvent.click(within(table).getByRole("button", { name: "Amount" }));
    fireEvent.click(within(table).getByRole("button", { name: "Amount" }));
    expect(within(table).getAllByRole("row")[1]).toHaveAccessibleName("Row 31");
    expect(
      within(table).getByRole("columnheader", { name: "Amount" }),
    ).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByRole("link", { name: "Record 31" })).toHaveAttribute(
      "href",
      "/records/31",
    );
    fireEvent.click(screen.getByRole("button", { name: "Open 31" }));
    expect(f.onOpen).toHaveBeenLastCalledWith("31");
    fireEvent.doubleClick(screen.getByRole("row", { name: "Row 31" }));
    expect(f.onOpen).toHaveBeenLastCalledWith("row:31");
  });
  it("supports keyboard sorting through native header actions", async () => {
    const user = userEvent.setup();
    render(<DataTable label="Records" rows={rows} {...fixture()} />);
    const amount = within(screen.getByRole("table")).getByRole("button", {
      name: "Amount",
    });
    amount.focus();
    await user.keyboard("{Enter}{Enter}");
    expect(
      screen.getByRole("columnheader", { name: "Amount" }),
    ).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByRole("link", { name: "Record 31" })).toBeInTheDocument();
  });
  it("filters source column values with AND and recovers with Reset view", () => {
    render(<DataTable label="Records" rows={rows} {...fixture()} />);
    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "Records filters" })).getByRole(
        "button",
        { name: /^Status/ },
      ),
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "ready" }));
    expect(screen.getByRole("status")).toHaveTextContent("16 rows of 32");
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "Records filters" })).getByRole(
        "button",
        { name: /^Amount/ },
      ),
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "2" }));
    expect(screen.getByRole("status")).toHaveTextContent("1 row of 32");
    expect(screen.getByRole("table")).toHaveTextContent("Record 2");
    fireEvent.click(screen.getByRole("button", { name: "Reset view" }));
    expect(screen.getByRole("status")).toHaveTextContent("32 rows");
  });
  it("dismisses filters with Escape and switches between filter and column controls", async () => {
    // FocusScope restores focus on the next animation frame; the suite's
    // no-op animation stub would suppress this browser behavior entirely.
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
      setTimeout(() => callback(performance.now()), 0),
    );
    vi.stubGlobal("cancelAnimationFrame", clearTimeout);
    const user = userEvent.setup();
    render(<DataTable label="Records" rows={rows} {...fixture()} />);
    const filters = screen.getByRole("button", { name: "Filters" });
    await user.click(filters);
    expect(
      screen.getByRole("dialog", { name: "Records filters" }),
    ).toBeVisible();
    await user.keyboard("{Escape}");
    expect(
      screen.queryByRole("dialog", { name: "Records filters" }),
    ).toBeNull();
    await waitFor(() => expect(filters).toHaveFocus());
    await user.click(filters);
    await user.click(screen.getByRole("button", { name: "Columns" }));
    expect(
      screen.queryByRole("dialog", { name: "Records filters" }),
    ).toBeNull();
    expect(
      screen.getByRole("dialog", { name: "Records visible columns" }),
    ).toBeVisible();
    await user.click(filters);
    expect(
      screen.queryByRole("dialog", { name: "Records visible columns" }),
    ).toBeNull();
    expect(
      screen.getByRole("dialog", { name: "Records filters" }),
    ).toBeVisible();
    expect(screen.getByRole("table", { name: "Records" })).toBeVisible();
  });
  it("changes page size, moves pages, and clamps when the loaded rows shrink", () => {
    const f = fixture();
    const { rerender } = render(
      <DataTable label="Records" rows={rows} {...f} />,
    );
    fireEvent.change(screen.getByLabelText("Records rows per page"), {
      target: { value: "10" },
    });
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(
      11,
    );
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Page 2 of 4")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Record 10" })).toBeInTheDocument();
    rerender(<DataTable label="Records" rows={rows.slice(0, 3)} {...f} />);
    expect(screen.getByText("Page 1 of 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });
  it("customizes density and columns with protected actions and resets on scope changes", () => {
    const f = fixture();
    const { rerender, container } = render(
      <AdvancedTable label="Records" rows={rows} scopeKey="a" {...f} />,
    );
    fireEvent.click(screen.getByRole("radio", { name: "Compact" }));
    expect(container.firstChild).toHaveAttribute("data-density", "compact");
    fireEvent.click(screen.getByRole("button", { name: "Columns" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Status" }));
    expect(screen.queryByRole("columnheader", { name: "Status" })).toBeNull();
    expect(screen.getByRole("checkbox", { name: "Actions" })).toBeDisabled();
    rerender(<AdvancedTable label="Records" rows={rows} scopeKey="b" {...f} />);
    expect(container.firstChild).toHaveAttribute("data-density", "comfortable");
    expect(
      screen.getByRole("columnheader", { name: "Status" }),
    ).toBeInTheDocument();
  });
  it("labels external windows truthfully without claiming server pagination", () => {
    render(
      <AdvancedTable
        label="Records"
        rows={rows}
        pagination="external"
        {...fixture()}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Loaded rows: 32 of 32",
    );
    expect(screen.getByRole("status")).toHaveTextContent("this loaded window");
    expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(
      33,
    );
  });
  it("offers a truly simple semantic variant and caller-owned row rendering", () => {
    const f = fixture();
    const rowAction = vi.fn();
    render(
      <SimpleTable
        label="Evidence"
        rows={rows}
        {...f}
        renderRow={(row, { key, ...props }, cells) => (
          <tr
            {...props}
            key={key}
            data-testid={`retained-${row.id}`}
            onClick={() => rowAction(row.id)}
          >
            {cells}
          </tr>
        )}
      />,
    );
    expect(screen.queryByRole("button", { name: "Filters" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Amount" })).toBeNull();
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(
      33,
    );
    fireEvent.click(screen.getByTestId("retained-31"));
    expect(rowAction).toHaveBeenCalledWith("31");
  });
});

describe("assistant actions on the visible table controller", () => {
  function setup(route = "/datasets") {
    const registry = new ComponentActionRegistry();
    const renderTable = (
      nextRoute = route,
      project = "fixture",
      second = false,
    ) => {
      registry.setScope({ user: "u", project, route: nextRoute });
      return (
        <StudioContext.Provider
          value={{ scopeKey: `u:${project}`, route: nextRoute } as any}
        >
          <ComponentActionsProvider registry={registry}>
            <AdvancedTable
              label="Records"
              rows={rows}
              scopeKey={`u:${project}`}
              {...fixture()}
            />
            {second && (
              <AdvancedTable
                label="Other"
                rows={rows}
                scopeKey={`u:${project}`}
                {...fixture()}
              />
            )}
          </ComponentActionsProvider>
        </StudioContext.Provider>
      );
    };
    const view = render(renderTable());
    const action = (label: string) => {
      const found = registry
        .inspect()
        .actions.find((action) => action.label === label);
      if (!found) throw new Error(`Missing ${label}`);
      return found;
    };
    const execute = async (label: string, value?: string) => {
      await act(async () => {
        await registry.execute(action(label).ref, value, false);
      });
    };
    return { registry, view, renderTable, action, execute };
  }
  it("revokes controls for an inactive retained pane without losing its table view", async () => {
    const registry = new ComponentActionRegistry();
    registry.setScope({ user: "u", project: "p", route: "/lab" });
    const table = (active: boolean) => (
      <ComponentActionsProvider registry={registry}>
        <AdvancedTable
          label="Records"
          rows={rows}
          {...fixture()}
          assistantActionsEnabled={active}
        />
      </ComponentActionsProvider>
    );
    const view = render(table(true));
    fireEvent.click(screen.getByRole("radio", { name: "Compact" }));
    const old = registry
      .inspect()
      .actions.find((action) => action.label === "Records: row density")!.ref;
    view.rerender(table(false));
    expect(registry.inspect().actions).toEqual([]);
    await expect(registry.execute(old, "spacious", false)).rejects.toThrow();
    view.rerender(table(true));
    expect(screen.getByRole("table").closest(".data-table")).toHaveAttribute(
      "data-density",
      "compact",
    );
    expect(
      registry
        .inspect()
        .actions.find((action) => action.label === "Records: row density")
        ?.state,
    ).toBe("compact");
  });
  it("updates density, sort, filters and columns through the same controller as visible controls", async () => {
    const { execute, action } = setup();
    await execute("Records: row density", "compact");
    expect(screen.getByRole("table").closest(".data-table")).toHaveAttribute(
      "data-density",
      "compact",
    );
    expect(screen.getByRole("radio", { name: "Compact" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    fireEvent.click(screen.getByRole("radio", { name: "Spacious" }));
    expect(action("Records: row density").state).toBe("spacious");
    await execute("Records: sort Amount", "descending");
    expect(
      screen.getByRole("columnheader", { name: "Amount" }),
    ).toHaveAttribute("aria-sort", "descending");
    expect(
      within(screen.getByRole("table")).getAllByRole("row")[1],
    ).toHaveTextContent("Record 31");
    fireEvent.click(screen.getByRole("button", { name: "Amount" }));
    expect(action("Records: sort Amount").state).toBe("none");
    await execute("Records: filter Status", "string:ready");
    expect(screen.getByRole("status")).toHaveTextContent("16 rows of 32");
    await execute("Records: filter Amount", "number:2");
    expect(screen.getByRole("status")).toHaveTextContent("1 row of 32");
    expect(screen.getByRole("table")).toHaveTextContent("Record 2");
    await execute("Records: Status visibility", "hide");
    expect(screen.queryByRole("columnheader", { name: "Status" })).toBeNull();
    await execute("Records: reset table view");
    expect(screen.getByRole("status")).toHaveTextContent("32 rows");
    expect(
      screen.getByRole("columnheader", { name: "Status" }),
    ).toBeInTheDocument();
    expect(action("Records: row density").state).toBe("comfortable");
  });
  it("denies hiding protected actions and rejects unoffered values", async () => {
    const { registry, action } = setup();
    expect(action("Records: Actions visibility").options).toEqual([
      { value: "show", label: "Show column" },
    ]);
    await expect(
      registry.execute(
        action("Records: Actions visibility").ref,
        "hide",
        false,
      ),
    ).rejects.toThrow("allowed component value");
    await expect(
      registry.execute(action("Records: row density").ref, "giant", false),
    ).rejects.toThrow("allowed component value");
    expect(screen.getByRole("button", { name: "Open 0" })).toBeInTheDocument();
  });
  it("revokes old references across routes, projects, private views and unmount, with unique table identities", async () => {
    const { registry, view, renderTable, action } = setup();
    const old = action("Records: row density").ref;
    view.rerender(renderTable("/models", "fixture", true));
    await expect(registry.execute(old, "compact", false)).rejects.toThrow(
      "stale",
    );
    const actions = registry.inspect().actions;
    expect(new Set(actions.map((action) => action.id)).size).toBe(
      actions.length,
    );
    const model = action("Records: row density").ref;
    view.rerender(renderTable("/models", "other"));
    await expect(registry.execute(model, "compact", false)).rejects.toThrow(
      "stale",
    );
    view.rerender(renderTable("/settings", "other"));
    expect(registry.inspect().actions).toEqual([]);
    view.rerender(renderTable("/datasets", "other"));
    const current = action("Records: row density").ref;
    view.unmount();
    await expect(registry.execute(current, "compact", false)).rejects.toThrow(
      "stale",
    );
  });
  it("does not expose credential column values as assistant filter options", () => {
    const registry = new ComponentActionRegistry();
    registry.setScope({ user: "u", project: "p", route: "/datasets" });
    render(
      <ComponentActionsProvider registry={registry}>
        <AdvancedTable
          label="Credentials"
          rows={[{ id: "1", token: "must-stay-private" }]}
          getRowId={(row) => row.id}
          columns={[
            { id: "token", label: "API token", value: (row) => row.token },
          ]}
        />
      </ComponentActionsProvider>,
    );
    expect(JSON.stringify(registry.inspect())).not.toContain(
      "must-stay-private",
    );
    expect(
      registry.inspect().actions.some((action) => action.kind === "filter"),
    ).toBe(false);
  });
  it("preserves bounded option listing while applying one explicit filter value", async () => {
    const registry = new ComponentActionRegistry();
    registry.setScope({ user: "u", project: "p", route: "/datasets" });
    const records = Array.from({ length: 125 }, (_, id) => ({
      id: String(id),
      value: id,
    }));
    render(
      <ComponentActionsProvider registry={registry}>
        <AdvancedTable
          label="Many"
          rows={records}
          getRowId={(row) => row.id}
          columns={[{ id: "value", label: "Value", value: (row) => row.value }]}
        />
      </ComponentActionsProvider>,
    );
    const filter = registry
      .inspect()
      .actions.find((action) => action.label === "Many: filter Value")!;
    expect(filter.options).toHaveLength(100);
    expect(filter.omitted_options).toBe(26);
    await act(async () => {
      await registry.execute(filter.ref, "number:7", false);
    });
    expect(screen.getByRole("status")).toHaveTextContent("1 row of 125");
  });
});
