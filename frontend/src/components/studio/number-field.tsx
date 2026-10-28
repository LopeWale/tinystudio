import { InputBase, TextField } from "../boardui/base/input/input";
import { Label } from "../boardui/base/input/label";
import "./form-controls.css";

export type NumberFieldProps = {
  label: string;
  value: string | number;
  onChange: (value: string | number) => void;
  min?: number;
  max?: number;
  step?: number | "any";
  disabled?: boolean;
};

/** Keeps an empty draft editable while emitting numeric values for populated fields. */
export function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step,
  disabled = false,
}: NumberFieldProps) {
  return (
    <TextField
      value={String(value)}
      onChange={(next) => onChange(next === "" ? "" : Number(next))}
      isDisabled={disabled}
    >
      <Label>{label}</Label>
      <InputBase
        type="number"
        min={min}
        max={max}
        step={step}
        fieldClassName="studio-form-control"
      />
    </TextField>
  );
}
