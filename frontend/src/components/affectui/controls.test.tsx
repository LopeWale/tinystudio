import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SegmentedControl } from "./controls";

afterEach(cleanup);
const options = [
  { value: "overview", label: "Overview", controls: "overview-panel" },
  {
    value: "locked",
    label: "Locked",
    disabled: true,
    controls: "locked-panel",
  },
  { value: "history", label: "History", controls: "history-panel" },
];

describe("TinyStudio tab adapter", () => {
  it.each(["underline", "tray", "buttons"] as const)(
    "preserves tab semantics and keyboard navigation for %s",
    (variant) => {
      function Tabs() {
        const [value, setValue] = useState("overview");
        return (
          <SegmentedControl
            kind="tabs"
            variant={variant}
            size="sm"
            aria-label="Workspace"
            options={options}
            value={value}
            onChange={setValue}
          />
        );
      }
      render(<Tabs />);
      const overview = screen.getByRole("tab", { name: "Overview" });
      const history = screen.getByRole("tab", { name: "History" });
      expect(overview).toHaveAttribute("aria-selected", "true");
      expect(overview).toHaveAttribute("aria-controls", "overview-panel");
      expect(screen.getByRole("tab", { name: "Locked" })).toBeDisabled();
      fireEvent.keyDown(overview, { key: "ArrowRight" });
      expect(history).toHaveAttribute("aria-selected", "true");
      expect(history).toHaveFocus();
      expect(overview).toHaveAttribute("aria-selected", "false");
      fireEvent.keyDown(history, { key: "Home" });
      expect(overview).toHaveAttribute("aria-selected", "true");
      fireEvent.keyDown(overview, { key: "End" });
      expect(history).toHaveAttribute("aria-selected", "true");
      fireEvent.keyDown(history, { key: "ArrowLeft" });
      expect(overview).toHaveAttribute("aria-selected", "true");
    },
  );

  it("leaves selection ownership with the controller and prevents disabled activation", () => {
    const onChange = vi.fn();
    const view = render(
      <SegmentedControl
        kind="tabs"
        variant="tray"
        aria-label="Workspace"
        options={options}
        value="overview"
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "History" }));
    expect(onChange).toHaveBeenCalledExactlyOnceWith("history");
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    view.rerender(
      <SegmentedControl
        kind="tabs"
        variant="tray"
        aria-label="Workspace"
        options={options}
        value="history"
        onChange={onChange}
        disabled
      />,
    );
    const overview = screen.getByRole("tab", { name: "Overview" });
    fireEvent.click(overview);
    fireEvent.keyDown(overview, { key: "Home" });
    expect(onChange).toHaveBeenCalledOnce();
    for (const tab of screen.getAllByRole("tab")) expect(tab).toBeDisabled();
    expect(screen.getByRole("tab", { name: "History" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});
