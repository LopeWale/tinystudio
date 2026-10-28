export type TableValue = string | number | boolean | null | undefined;
export type RecordColumn<T> = {
  id: string;
  label: string;
  value: (row: T) => TableValue;
  sortable?: boolean;
  filterable?: boolean;
  hideable?: boolean;
  /** Explicit render-only controls remain available even in customized views. */
  render?: unknown;
};
export type TableDensity = "compact" | "comfortable" | "spacious";
export type TablePagination = "client" | "external" | "none";
export type RecordTableSnapshot<T> = Readonly<{
  rows: readonly T[];
  rowIds: readonly string[];
  total: number;
  filtered: number;
  page: number;
  pageSize: number;
  pageSizeMode: "manual" | "fit";
  pageCount: number;
  density: TableDensity;
  visibleColumns: readonly string[];
  filters: Readonly<Record<string, readonly string[]>>;
  sort: Readonly<{
    column: string;
    direction: "ascending" | "descending";
  }> | null;
}>;
/** Typed values keep filtering separate from formatted, interactive cell content. */
export function tableValueKey(value: TableValue): string {
  return value == null ? "null:" : `${typeof value}:${String(value)}`;
}
export function tableValueLabel(value: TableValue): string {
  return value == null ? "Not available" : String(value);
}
function isControlColumn<T>(column: RecordColumn<T>): boolean {
  return Boolean(
    column.render && column.sortable === false && column.filterable === false,
  );
}
export function canHideColumn<T>(column: RecordColumn<T>): boolean {
  return column.hideable !== false && !isControlColumn(column);
}

