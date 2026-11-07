import { useState, type ReactNode } from "react";
import { SegmentedControl } from "../../components/affectui/controls";
import { cx } from "../../components/boardui/utils/cx";

/** Show the complete layer before selection; preserve both representations while inspecting evidence. */
export function AnalysisNavigator({
  id,
  label,
  selected,
  map,
  table,
}: {
  id: string;
  label: string;
  selected: boolean;
  map: ReactNode;
  table: ReactNode;
}) {
  const [view, setView] = useState<"map" | "table">("map");
  return (
    <div
      className={cx(
        "interp-master capture-navigator",
        selected && "capture-navigator--selected",
        `capture-navigator--${view}`,
      )}
    >
      {selected && (
        <SegmentedControl
          aria-label={label}
          value={view}
          onChange={setView}
          options={[
            { value: "map", label: "Map", controls: `${id}-map` },
            { value: "table", label: "Table", controls: `${id}-table` },
          ]}
          kind="tabs"
          size="sm"
        />
      )}
      <div
        id={`${id}-map`}
        role={selected ? "tabpanel" : undefined}
        aria-label="Layer map"
        className="capture-navigator__map"
      >
        {map}
      </div>
      <div
        id={`${id}-table`}
        role={selected ? "tabpanel" : undefined}
        aria-label="Layer table"
        className="capture-navigator__table"
      >
        {table}
      </div>
    </div>
  );
}
