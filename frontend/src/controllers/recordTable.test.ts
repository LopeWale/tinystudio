import { describe, expect, it, vi } from "vitest";
import { RecordTableController, tableValueKey } from "./recordTable";
type Row = { id: string; amount: number | null; status: string; name: string };
const rows: Row[] = [
  { id: "a", amount: 10, status: "ready", name: "Record 10" },
  { id: "b", amount: 2, status: "ready", name: "Record 2" },
  { id: "c", amount: 2, status: "failed", name: "Record 2" },
  { id: "d", amount: null, status: "ready", name: "Missing" },
];
const columns = [
  { id: "name", label: "Name", value: (row: Row) => row.name },
  { id: "amount", label: "Amount", value: (row: Row) => row.amount },
  { id: "status", label: "Status", value: (row: Row) => row.status },
  {
    id: "actions",
    label: "Actions",
    value: () => null,
    render: () => null,
    sortable: false,
    filterable: false,
    hideable: false,
  },
];
const create = () => new RecordTableController(rows, columns, (row) => row.id);
describe("RecordTableController view invariants", () => {
  it("sorts numbers numerically, preserves ties and keeps null last in both directions", () => {
    const c = create();
    c.toggleSort("amount");
    expect(c.getSnapshot().rowIds).toEqual(["b", "c", "a", "d"]);
    c.toggleSort("amount");
    expect(c.getSnapshot().rowIds).toEqual(["a", "b", "c", "d"]);
    c.toggleSort("amount");
    expect(c.getSnapshot().rowIds).toEqual(["a", "b", "c", "d"]);
    expect(c.getSnapshot().sort).toBeNull();
  });
  it("uses natural string order and combines typed column filters with AND", () => {
    const c = create();
    c.toggleSort("name");
    expect(c.getSnapshot().rowIds).toEqual(["d", "b", "c", "a"]);
    c.setFilters({
      amount: [tableValueKey(2)],
      status: [tableValueKey("ready")],
    });
    expect(c.getSnapshot().rowIds).toEqual(["b"]);
    expect(c.getSnapshot().filtered).toBe(1);
    expect(c.getSnapshot().total).toBe(4);
    expect(tableValueKey(2)).not.toBe(tableValueKey("2"));
  });
  it("clamps pages after row updates and resets page when filters or size change", () => {
    const many = Array.from({ length: 61 }, (_, index) => ({
      ...rows[0],
      id: String(index),
      amount: index,
    }));
    const c = new RecordTableController(many, columns, (row) => row.id);
    c.setPage(99);
    expect(c.getSnapshot().page).toBe(2);
    expect(c.getSnapshot().rows).toHaveLength(11);
    c.update(many.slice(0, 12), columns, (row) => row.id);
    expect(c.getSnapshot().page).toBe(0);
    expect(c.getSnapshot().rows).toHaveLength(12);
    c.update(many, columns, (row) => row.id);
    c.setPage(1);
    c.setPageSize(10);
    expect(c.getSnapshot().page).toBe(0);
    expect(c.getSnapshot().pageCount).toBe(7);
    c.setPage(5);
    c.setFilters({ status: [tableValueKey("failed")] });
    expect(c.getSnapshot().page).toBe(0);
    expect(c.getSnapshot().filtered).toBe(0);
  });
  it("never locally pages an external window", () => {
    const many = Array.from({ length: 40 }, (_, index) => ({
      ...rows[0],
      id: String(index),
    }));
    const c = new RecordTableController(
      many,
      columns,
      (row) => row.id,
      "external",
    );
    c.setPage(9);
    expect(c.getSnapshot().page).toBe(0);
    expect(c.getSnapshot().rows).toHaveLength(40);
    expect(c.getSnapshot().pageCount).toBe(1);
  });
  it("guards action columns and the last visible column", () => {
    const c = create();
    c.setColumnVisible("actions", false);
    expect(c.getSnapshot().visibleColumns).toContain("actions");
    const one = new RecordTableController(rows, [columns[0]], (row) => row.id);
    one.setColumnVisible("name", false);
    expect(one.getSnapshot().visibleColumns).toEqual(["name"]);
    c.setColumnVisible("name", false);
    c.setColumnVisible("status", false);
    c.setColumnVisible("amount", false);
    expect(c.getSnapshot().visibleColumns).toEqual(["amount", "actions"]);
    c.toggleSort("actions");
    expect(c.getSnapshot().sort).toBeNull();
    c.setFilters({ actions: ["null:"] });
    expect(c.getSnapshot().filters).toEqual({});
  });
  it("publishes immutable view state and restores defaults on reset", () => {
    const c = create(),
      listener = vi.fn(),
      unsubscribe = c.subscribe(listener);
    c.setDensity("compact");
    c.setColumnVisible("status", false);
    c.setPageSize(10);
    c.setFilters({ amount: [tableValueKey(2)] });
    const state = c.getSnapshot();
    expect(Object.isFrozen(state)).toBe(true);
    expect(Object.isFrozen(state.filters.amount)).toBe(true);
    expect(Object.isFrozen(state.visibleColumns)).toBe(true);
    expect(state.rows).toHaveLength(2);
    c.reset();
    expect(c.getSnapshot()).toMatchObject({
      density: "comfortable",
      page: 0,
      pageSize: 25,
      filters: {},
      sort: null,
    });
    expect(c.getSnapshot().visibleColumns).toHaveLength(4);
    unsubscribe();
    const calls = listener.mock.calls.length;
    c.setDensity("spacious");
    expect(listener).toHaveBeenCalledTimes(calls);
  });
});

