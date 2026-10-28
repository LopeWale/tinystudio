import {
  useContext,
  useLayoutEffect,
  useId,
  useRef,
  useMemo,
  useState,
  useSyncExternalStore,
  type CSSProperties,
  type HTMLAttributes,
  type ReactNode,
} from "react";
import {
  RecordTableController,
  tableValueKey,
  tableValueLabel,
  type TablePagination,
  type RecordTableSnapshot,
} from "../../controllers/recordTable";
import { StudioContext } from "../../app/StudioContext";
import { useComponentActionRegistry } from "../../features/assistant/actions";
import type {
  ComponentActionDefinition,
  ComponentActionRegistry,
} from "../../controllers/componentActions";
import { Dialog, Popover } from "react-aria-components";
import { useDismissOnOutsidePress } from "../boardui/utils/use-dismiss-on-outside-press";
import { Checkbox } from "../boardui/base/checkbox/checkbox";
import { Button as SourceButton } from "../affectui/button-clean";
import { RiMenuLine } from "@remixicon/react";
import { FilterMenu, SegmentedControl } from "../affectui/controls";
import { Button } from "./primitives";
import "./data-table.css";
export type TableColumn<T> = {
  id: string;
  label: string;
  value: (row: T) => string | number | boolean | null | undefined;
  render?: (row: T) => ReactNode;
  sortable?: boolean;
  filterable?: boolean;
  hideable?: boolean;
  /** Optional presentation width; follows the column when other columns hide. */
  width?: CSSProperties["width"];
};
export type DataTableProps<T> = {
  label: string;
  rows: readonly T[];
  columns: readonly TableColumn<T>[];
  getRowId: (row: T) => string;
  rowProps?: (row: T) => HTMLAttributes<HTMLTableRowElement>;
  renderRow?: (
    row: T,
    props: HTMLAttributes<HTMLTableRowElement> & { key: string },
    cells: ReactNode,
  ) => ReactNode;
  empty?: ReactNode;
  /** Feature-owned search and selection controls, beside the shared view controls. */
  toolbarContent?: ReactNode;
  scopeKey?: string;
  /** Revoke assistant controls when a retained view is not active. */
  assistantActionsEnabled?: boolean;
  pagination?: TablePagination;
};
function TableView<T>({
  label,
  rows,
  columns,
  getRowId,
  rowProps,
  renderRow,
  empty,
  toolbarContent,
  scopeKey,
  assistantActionsEnabled = true,
  pagination = "client",
  variant,
}: DataTableProps<T> & { variant: "simple" | "standard" | "advanced" }) {
  if (variant === "simple") pagination = "none";
  // Scope changes replace the state owner before the new scope is displayed.
  const controller = useMemo(
    () => new RecordTableController(rows, columns, getRowId, pagination),
    [scopeKey],
  );
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
  );
  const [filtersOpen, setFiltersOpen] = useState(false);
  useLayoutEffect(() => setFiltersOpen(false), [scopeKey]);
  useLayoutEffect(
    () => controller.update(rows, columns, getRowId, pagination),
    [controller, rows, columns, getRowId, pagination],
  );
  const visible = columns.filter((column) =>
    state.visibleColumns.includes(column.id),
  );
  const groups = columns
    .filter((column) => column.filterable !== false)
    .map((column) => {
      const values = new Map<string, string>();
      rows.forEach((row) => {
        const value = column.value(row);
        values.set(tableValueKey(value), tableValueLabel(value));
      });
      return {
        id: column.id,
        label: column.label,
        chipPrefix: `${column.label}: `,
        options: [...values].map(([id, label]) => ({ id, label })),
      };
    });
  const count = Object.values(state.filters).reduce(
    (sum, values) => sum + values.length,
    0,
  );
  const densityControl = (
    <SegmentedControl
      variant="tray"
      size="sm"
      className="data-table__density"
      aria-label={`${label} density`}
      value={state.density}
      onChange={(density) => controller.setDensity(density)}
      options={[
        {
          value: "compact",
          label: "Compact",
          ariaLabel: "Compact",
          icon: <RiMenuLine className="data-table__density-icon--compact" />,
        },
        {
          value: "comfortable",
          label: "Comfortable",
          ariaLabel: "Comfortable",
          icon: <RiMenuLine />,
        },
        {
          value: "spacious",
          label: "Spacious",
          ariaLabel: "Spacious",
          icon: <RiMenuLine className="data-table__density-icon--spacious" />,
        },
      ]}
    />
  );
  return (
    <div
      className="data-table"
      data-density={state.density}
      data-table-variant={variant}
    >
      {variant !== "simple" && assistantActionsEnabled && (
        <TableComponentActions
          label={label}
          controller={controller}
          state={state}
          columns={columns}
          groups={groups}
          scopeKey={scopeKey}
        />
      )}
      {variant !== "simple" && (
        <TableToolbar
          key={scopeKey}
          label={label}
          controller={controller}
          state={state}
          columns={columns}
          showFilters={groups.length > 0}
          filtersOpen={filtersOpen}
          onToggleFilters={() => setFiltersOpen((open) => !open)}
          count={count}
          densityControl={densityControl}
          advanced={variant === "advanced"}
          content={toolbarContent}
          onCloseFilters={() => setFiltersOpen(false)}
          filtersContent={
            <FilterMenu
              key={scopeKey}
              title={`${label} filters`}
              groups={groups}
              value={Object.fromEntries(
                Object.entries(state.filters).map(([id, values]) => [
                  id,
                  [...values],
                ]),
              )}
              onChange={(filters) => controller.setFilters(filters)}
            />
          }
        />
      )}
      <div
        className="data-table__scroll"
        role="region"
        aria-label={`${label} rows`}
        tabIndex={0}
      >
        <table aria-label={label}>
          <colgroup>
            {visible.map((column) => (
              <col key={column.id} style={{ width: column.width }} />
            ))}
          </colgroup>
          <thead>
            <tr>
              {visible.map((column) => (
                <th
                  key={column.id}
                  scope="col"
                  aria-sort={
                    state.sort?.column === column.id
                      ? state.sort.direction
                      : undefined
                  }
                >
                  {variant === "simple" || column.sortable === false ? (
                    column.label
                  ) : (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => controller.toggleSort(column.id)}
                    >
                      {column.label}
                      <span aria-hidden="true">
                        {state.sort?.column === column.id
                          ? state.sort.direction === "ascending"
                            ? " ↑"
                            : " ↓"
                          : " ↕"}
                      </span>
                    </Button>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {state.rows.map((row, index) => {
              const props = { ...rowProps?.(row), key: state.rowIds[index] };
              const cells = visible.map((column) => (
                <td key={column.id}>
                  {column.render
                    ? column.render(row)
                    : tableValueLabel(column.value(row))}
                </td>
              ));
              if (renderRow) return renderRow(row, props, cells);
              const { key, ...attributes } = props;
              return (
                <tr {...attributes} key={key}>
                  {cells}
                </tr>
              );
            })}
          </tbody>
        </table>
        {!state.filtered && (
          <div className="data-table__empty">
            {rows.length
              ? "No matching rows. Clear filters or reset the view to see loaded records."
              : (empty ?? "No records available.")}
          </div>
        )}
      </div>
      {variant !== "simple" && (
        <div className="data-table__footer">
          <span role="status">
            {pagination === "external"
              ? `Loaded rows: ${state.filtered} of ${state.total}. Filters and sorting apply to this loaded window.`
              : `${state.filtered} ${state.filtered === 1 ? "row" : "rows"}${state.filtered !== state.total ? ` of ${state.total}` : ""}`}
          </span>
          {pagination === "client" && (
            <div className="data-table__paging">
              <label>
                Rows per page{" "}
                <select
                  aria-label={`${label} rows per page`}
                  value={state.pageSize}
                  onChange={(event) =>
                    controller.setPageSize(Number(event.target.value))
                  }
                >
                  {[10, 25, 50, 100].map((size) => (
                    <option value={size} key={size}>
                      {size}
                    </option>
                  ))}
                </select>
              </label>
              <Button
                size="sm"
                disabled={state.page === 0}
                onClick={() => controller.setPage(state.page - 1)}
              >
                Previous
              </Button>
              <span>
                Page {state.page + 1} of {state.pageCount}
              </span>
              <Button
                size="sm"
                disabled={state.page + 1 >= state.pageCount}
                onClick={() => controller.setPage(state.page + 1)}
              >
                Next
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
/** Registers committed controller operations, never DOM selectors or row payloads. */
function TableComponentActions<T>({
  label,
  controller,
  state,
  columns,
  groups,
  scopeKey,
}: {
  label: string;
  controller: RecordTableController<T>;
  state: RecordTableSnapshot<T>;
  columns: readonly TableColumn<T>[];
  groups: readonly {
    id: string;
    label: string;
    options: readonly { id: string; label: string }[];
  }[];
  scopeKey?: string;
}) {
  const registry = useComponentActionRegistry();
  const studio = useContext(StudioContext);
  const id = useId();
  const registrations = useRef<
    ReturnType<ComponentActionRegistry["register"]>[]
  >([]);
  const definitions: ComponentActionDefinition[] = [
    {
      id: `table.${id}.density`,
      label: `${label}: row density`,
      kind: "select",
      state: state.density,
      options: ["compact", "comfortable", "spacious"].map((value) => ({
        value,
        label: value,
      })),
      run: (value) =>
        controller.setDensity(value as RecordTableSnapshot<T>["density"]),
    },
    {
      id: `table.${id}.reset`,
      label: `${label}: reset table view`,
      kind: "select",
      run: () => controller.reset(),
    },
  ];
  for (const column of columns) {
    if (column.sortable !== false)
      definitions.push({
        id: `table.${id}.sort.${column.id}`,
        label: `${label}: sort ${column.label}`,
        kind: "select",
        state: state.sort?.column === column.id ? state.sort.direction : "none",
        options: [
          { value: "none", label: "No sorting" },
          { value: "ascending", label: "Ascending" },
          { value: "descending", label: "Descending" },
        ],
        run: (value) =>
          controller.setSort(
            column.id,
            value === "none" ? null : (value as "ascending" | "descending"),
          ),
      });
    const visible = state.visibleColumns.includes(column.id);
    definitions.push({
      id: `table.${id}.column.${column.id}`,
      label: `${label}: ${column.label} visibility`,
      kind: "select",
      state: visible ? "show" : "hide",
      options: [
        { value: "show", label: "Show column" },
        ...(!visible || controller.canHide(column.id)
          ? [{ value: "hide", label: "Hide column" }]
          : []),
      ],
      run: (value) => {
        if (
          value === "hide" &&
          !controller.canHide(column.id) &&
          controller.getSnapshot().visibleColumns.includes(column.id)
        )
          throw new Error("This column must remain visible");
        controller.setColumnVisible(column.id, value === "show");
      },
    });
  }
  for (const group of groups.filter(
    (group) =>
      !/password|token|secret|credential|api.?key|authorization|cookie|system_prompt/i.test(
        `${group.id} ${group.label}`,
      ),
  ))
    definitions.push({
      id: `table.${id}.filter.${group.id}`,
      label: `${label}: filter ${group.label}`,
      kind: "filter",
      state: state.filters[group.id] || [],
      options: [
        { value: "clear", label: "Clear this column filter" },
        ...group.options
          .filter((option) => option.id.length <= 4000)
          .map((option) => ({
            value: option.id,
            label: option.label,
          })),
      ],
      run: (value) =>
        controller.setFilters({
          ...controller.getSnapshot().filters,
          [group.id]: value === "clear" ? [] : [value!],
        }),
    });
  const identity = JSON.stringify(
    definitions.map((definition) => definition.id),
  );
  useLayoutEffect(() => {
    if (!registry) return;
    const handles = definitions.map((definition) =>
      registry.register(definition),
    );
    registrations.current = handles;
    return () => {
      handles.forEach((handle) => handle.dispose());
      if (registrations.current === handles) registrations.current = [];
    };
  }, [
    registry,
    studio?.scopeKey,
    studio?.route,
    studio?.project,
    studio?.user?.id,
    scopeKey,
    controller,
    identity,
  ]);
  useLayoutEffect(() => {
    registrations.current.forEach((handle, index) =>
      handle.update(definitions[index]),
    );
  });
  return null;
}

/** One adaptive toolbar shared by both interactive table variants. */
function TableToolbar<T>({
  label,
  controller,
  state,
  columns,
  showFilters,
  filtersOpen,
  onToggleFilters,
  count,
  densityControl,
  advanced,
  content,
  filtersContent,
  onCloseFilters,
}: {
  label: string;
  controller: RecordTableController<T>;
  state: RecordTableSnapshot<T>;
  columns: readonly TableColumn<T>[];
  showFilters: boolean;
  filtersOpen: boolean;
  onToggleFilters: () => void;
  count: number;
  densityControl: ReactNode;
  advanced: boolean;
  content?: ReactNode;
  filtersContent: ReactNode;
  onCloseFilters: () => void;
}) {
  const [columnsOpen, setColumnsOpen] = useState(false);
  const filterTrigger = useRef<HTMLButtonElement>(null);
  const filterPopover = useRef<HTMLElement>(null);
  const filterId = useId();
  useDismissOnOutsidePress(filtersOpen, onCloseFilters, [
    filterTrigger,
    filterPopover,
  ]);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const popover = useRef<HTMLElement>(null);
  useDismissOnOutsidePress(columnsOpen, () => setColumnsOpen(false), [
    trigger,
    popover,
  ]);
  return (
    <div
      className="data-table__toolbar"
      role="group"
      aria-label={`${label} table controls`}
    >
      {content != null && <div className="data-table__context">{content}</div>}
      <div className="data-table__view-actions">
        {showFilters && (
          <SourceButton
            ref={filterTrigger}
            variant="secondary"
            className="studio-button"
            aria-haspopup="dialog"
            aria-expanded={filtersOpen}
            aria-controls={filtersOpen ? filterId : undefined}
            onClick={() => {
              setColumnsOpen(false);
              onToggleFilters();
            }}
          >
            Filters{count ? ` (${count})` : ""}
          </SourceButton>
        )}
        <SourceButton
          variant="secondary"
          className="studio-button"
          ref={trigger}
          aria-haspopup="dialog"
          aria-expanded={columnsOpen}
          aria-controls={columnsOpen ? panelId : undefined}
          onClick={() => {
            onCloseFilters();
            setColumnsOpen((open) => !open);
          }}
        >
          Columns
        </SourceButton>
        <Button variant="ghost" onClick={() => controller.reset()}>
          Reset view
        </Button>
      </div>
      {advanced && densityControl}
      <Popover
        ref={filterPopover}
        isNonModal
        triggerRef={filterTrigger}
        isOpen={filtersOpen}
        onOpenChange={(open) => {
          if (!open) onCloseFilters();
        }}
        placement="bottom start"
        offset={6}
        className="data-table__filter-popover"
      >
        <Dialog
          id={filterId}
          aria-label={`${label} filters`}
          className="data-table__filter-dialog"
        >
          {filtersContent}
        </Dialog>
      </Popover>
      <Popover
        ref={popover}
        isNonModal
        triggerRef={trigger}
        isOpen={columnsOpen}
        onOpenChange={setColumnsOpen}
        placement="bottom end"
        offset={6}
        className="data-table__column-popover"
      >
        <Dialog
          id={panelId}
          aria-label={`${label} visible columns`}
          className="data-table__column-dialog"
        >
          {!advanced && densityControl}
          <fieldset>
            <legend>Visible columns</legend>
            {columns.map((column) => (
              <Checkbox
                key={column.id}
                size="sm"
                isSelected={state.visibleColumns.includes(column.id)}
                isDisabled={
                  state.visibleColumns.includes(column.id) &&
                  !controller.canHide(column.id)
                }
                onChange={(visible) =>
                  controller.setColumnVisible(column.id, visible)
                }
              >
                {column.label}
              </Checkbox>
            ))}
          </fieldset>
        </Dialog>
      </Popover>
    </div>
  );
}

/** Semantic records without view controls; every supplied row remains visible. */
export function SimpleTable<T>(props: DataTableProps<T>) {
  return <TableView {...props} variant="simple" />;
}
/** Standard browsing; density is available within the Columns menu. */
export function DataTable<T>(props: DataTableProps<T>) {
  return <TableView {...props} variant="standard" />;
}
/** Full workbench; density is directly available in the toolbar. */
export function AdvancedTable<T>(props: DataTableProps<T>) {
  return <TableView {...props} variant="advanced" />;
}
