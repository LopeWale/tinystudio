import { cx } from "../boardui/utils/cx";
import "./controls.css";
import {
  FilterMenu as SourceFilterMenu,
  type FilterMenuProps,
  type FilterValue,
} from "./filter-menu-clean";
import {
  SegmentedControl as SourceSegmentedControl,
  type SegmentedControlProps,
} from "./segmented-control-clean";

export type {
  FilterGroup,
  FilterOption,
  FilterValue,
} from "./filter-menu-clean";
export type { SegmentOption } from "./segmented-control-clean";
/** Selection belongs to the consuming feature. Only expansion/focus are local. */
export type ControlledFilterMenuProps = Omit<
  FilterMenuProps,
  "value" | "defaultValue" | "onChange"
> & {
  value: FilterValue;
  onChange: (value: FilterValue) => void;
};
export function FilterMenu(props: ControlledFilterMenuProps) {
  return <SourceFilterMenu {...props} />;
}
export type ControlledSegmentedControlProps<T extends string = string> = Omit<
  SegmentedControlProps<T>,
  "value" | "defaultValue" | "onChange" | "aria-label"
> & {
  value: T;
  onChange: (value: T) => void;
  "aria-label": string;
};
export function SegmentedControl<T extends string = string>(
  props: ControlledSegmentedControlProps<T>,
) {
  // TinyStudio adapts the source tab contrast and target size to its app surfaces.
  return (
    <SourceSegmentedControl
      {...props}
      className={cx(
        "studio-segmented-control",
        props.kind === "tabs" && "studio-tabs",
        props.className,
      )}
    />
  );
}
