import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DatasetEditFields,
  type DatasetEditFieldsProps,
} from "./dataset-editor";

afterEach(cleanup);
const props: DatasetEditFieldsProps = {
  mode: "add",
  values: {
    user: "Question",
    assistant: "Answer",
    system: "Context",
    bucket: "training",
  },
  onChange: vi.fn(),
  labels: [],
  onLabelsChange: vi.fn(),
  disabled: false,
  snapshotPlaceholder: "sample-v2",
};

describe("DatasetEditFields controlled form contract", () => {
  it("retains conversation edits when optional system context closes", () => {
    function Controlled() {
      const [values, setValues] = useState(props.values);
      return (
        <DatasetEditFields
          {...props}
          values={values}
          onChange={(key, value) =>
            setValues((previous) => ({ ...previous, [key]: value }))
          }
        />
      );
    }
    render(<Controlled />);
    fireEvent.change(screen.getByRole("textbox", { name: "user" }), {
      target: { value: "Updated question" },
    });
    const disclosure = screen.getByText("System context").closest("details")!;
    fireEvent.click(screen.getByText("System context"));
    fireEvent.change(screen.getByRole("textbox", { name: "system" }), {
      target: { value: "Updated context" },
    });
    fireEvent.click(screen.getByText("System context"));
    expect(disclosure).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText("System context"));
    expect(screen.getByRole("textbox", { name: "system" })).toHaveValue(
      "Updated context",
    );
    expect(screen.getByRole("textbox", { name: "user" })).toHaveValue(
      "Updated question",
    );
    expect(screen.getByRole("button", { name: /Bucket/ })).toHaveTextContent(
      "training",
    );
  });

  it("keeps numeric bounds and sends numbers while allowing an empty draft", () => {
    const onChange = vi.fn();
    render(
      <DatasetEditFields
        {...props}
        mode="enrich"
        values={{ count: 25, temperature: 0.3 }}
        onChange={onChange}
      />,
    );
    const count = screen.getByRole("spinbutton", { name: "New datapoints" });
    expect(count).toHaveAttribute("min", "1");
    expect(count).toHaveAttribute("max", "2000");
    const temperature = screen.getByRole("spinbutton", { name: "Temperature" });
    expect(temperature).toHaveAttribute("min", "0");
    expect(temperature).toHaveAttribute("max", "1.5");
    expect(temperature).toHaveAttribute("step", "0.1");
    fireEvent.change(count, { target: { value: "30" } });
    expect(onChange).toHaveBeenLastCalledWith("count", 30);
    fireEvent.change(count, { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith("count", "");
  });

  it.each([
    "create-snapshot",
    "split",
    "enrich",
    "add",
    "labels",
    "auto-label",
  ])("disables every editable control while %s is busy", (mode) => {
    render(
      <DatasetEditFields
        {...props}
        mode={mode}
        disabled
        labels={[{ name: "A", description: "A label" }]}
      />,
    );
    for (const field of document.querySelectorAll(
      "input, textarea, button, select",
    ))
      expect(field).toBeDisabled();
  });

  it("preserves split bounds and the synthetic validation option", () => {
    const onChange = vi.fn();
    render(
      <DatasetEditFields
        {...props}
        mode="split"
        values={{ train_pct: 80, synthetic_in_validation: false }}
        onChange={onChange}
      />,
    );
    const percentage = screen.getByRole("spinbutton", {
      name: "Training percentage",
    });
    expect(percentage).toHaveValue(80);
    expect(percentage).toHaveAttribute("min", "1");
    expect(percentage).toHaveAttribute("max", "99");
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "Allow synthetic data in validation",
      }),
    );
    expect(onChange).toHaveBeenCalledWith("synthetic_in_validation", true);
  });

  it("keeps snapshot naming controlled and exposes the suggested name", () => {
    const onChange = vi.fn();
    render(
      <DatasetEditFields
        {...props}
        mode="create-snapshot"
        values={{ name: "" }}
        onChange={onChange}
      />,
    );
    const name = screen.getByRole("textbox", { name: "New snapshot name" });
    expect(name).toHaveAttribute("placeholder", "sample-v2");
    fireEvent.change(name, { target: { value: "release" } });
    expect(onChange).toHaveBeenCalledWith("name", "release");
  });

  it("provides label recovery and keeps vocabulary mutations with the parent", () => {
    const onLabelsChange = vi.fn();
    const view = render(
      <DatasetEditFields
        {...props}
        mode="labels"
        onLabelsChange={onLabelsChange}
      />,
    );
    expect(screen.getByText(/No labels yet/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Add label" }));
    expect(onLabelsChange).toHaveBeenLastCalledWith([
      { name: "", description: "" },
    ]);
    view.rerender(
      <DatasetEditFields
        {...props}
        mode="labels"
        labels={[{ name: "A", description: "Existing" }]}
        onLabelsChange={onLabelsChange}
      />,
    );
    fireEvent.change(
      screen.getByRole("textbox", { name: "Label 1 description" }),
      { target: { value: "Changed" } },
    );
    expect(onLabelsChange).toHaveBeenLastCalledWith([
      { name: "A", description: "Changed" },
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Remove label 1" }));
    expect(onLabelsChange).toHaveBeenLastCalledWith([]);
  });
});