describe("workspace-fitted table paging", () => {
  const many = Array.from({ length: 61 }, (_, index) => ({
    ...rows[0],
    id: String(index),
  }));
  const fit = () =>
    new RecordTableController(many, columns, (row) => row.id, "client", "fit");
  it("retains the previous first row within the resized page and clamps after data shrinks", () => {
    const c = fit();
    c.setFittedPageSize(10);
    c.setPage(2);
    expect(c.getSnapshot().rowIds[0]).toBe("20");
    c.setFittedPageSize(6);
    expect(c.getSnapshot().page).toBe(3);
    expect(c.getSnapshot().rowIds).toEqual([
      "18",
      "19",
      "20",
      "21",
      "22",
      "23",
    ]);
    c.update(many.slice(0, 7), columns, (row) => row.id);
    expect(c.getSnapshot().page).toBe(1);
    expect(c.getSnapshot().rowIds).toEqual(["6"]);
    c.update([], columns, (row) => row.id);
    expect(c.getSnapshot()).toMatchObject({ page: 0, pageCount: 1, rows: [] });
  });
  it("partitions all rows across capacity-based pages without omissions or duplicates", () => {
    const c = fit();
    c.setFittedPageSize(7);
    const visited: string[] = [];
    for (let page = 0; page < c.getSnapshot().pageCount; page++) {
      c.setPage(page);
      visited.push(...c.getSnapshot().rowIds);
    }
    expect(visited).toEqual(many.map((row) => row.id));
  });
  it("ignores repeated capacity, invalid capacities and measurements after manual override", () => {
    const c = fit();
    const changed = vi.fn();
    c.subscribe(changed);
    c.setFittedPageSize(6);
    expect(changed).toHaveBeenCalledTimes(1);
    for (const size of [6, 0, -1, NaN, Infinity]) c.setFittedPageSize(size);
    expect(changed).toHaveBeenCalledTimes(1);
    c.setPageSize(10);
    c.setFittedPageSize(3);
    expect(c.getSnapshot()).toMatchObject({
      pageSizeMode: "manual",
      pageSize: 10,
    });
    c.reset();
    expect(c.getSnapshot().pageSizeMode).toBe("fit");
    c.setFittedPageSize(3);
    expect(c.getSnapshot().pageSize).toBe(3);
  });
  it.each(["external", "none"] as const)(
    "never capacity-pages %s records",
    (pagination) => {
      const c = new RecordTableController(
        many,
        columns,
        (row) => row.id,
        pagination,
        "fit",
      );
      c.setFittedPageSize(3);
      c.setPage(8);
      expect(c.getSnapshot().rowIds).toEqual(many.map((row) => row.id));
      expect(c.getSnapshot()).toMatchObject({
        page: 0,
        pageCount: 1,
        total: 61,
        filtered: 61,
      });
    },
  );
});
