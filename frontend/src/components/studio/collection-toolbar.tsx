import { useRef, type MouseEventHandler, type RefObject } from "react";
import { RiSearchLine } from "@remixicon/react";
import { Button } from "./primitives";
import { Input } from "../boardui/base/input/input";
import "./collection-toolbar.css";

/** Contextual actions shared by searchable collections and filtered record tables. */
export function CollectionSelectionActions({
  selectedCount,
  onCompare,
  onClearSelection,
  returnFocus,
}: {
  selectedCount: number;
  onCompare?: MouseEventHandler<HTMLButtonElement>;
  onClearSelection?: () => void;
  returnFocus?: RefObject<HTMLElement | null>;
}) {
  if (selectedCount <= 0) return null;

  return (
    <div
      className="collection-toolbar__selection"
      role="group"
      aria-label="Selection actions"
    >
      <span role="status">{selectedCount} selected</span>
      {onCompare && (
        <Button size="sm" disabled={selectedCount < 2} onClick={onCompare}>
          Compare selected
        </Button>
      )}
      {onClearSelection && (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            onClearSelection();
            returnFocus?.current?.focus();
          }}
        >
          Clear selection
        </Button>
      )}
    </div>
  );
}

/** Controlled collection actions. The feature owns query and cross-filter selection. */
export function CollectionToolbar({
  label,
  query,
  onQueryChange,
  selectedCount = 0,
  onCompare,
  onClearSelection,
  searchRef,
}: {
  label: string;
  query: string;
  onQueryChange: (query: string) => void;
  selectedCount?: number;
  onCompare?: MouseEventHandler<HTMLButtonElement>;
  onClearSelection?: () => void;
  searchRef?: RefObject<HTMLInputElement | null>;
}) {
  const localSearch = useRef<HTMLInputElement>(null);
  const search = searchRef ?? localSearch;
  return (
    <div className="collection-toolbar">
      <Input
        ref={search}
        size="small"
        type="search"
        className="collection-toolbar__search"
        fieldClassName="collection-toolbar__search-field"
        leadingIcon={RiSearchLine}
        aria-label={label}
        placeholder={label}
        value={query}
        onChange={onQueryChange}
      />
      <CollectionSelectionActions
        selectedCount={selectedCount}
        onCompare={onCompare}
        onClearSelection={onClearSelection}
        returnFocus={search}
      />
    </div>
  );
}
