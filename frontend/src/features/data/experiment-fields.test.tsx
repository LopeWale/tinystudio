import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ExperimentFields, type ExperimentDraft } from "./experiment-fields";

afterEach(cleanup);
const draft: ExperimentDraft = {
  base_model: "small/model",
  training_type: "lora",
  refinetune_from: null,
  batch_size: 8,
  learning_rate: 0.0002,
  epochs: 3,
  max_length: 512,
  gradient_accumulation_steps: 2,
  lora_r: 16,
  lora_alpha: 32,
  checkpoint_every_steps: 0,
  keep_checkpoints: 0,
  assistant_only_loss: false,
};
const sources = {
  catalog: [{ id: "small/model" }],
  models: [{ kind: "fine-tuned", alias: "ft-existing-1" }],
};
function Controlled() {
  const [value, onChange] = useState(draft);
  return <ExperimentFields {...sources} value={value} onChange={onChange} />;
}

describe("Experiment configuration fields", () => {
  it("keeps checkpoint drafts through disclosure collapse and reopening", async () => {
    const user = userEvent.setup();
    render(<Controlled />);
    const toggle = screen.getByRole("button", { name: "Checkpoints" });
    await user.click(toggle);
    const field = screen.getByRole("spinbutton", {
      name: "Checkpoint every N steps (0 = off)",
    });
    fireEvent.change(field, { target: { value: "42" } });
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(
      screen.getByRole("spinbutton", {
        name: "Checkpoint every N steps (0 = off)",
      }),
    ).toHaveValue(42);
  });
  it("disables every editable field and disclosure during submission", () => {
    const onChange = vi.fn();
    const view = render(
      <ExperimentFields
        {...sources}
        value={draft}
        onChange={onChange}
        disabled
      />,
    );
    for (const control of view.container.querySelectorAll("input, button"))
      expect(control).toBeDisabled();
    expect(screen.getByRole("button", { name: "Checkpoints" })).toBeDisabled();
    expect(onChange).not.toHaveBeenCalled();
  });
  it("gives each base model its own matching suggestion list", () => {
    render(
      <>
        <ExperimentFields {...sources} value={draft} onChange={vi.fn()} />
        <ExperimentFields {...sources} value={draft} onChange={vi.fn()} />
      </>,
    );
    const inputs = screen.getAllByRole("combobox", { name: "Base model" });
    const ids = inputs.map((input) => input.getAttribute("list"));
    expect(new Set(ids).size).toBe(2);
    for (const id of ids) {
      const list = document.getElementById(id!);
      expect(list?.tagName).toBe("DATALIST");
      expect(list?.querySelector("option")).toHaveAttribute(
        "value",
        "small/model",
      );
    }
  });
  it("emits numeric zero and blank distinctly without dropping other draft values", async () => {
    const onChange = vi.fn();
    render(<ExperimentFields {...sources} value={draft} onChange={onChange} />);
    const rate = screen.getByRole("spinbutton", { name: "Learning rate" });
    expect(rate).toHaveAttribute("step", "any");
    expect(
      screen.getByRole("spinbutton", { name: "Number of epochs" }),
    ).toHaveAttribute("step", "any");
    expect(
      screen.getByRole("spinbutton", { name: "Batch size" }),
    ).toHaveAttribute("step", "1");
    fireEvent.change(rate, { target: { value: "0.0000005" } });
    expect(onChange).toHaveBeenLastCalledWith({
      ...draft,
      learning_rate: 0.0000005,
    });
    fireEvent.change(rate, { target: { value: "0" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...draft, learning_rate: 0 });
    fireEvent.change(rate, { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...draft, learning_rate: "" });
    fireEvent.change(screen.getByRole("combobox", { name: "Base model" }), {
      target: { value: "custom/model" },
    });
    expect(onChange).toHaveBeenLastCalledWith({
      ...draft,
      base_model: "custom/model",
    });
  });
  it.each([
    ["dpo", 0.000005],
    ["full", 0.00005],
    ["qlora", 0.0002],
    ["lora", 0.0002],
  ])(
    "resets %s learning rate to the existing training contract",
    async (type, rate) => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      render(
        <ExperimentFields
          {...sources}
          value={{
            ...draft,
            training_type: type === "lora" ? "full" : "lora",
            learning_rate: 0.1,
          }}
          onChange={onChange}
        />,
      );
      await user.click(screen.getByRole("button", { name: /Training type/ }));
      await user.click(screen.getByRole("option", { name: String(type) }));
      expect(onChange).toHaveBeenCalledWith({
        ...draft,
        training_type: type,
        learning_rate: rate,
      });
    },
  );
  it("preserves adapter values when switching through full training and exposes DPO adapters", async () => {
    const user = userEvent.setup();
    render(<Controlled />);
    fireEvent.change(screen.getByRole("spinbutton", { name: "LoRA rank" }), {
      target: { value: "48" },
    });
    await user.click(screen.getByRole("button", { name: /Training type/ }));
    await user.click(screen.getByRole("option", { name: "full" }));
    expect(screen.getByRole("spinbutton", { name: "LoRA rank" })).toHaveValue(
      48,
    );
    expect(screen.getByText("Retained adapter settings")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Training type/ }));
    await user.click(screen.getByRole("option", { name: "dpo" }));
    expect(screen.getByRole("spinbutton", { name: "LoRA rank" })).toHaveValue(
      48,
    );
    expect(
      screen.getByText("DPO preference training uses LoRA adapters."),
    ).toBeInTheDocument();
  });
});