/** Owns local table view invariants; never fetches records or owns row actions. */
export class RecordTableController<T> {
  private listeners = new Set<() => void>();
  private page = 0;
  private pageSize = 25;
  private pageSizeMode: "manual" | "fit";
  private density: TableDensity = "comfortable";
  private hidden = new Set<string>();
  private filters: Record<string, readonly string[]> = {};
  private sort: RecordTableSnapshot<T>["sort"] = null;
  private snapshot!: RecordTableSnapshot<T>;
  constructor(
    private rows: readonly T[],
    private columns: readonly RecordColumn<T>[],
    private getRowId: (row: T) => string,
    private pagination: TablePagination = "client",
    private initialPageSizeMode: "manual" | "fit" = "manual",
  ) {
    this.pageSizeMode = initialPageSizeMode;
    this.rebuild();
  }
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  getSnapshot = () => this.snapshot;
  update(
    rows: readonly T[],
    columns: readonly RecordColumn<T>[],
    getRowId: (row: T) => string,
    pagination: TablePagination = "client",
  ) {
    this.rows = rows;
    this.columns = columns;
    this.getRowId = getRowId;
    this.pagination = pagination;
    const available = new Set(columns.map((column) => column.id));
    this.hidden = new Set(
      [...this.hidden].filter(
        (id) =>
          available.has(id) &&
          canHideColumn(columns.find((column) => column.id === id)!),
      ),
    );
    if (columns.length && columns.every((column) => this.hidden.has(column.id)))
      this.hidden.delete(columns[0].id);
    const dataColumns = columns.filter((column) => !isControlColumn(column));
    if (
      dataColumns.length &&
      dataColumns.every((column) => this.hidden.has(column.id))
    )
      this.hidden.delete(dataColumns[0].id);
    this.filters = Object.fromEntries(
      Object.entries(this.filters).filter(([id]) =>
        columns.some(
          (column) => column.id === id && column.filterable !== false,
        ),
      ),
    );
    if (
      !columns.some(
        (column) =>
          column.id === this.sort?.column && column.sortable !== false,
      )
    )
      this.sort = null;
    this.rebuild();
  }
  setFilters(filters: Readonly<Record<string, readonly string[]>>) {
    this.filters = Object.fromEntries(
      this.columns
        .filter(
          (column) => column.filterable !== false && filters[column.id]?.length,
        )
        .map((column) => [
          column.id,
          Object.freeze([...new Set(filters[column.id])]),
        ]),
    );
    this.page = 0;
    this.rebuild();
  }
  toggleSort(id: string) {
    if (
      !this.columns.some(
        (column) => column.id === id && column.sortable !== false,
      )
    )
      return;
    this.sort =
      this.sort?.column === id
        ? this.sort.direction === "ascending"
          ? { column: id, direction: "descending" }
          : null
        : { column: id, direction: "ascending" };
    this.page = 0;
    this.rebuild();
  }
  setSort(id: string, direction: "ascending" | "descending" | null) {
    if (
      !this.columns.some(
        (column) => column.id === id && column.sortable !== false,
      )
    )
      return;
    if (
      direction !== null &&
      direction !== "ascending" &&
      direction !== "descending"
    )
      return;
    this.sort = direction ? { column: id, direction } : null;
    this.page = 0;
    this.rebuild();
  }
  canHide(id: string): boolean {
    const column = this.columns.find((item) => item.id === id);
    if (!column || !canHideColumn(column)) return false;
    const visible = this.columns.filter((item) => !this.hidden.has(item.id));
    return (
      visible.length > 1 &&
      (isControlColumn(column) ||
        visible.filter((item) => !isControlColumn(item)).length > 1)
    );
  }
  setColumnVisible(id: string, visible: boolean) {
    if (
      !this.columns.some((column) => column.id === id) ||
      (!visible && !this.canHide(id))
    )
      return;
    if (visible) this.hidden.delete(id);
    else this.hidden.add(id);
    this.rebuild();
  }
  setDensity(density: TableDensity) {
    if (!["compact", "comfortable", "spacious"].includes(density)) return;
    this.density = density;
    this.rebuild();
  }
  setPageSize(size: number | "fit") {
    if (size !== "fit" && ![10, 25, 50, 100].includes(size)) return;
    this.pageSizeMode = size === "fit" ? "fit" : "manual";
    if (size !== "fit") this.pageSize = size;
    this.page = 0;
    this.rebuild();
  }
  /** Capacity changes keep the previous first row on the resized page when possible. */
  setFittedPageSize(size: number) {
    if (
      this.pagination !== "client" ||
      this.pageSizeMode !== "fit" ||
      !Number.isFinite(size) ||
      size < 1
    )
      return;
    const capacity = Math.floor(size);
    if (capacity === this.pageSize) return;
    const firstRow = this.page * this.pageSize;
    this.pageSize = capacity;
    this.page = Math.floor(firstRow / capacity);
    this.rebuild();
  }
  setPage(page: number) {
    if (!Number.isFinite(page)) return;
    this.page = Math.max(0, Math.floor(page));
    this.rebuild();
  }
  reset() {
    this.page = 0;
    this.pageSize = 25;
    this.pageSizeMode = this.initialPageSizeMode;
    this.density = "comfortable";
    this.hidden.clear();
    this.filters = {};
    this.sort = null;
    this.rebuild();
  }
  private rebuild() {
    const rows = this.rows
      .map((row, index) => ({ row, index, id: this.getRowId(row) }))
      .filter(({ row }) =>
        this.columns.every(
          (column) =>
            !this.filters[column.id]?.length ||
            this.filters[column.id].includes(tableValueKey(column.value(row))),
        ),
      );
    const sort = this.sort;
    const column = this.columns.find((column) => column.id === sort?.column);
    if (column && sort)
      rows.sort((a, b) => {
        const av = column.value(a.row),
          bv = column.value(b.row);
        // Missing values always come last, including descending order.
        if (av == null || bv == null)
          return av == null && bv == null
            ? a.index - b.index
            : av == null
              ? 1
              : -1;
        const order =
          typeof av === "number" && typeof bv === "number"
            ? Number.isNaN(av)
              ? Number.isNaN(bv)
                ? 0
                : 1
              : Number.isNaN(bv)
                ? -1
                : av - bv
            : String(av).localeCompare(String(bv), undefined, {
                numeric: true,
                sensitivity: "base",
              });
        return (
          (sort.direction === "ascending" ? order : -order) || a.index - b.index
        );
      });
    const pageCount =
      this.pagination === "client"
        ? Math.max(1, Math.ceil(rows.length / this.pageSize))
        : 1;
    this.page =
      this.pagination === "client" ? Math.min(this.page, pageCount - 1) : 0;
    const window =
      this.pagination === "client"
        ? rows.slice(this.page * this.pageSize, (this.page + 1) * this.pageSize)
        : rows;
    this.snapshot = Object.freeze({
      rows: Object.freeze(window.map((item) => item.row)),
      rowIds: Object.freeze(window.map((item) => item.id)),
      total: this.rows.length,
      filtered: rows.length,
      page: this.page,
      pageSize: this.pageSize,
      pageSizeMode: this.pageSizeMode,
      pageCount,
      density: this.density,
      visibleColumns: Object.freeze(
        this.columns
          .filter((column) => !this.hidden.has(column.id))
          .map((column) => column.id),
      ),
      filters: Object.freeze({ ...this.filters }),
      sort: sort ? Object.freeze({ ...sort }) : null,
    });
    this.listeners.forEach((listener) => listener());
  }
}
