import { useId, type ReactNode } from "react";
import { Input } from "../../components/boardui/base/input/input";
import { NumberField } from "../../components/studio/number-field";
import { Textarea } from "../../components/boardui/base/textarea/textarea";
import {
  Select,
  SelectItem,
} from "../../components/boardui/base/select/select";
import { Checkbox } from "../../components/boardui/base/checkbox/checkbox";
import { Button } from "../../components/studio/primitives";
import "../../components/studio/form-controls.css";
import "./dataset-editor.css";

export type DatasetLabel = { name: string; description: string };
export type DatasetEditFieldsProps = {
  mode: string;
  values: Record<string, unknown>;
  onChange: (key: string, value: string | number | boolean) => void;
  labels: DatasetLabel[];
  onLabelsChange: (labels: DatasetLabel[]) => void;
  disabled: boolean;
  snapshotPlaceholder: string;
};

function EditorSection({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section className="dataset-editor-section" aria-labelledby={id}>
      <div className="dataset-editor-section-heading">
        <h3 id={id} className="text-body-medium text-text-primary">
          {title}
        </h3>
        {hint && (
          <p className="text-body-2-regular text-text-tertiary">{hint}</p>
        )}
      </div>
      {children}
    </section>
  );
}

/** Presentation only: dataset state, save validation and requests stay with DatasetPage. */
export function DatasetEditFields({
  mode,
  values,
  onChange,
  labels,
  onLabelsChange,
  disabled,
  snapshotPlaceholder,
}: DatasetEditFieldsProps) {
  const text = (key: string) => String(values[key] ?? "");
  const numeric = (
    key: string,
    label: string,
    min: number,
    max: number,
    step = 1,
  ) => (
    <NumberField
      label={label}
      value={text(key)}
      onChange={(next) => onChange(key, next)}
      disabled={disabled}
      min={min}
      max={max}
      step={step}
    />
  );
  const multiline = (key: string, label: string, rows = 4, hint?: string) => (
    <Textarea
      label={label}
      value={text(key)}
      onChange={(next) => onChange(key, next)}
      isDisabled={disabled}
      rows={rows}
      hint={hint}
      fieldClassName="studio-form-control"
    />
  );

  if (mode === "create-snapshot")
    return (
      <div className="dataset-editor">
        <EditorSection
          title="Snapshot details"
          hint="Freeze the current training and validation buckets for reuse."
        >
          <Input
            label="New snapshot name"
            value={text("name")}
            onChange={(next) => onChange("name", next)}
            isDisabled={disabled}
            placeholder={snapshotPlaceholder}
            fieldClassName="studio-form-control"
          />
        </EditorSection>
      </div>
    );

  if (mode === "split")
    return (
      <div className="dataset-editor">
        <EditorSection
          title="Bucket assignment"
          hint="Assign uncategorized rows; existing assignments are kept."
        >
          <div className="dataset-editor-short-field">
            {numeric("train_pct", "Training percentage", 1, 99)}
          </div>
          <Checkbox
            isSelected={!!values.synthetic_in_validation}
            onChange={(next) => onChange("synthetic_in_validation", next)}
            isDisabled={disabled}
            size="sm"
          >
            Allow synthetic data in validation
          </Checkbox>
        </EditorSection>
      </div>
    );

  if (mode === "enrich")
    return (
      <div className="dataset-editor">
        <EditorSection title="Generation settings">
          <div className="dataset-editor-pair">
            {numeric("count", "New datapoints", 1, 2000)}
            {numeric("temperature", "Temperature", 0, 1.5, 0.1)}
          </div>
        </EditorSection>
        <EditorSection
          title="Generation guidance"
          hint="Describe what to generate and what to leave out."
        >
          <div className="dataset-editor-pair">
            {multiline("instructions", "Instructions")}
            {multiline("avoid", "Avoid")}
          </div>
        </EditorSection>
      </div>
    );

  if (mode === "add")
    return (
      <div className="dataset-editor">
        <EditorSection
          title="Conversation"
          hint="Write the user message and its expected assistant response."
        >
          <div className="dataset-editor-pair">
            {multiline("user", "user", 6)}
            {multiline("assistant", "assistant", 6)}
          </div>
          <details className="dataset-editor-disclosure">
            <summary className="text-body-2-medium text-text-secondary">
              System context{" "}
              <span className="text-caption-1-regular text-text-tertiary">
                Optional
              </span>
            </summary>
            {multiline("system", "system", 3)}
          </details>
        </EditorSection>
        <EditorSection title="Placement">
          <div className="dataset-editor-bucket dataset-editor-short-field">
            <span className="text-body-medium text-text-primary">Bucket</span>
            <Select
              aria-label="Bucket"
              selectedKey={text("bucket")}
              onSelectionChange={(next) =>
                next !== null && onChange("bucket", String(next))
              }
              isDisabled={disabled}
              className="dataset-editor-short-field"
              triggerClassName="studio-form-control"
              size="sm"
            >
              {["uncategorized", "training", "validation"].map((bucket) => (
                <SelectItem key={bucket} id={bucket}>
                  {bucket}
                </SelectItem>
              ))}
            </Select>
          </div>
        </EditorSection>
      </div>
    );

  return (
    <div className="dataset-editor">
      <EditorSection
        title="Label vocabulary"
        hint="Helper auto-labeling assigns one label per datapoint from this vocabulary."
      >
        <div className="dataset-editor-labels">
          {labels.length === 0 && (
            <p className="dataset-editor-empty text-body-2-regular text-text-secondary">
              No labels yet. Add a label to define your vocabulary.
            </p>
          )}
          {labels.map((label, index) => (
            <div className="dataset-editor-label-row" key={index}>
              <Input
                label={`Label ${index + 1} name`}
                value={label.name}
                onChange={(name) =>
                  onLabelsChange(
                    labels.map((item, i) =>
                      i === index ? { ...item, name } : item,
                    ),
                  )
                }
                isDisabled={disabled}
                fieldClassName="studio-form-control"
              />
              <Input
                label={`Label ${index + 1} description`}
                value={label.description}
                onChange={(description) =>
                  onLabelsChange(
                    labels.map((item, i) =>
                      i === index ? { ...item, description } : item,
                    ),
                  )
                }
                isDisabled={disabled}
                fieldClassName="studio-form-control"
              />
              <Button
                variant="ghost"
                size="sm"
                disabled={disabled}
                onClick={() =>
                  onLabelsChange(labels.filter((_, i) => i !== index))
                }
                aria-label={`Remove label ${index + 1}`}
              >
                Remove
              </Button>
            </div>
          ))}
        </div>
        <div>
          <Button
            size="sm"
            disabled={disabled}
            onClick={() =>
              onLabelsChange([...labels, { name: "", description: "" }])
            }
          >
            Add label
          </Button>
        </div>
      </EditorSection>
    </div>
  );
}
