import { useEffect, useRef, useState } from "react";
import { Input } from "../../components/boardui/base/input/input";
import { Textarea } from "../../components/boardui/base/textarea/textarea";
import { Button } from "../../components/studio/primitives";
import { WorkspaceDisclosure } from "../../components/studio/workspace";
import { LabChoice } from "./inputs";
import type { ExperimentDefinition } from "./definition";

type Options = Record<string, unknown>;
const stageDefaults: Options = {
  factor: 1,
  offset: 0,
  window: 5,
  dt: 0.1,
  value: 0,
  threshold: 0,
};
const object = (value: unknown): Options =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Options)
    : {};
const choices = (values: string[]) =>
  values.map((id) => ({ id, name: id.replaceAll("_", " ") }));
const stageKinds = [
  "scale",
  "offset",
  "scale_offset",
  "moving_average",
  "filter",
  "impute",
  "standardize",
  "features",
  "resample",
  "representation",
];
const codecKinds = [
  "numeric_text",
  "raw_bytes",
  "decoded_protocol",
  "engineering_state",
  "hybrid",
];
type Validity = (key: string, valid: boolean) => void;

/** Preserve incomplete edits locally while explicitly blocking definition actions. */
function DraftField({
  label,
  value,
  onChange,
  report,
  fieldKey,
  kind = "number",
  optional = false,
  minimum,
  integer = false,
}: {
  label: string;
  value: unknown;
  onChange: (value: unknown) => void;
  report: Validity;
  fieldKey: string;
  kind?: "number" | "object" | "array" | "list";
  optional?: boolean;
  minimum?: number;
  integer?: boolean;
}) {
  const serialized =
    kind === "number"
      ? String(value ?? "")
      : kind === "list"
        ? (Array.isArray(value) ? value : [value]).join(", ")
        : JSON.stringify(value ?? (kind === "array" ? [] : {}), null, 2);
  const [draft, setDraft] = useState(serialized);
  const [error, setError] = useState("");
  const committed = useRef(serialized);
  useEffect(() => {
    if (serialized !== committed.current) {
      committed.current = serialized;
      setDraft(serialized);
      setError("");
      report(fieldKey, true);
    }
  }, [serialized, fieldKey, report]);
  useEffect(() => () => report(fieldKey, true), [fieldKey, report]);
  const edit = (text: string) => {
    setDraft(text);
    try {
      let parsed: unknown;
      if (kind === "number") {
        if (!text.trim() && optional) parsed = undefined;
        else {
          parsed = text.trim() ? Number(text) : NaN;
          if (
            !Number.isFinite(parsed) ||
            (minimum !== undefined && Number(parsed) < minimum) ||
            (integer && !Number.isInteger(parsed))
          )
            throw new Error(
              "Enter a finite" +
                (integer ? " integer" : " number") +
                (minimum !== undefined ? ` ≥ ${minimum}` : "") +
                ".",
            );
        }
      } else if (kind === "list") {
        parsed = text
          .split(",")
          .map((field) => field.trim())
          .filter(Boolean);
      } else {
        parsed = JSON.parse(text);
        if (
          kind === "array"
            ? !Array.isArray(parsed)
            : !parsed || typeof parsed !== "object" || Array.isArray(parsed)
        )
          throw new Error(`Enter a JSON ${kind}.`);
      }
      setError("");
      report(fieldKey, true);
      committed.current =
        kind === "number"
          ? String(parsed ?? "")
          : kind === "list"
            ? (parsed as string[]).join(", ")
            : JSON.stringify(parsed, null, 2);
      onChange(parsed);
    } catch (e) {
      setError((e as Error).message);
      report(fieldKey, false);
    }
  };
  return (
    <div>
      {kind === "number" || kind === "list" ? (
        <Input
          label={label}
          value={draft}
          onChange={edit}
          inputMode={kind === "number" ? "decimal" : "text"}
          isInvalid={!!error}
        />
      ) : (
        <Textarea
          label={label}
          value={draft}
          onChange={edit}
          rows={5}
          isInvalid={!!error}
        />
      )}
      {error && (
        <p role="alert">
          {label}: {error}
        </p>
      )}
    </div>
  );
}

export function DataPreparation({
  definition,
  onChange,
  onValidityChange,
}: {
  definition: ExperimentDefinition;
  onChange: (patch: Partial<ExperimentDefinition>) => void;
  onValidityChange?: (valid: boolean) => void;
}) {
  const p = definition.parameters;
  const schema = object(p.schema),
    cleaning = object(p.cleaning),
    policy = object(p.partition_policy);
  const dataset = p.partition_policy != null && policy.enabled !== false;
  const [invalid, setInvalid] = useState<Set<string>>(() => new Set());
  const pipelineSignature = JSON.stringify(definition.pipeline);
  const [storedEditors, setStoredEditors] = useState(() => ({
    signature: pipelineSignature,
    ids: definition.pipeline.map((_, index) => index),
    nextId: definition.pipeline.length,
  }));
  // Parent patches serialize the definition, so object references cannot identify
  // stages. Internal transitions carry editor IDs alongside the portable rows.
  // A different external pipeline replaces its editors and discards their drafts;
  // DraftField cleanup removes only those retired editors' validation entries.
  let stageEditors = storedEditors;
  if (storedEditors.signature !== pipelineSignature) {
    stageEditors = {
      signature: pipelineSignature,
      ids: definition.pipeline.map((_, index) => storedEditors.nextId + index),
      nextId: storedEditors.nextId + definition.pipeline.length,
    };
    setStoredEditors(stageEditors);
  }
  const changePipeline = (
    pipeline: unknown[],
    ids = stageEditors.ids,
    nextId = stageEditors.nextId,
  ) => {
    setStoredEditors({ signature: JSON.stringify(pipeline), ids, nextId });
    onChange({ pipeline });
  };
  const validityCallback = useRef(onValidityChange);
  validityCallback.current = onValidityChange;
  const [report] = useState<Validity>(
    () => (key: string, valid: boolean) =>
      setInvalid((current) => {
        if (current.has(key) === !valid) return current;
        const next = new Set(current);
        if (valid) next.delete(key);
        else next.add(key);
        return next;
      }),
  );
  const fractionError =
    dataset &&
    (!(Number(policy.train ?? 0.6) > 0) ||
      !(Number(policy.validation ?? 0.2) > 0) ||
      Number(policy.train ?? 0.6) + Number(policy.validation ?? 0.2) >= 1);
  useEffect(() => {
    validityCallback.current?.(!invalid.size && !fractionError);
  }, [invalid, fractionError]);
  useEffect(() => () => validityCallback.current?.(true), []);
  const patch = (key: string, value: unknown) => {
    const next = { ...p };
    if (value === undefined) delete next[key];
    else next[key] = value;
    onChange({ parameters: next });
  };
  const nested = (key: string, field: string, value: unknown) => {
    const next = { ...object(p[key]) };
    if (value === undefined || value === "") delete next[field];
    else next[field] = value;
    patch(key, next);
  };
  const text = (key: string, label: string) => (
    <Input
      key={key}
      label={label}
      value={String(p[key] ?? "")}
      onChange={(value) => patch(key, value || undefined)}
    />
  );
  const numeric = (
    key: string,
    label: string,
    value: unknown,
    change: (value: unknown) => void,
    extras: { optional?: boolean; minimum?: number; integer?: boolean } = {},
  ) => (
    <DraftField
      key={key}
      fieldKey={key}
      label={label}
      value={value}
      onChange={change}
      report={report}
      {...extras}
    />
  );
  const json = (
    key: string,
    label: string,
    value: unknown,
    change: (value: unknown) => void,
    kind: "array" | "object" = "object",
  ) => (
    <DraftField
      key={key}
      fieldKey={key}
      label={label}
      value={value}
      onChange={change}
      report={report}
      kind={kind}
    />
  );
  const updateStage = (index: number, stage: Options) =>
    changePipeline(
      definition.pipeline.map((row, i) => (i === index ? stage : row)),
    );
  const representations = Array.isArray(p.representations)
    ? p.representations
    : [];
  const updateCodec = (index: number, value: Options) =>
    patch(
      "representations",
      representations.map((row, i) => (i === index ? value : row)),
    );
  return (
    <section aria-label="Data preparation" className="flex flex-col gap-4">
      <div>
        <h3>Recorded data preparation</h3>
        <p>
          Prepare retained observations first. Dataset construction is optional
          and never assigns a split until explicitly requested. Complete source
          partition labels are validated by the engine.
        </p>
      </div>
      <WorkspaceDisclosure
        title="Source field mapping"
        defaultOpen
        summary="Map source columns to normalized observations"
      >
        <div className="lab-fields">
          {[
            "time",
            "value",
            "episode_id",
            "available_at",
            "units",
            "observed",
            "quality",
          ].map((key) => (
            <Input
              key={key}
              label={`${key.replaceAll("_", " ")} field`}
              value={String(schema[key] ?? key)}
              onChange={(value) => nested("schema", key, value)}
            />
          ))}
          <LabChoice
            label="Source format"
            value={String(p.format ?? "auto")}
            options={choices(["auto", "jsonl", "json", "csv", "parquet"])}
            onChange={(value) =>
              patch("format", value === "auto" ? undefined : value)
            }
          />
          {text("units", "Fallback value units")}
          {numeric(
            "sampling_interval",
            "Sampling interval (only for missing times)",
            p.sampling_interval,
            (value) => patch("sampling_interval", value),
            { optional: true, minimum: 1e-12 },
          )}
        </div>
        <p>
          Missing availability defaults to event time and is marked assumed.
          Missing event time requires a sampling interval.
        </p>
      </WorkspaceDisclosure>
      <WorkspaceDisclosure
        title="Clock coordinates"
        summary="Declare coordinates; metadata is not measured calibration"
      >
        <div className="lab-fields">
          {[
            "clock_domain",
            "clock_units",
            "clock_origin",
            "clock_session",
            "clock_reset_id",
            "capture_id",
          ].map((key) => text(key, key.replaceAll("_", " ")))}
        </div>
        <p>
          Temporal datasets require comparable qualified coordinates: domain,
          units, origin and session. Declare reset identity when a clock resets.
          Source column mappings and affine alignment remain explicit advanced
          settings.
        </p>
        {json("schema", "Advanced field mapping JSON", p.schema, (value) =>
          patch("schema", value),
        )}
        {json(
          "clocks",
          "Affine clock alignments JSON",
          definition.clocks,
          (value) => onChange({ clocks: object(value) }),
        )}
      </WorkspaceDisclosure>
      <WorkspaceDisclosure
        title="Cleaning and replay"
        summary="Invalid values, duplicate events and causal order"
      >
        <div className="lab-fields">
          <LabChoice
            label="Invalid numeric values"
            value={String(cleaning.invalid_values ?? "error")}
            options={choices(["error", "missing"])}
            onChange={(value) => nested("cleaning", "invalid_values", value)}
          />
          <LabChoice
            label="Duplicate event times"
            value={String(cleaning.duplicates ?? "error")}
            options={choices(["error", "first", "last"])}
            onChange={(value) => nested("cleaning", "duplicates", value)}
          />
          <LabChoice
            label="Processing mode"
            value={definition.mode}
            options={[
              { id: "offline", name: "Offline preparation" },
              { id: "online", name: "Online availability replay" },
            ]}
            onChange={(mode) => onChange({ mode })}
          />
        </div>
        <p>
          Offline cleaning orders each scalar series by event time and source
          identity. Online replay orders arrivals by availability, then event
          time and source identity; it requires duplicate rejection and excludes
          batch fits and resampling.
        </p>
        {definition.mode === "online" &&
          json(
            "online_policy",
            "Online availability assumption JSON",
            p.online_policy,
            (value) => patch("online_policy", value),
          )}
      </WorkspaceDisclosure>
      <WorkspaceDisclosure
        title="Ordered transformations"
        summary={`${definition.pipeline.length} stages · applied in displayed order`}
      >
        <p>
          Training fits (standardize, mean or median imputation) require
          validated training partitions. All other operations retain their
          authored parameters.
        </p>
        {definition.pipeline.map((raw, index) => {
          const stage = object(raw),
            options = object(stage.parameters),
            kind = String(stage.kind ?? "");
          const option = (key: string, value: unknown) =>
            updateStage(index, {
              ...stage,
              parameters: { ...options, [key]: value },
            });
          return (
            <section
              key={stageEditors.ids[index]}
              aria-label={`Transformation ${index + 1}`}
              className="flex flex-col gap-2"
            >
              <div className="lab-actions">
                <strong>Stage {index + 1}</strong>
                <Button
                  disabled={index === 0}
                  onClick={() => {
                    const next = [...definition.pipeline];
                    [next[index - 1], next[index]] = [
                      next[index],
                      next[index - 1],
                    ];
                    const ids = [...stageEditors.ids];
                    [ids[index - 1], ids[index]] = [ids[index], ids[index - 1]];
                    changePipeline(next, ids);
                  }}
                >
                  Move stage {index + 1} up
                </Button>
                <Button
                  disabled={index === definition.pipeline.length - 1}
                  onClick={() => {
                    const next = [...definition.pipeline];
                    [next[index + 1], next[index]] = [
                      next[index],
                      next[index + 1],
                    ];
                    const ids = [...stageEditors.ids];
                    [ids[index + 1], ids[index]] = [ids[index], ids[index + 1]];
                    changePipeline(next, ids);
                  }}
                >
                  Move stage {index + 1} down
                </Button>
                <Button
                  onClick={() =>
                    changePipeline(
                      definition.pipeline.filter((_, i) => i !== index),
                      stageEditors.ids.filter((_, i) => i !== index),
                    )
                  }
                >
                  Remove stage {index + 1}
                </Button>
              </div>
              <LabChoice
                label={`Stage ${index + 1} operation`}
                value={kind}
                options={choices(stageKinds)}
                onChange={(value) =>
                  updateStage(index, { ...stage, kind: value })
                }
              />
              <div className="lab-fields">
                {["scale", "offset", "scale_offset"].includes(kind) && (
                  <>
                    <Input
                      label={`Stage ${index + 1} source units`}
                      value={String(options.source_units ?? "")}
                      onChange={(value) => option("source_units", value)}
                    />
                    <Input
                      label={`Stage ${index + 1} output units`}
                      value={String(options.units ?? "")}
                      onChange={(value) => {
                        const next = { ...options };
                        if (value) next.units = value;
                        else delete next.units;
                        updateStage(index, { ...stage, parameters: next });
                      }}
                    />
                  </>
                )}
                {[
                  ...(["scale", "scale_offset"].includes(kind)
                    ? ["factor"]
                    : []),
                  ...(["offset", "scale_offset"].includes(kind)
                    ? ["offset"]
                    : []),
                  ...(["moving_average", "filter", "features"].includes(kind)
                    ? ["window"]
                    : []),
                  ...(kind === "resample" ? ["dt"] : []),
                  ...(kind === "representation" &&
                  options.format === "state_tokens"
                    ? ["threshold"]
                    : []),
                  ...(kind === "impute" && options.method === "constant"
                    ? ["value"]
                    : []),
                ].map((key) =>
                  numeric(
                    `stage-${stageEditors.ids[index]}-${key}`,
                    `Stage ${index + 1} ${key}`,
                    options[key] ??
                      (key === "factor"
                        ? (options.scale ?? 1)
                        : stageDefaults[key]),
                    (value) => option(key, value),
                    key === "window"
                      ? { minimum: 1, integer: true }
                      : key === "dt"
                        ? { minimum: 1e-9 }
                        : {},
                  ),
                )}
                {kind === "impute" && (
                  <LabChoice
                    label={`Stage ${index + 1} imputation method`}
                    value={String(options.method ?? "forward_fill")}
                    options={choices([
                      "forward_fill",
                      "constant",
                      "mean",
                      "median",
                    ])}
                    onChange={(value) => option("method", value)}
                  />
                )}
                {kind === "resample" && (
                  <LabChoice
                    label={`Stage ${index + 1} resampling method`}
                    value={String(options.method ?? "forward_fill")}
                    options={choices(["forward_fill", "linear"])}
                    onChange={(value) => option("method", value)}
                  />
                )}
                {kind === "resample" && (
                  <LabChoice
                    label={`Stage ${index + 1} future-aware interpolation`}
                    value={String(options.future_aware ?? false)}
                    options={[
                      { id: "false", name: "Causal only" },
                      {
                        id: "true",
                        name: "Allow future-aware offline interpolation",
                      },
                    ]}
                    onChange={(value) =>
                      option("future_aware", value === "true")
                    }
                  />
                )}
                {kind === "representation" && (
                  <LabChoice
                    label={`Stage ${index + 1} representation format`}
                    value={String(options.format ?? "numeric")}
                    options={choices([
                      "numeric",
                      "text",
                      "bytes",
                      "state_tokens",
                      "chat",
                    ])}
                    onChange={(value) => option("format", value)}
                  />
                )}
              </div>
              <WorkspaceDisclosure
                title={`Stage ${index + 1} advanced options`}
              >
                {json(
                  `stage-${stageEditors.ids[index]}-options`,
                  `Stage ${index + 1} parameters JSON`,
                  options,
                  (value) =>
                    updateStage(index, { ...stage, parameters: value }),
                )}
              </WorkspaceDisclosure>
            </section>
          );
        })}
        <Button
          disabled={definition.pipeline.length >= 32 || !!invalid.size}
          onClick={() =>
            changePipeline(
              [
                ...definition.pipeline,
                { kind: "scale", version: "1", parameters: { factor: 1 } },
              ],
              [...stageEditors.ids, stageEditors.nextId],
              stageEditors.nextId + 1,
            )
          }
        >
          Add transformation
        </Button>
      </WorkspaceDisclosure>
      <WorkspaceDisclosure
        title="Dataset construction"
        summary={dataset ? "Explicit dataset requested" : "Preparation only"}
      >
        <LabChoice
          label="Output purpose"
          value={dataset ? "dataset" : "preparation"}
          options={[
            { id: "preparation", name: "Prepare evidence only" },
            {
              id: "dataset",
              name: "Construct train / validation / test dataset",
            },
          ]}
          onChange={(value) =>
            patch(
              "partition_policy",
              value === "dataset"
                ? {
                    ...policy,
                    enabled: true,
                    strategy: policy.strategy ?? "grouped",
                    group_by: policy.group_by ?? ["episode_id"],
                    train: policy.train ?? 0.6,
                    validation: policy.validation ?? 0.2,
                  }
                : { ...policy, enabled: false },
            )
          }
        />
        {dataset && (
          <>
            <div className="lab-fields">
              <LabChoice
                label="Partition strategy"
                value={String(policy.strategy ?? "temporal")}
                options={[
                  {
                    id: "grouped",
                    name: "Grouped · no cross-clock time claims",
                  },
                  {
                    id: "temporal",
                    name: "Temporal · qualified comparable clocks",
                  },
                ]}
                onChange={(value) => {
                  const next: Options = { ...policy, strategy: value };
                  if (value === "grouped") {
                    delete next.boundaries;
                    delete next.purge_seconds;
                    delete next.boundary_action;
                  }
                  patch("partition_policy", next);
                }}
              />
              <DraftField
                label="Isolation fields (comma separated)"
                fieldKey="group_by"
                kind="list"
                value={policy.group_by ?? ["episode_id"]}
                report={report}
                onChange={(value) =>
                  nested("partition_policy", "group_by", value)
                }
              />
              {numeric(
                "train-fraction",
                "Training fraction",
                policy.train ?? 0.6,
                (value) => nested("partition_policy", "train", value),
                { minimum: 1e-12 },
              )}
              {numeric(
                "validation-fraction",
                "Validation fraction",
                policy.validation ?? 0.2,
                (value) => nested("partition_policy", "validation", value),
                { minimum: 1e-12 },
              )}
              <p>
                Test fraction:{" "}
                {Number(
                  (
                    1 -
                    Number(policy.train ?? 0.6) -
                    Number(policy.validation ?? 0.2)
                  ).toFixed(6),
                )}
                . At least three disjoint groups are required.
              </p>
              {policy.strategy !== "grouped" && (
                <>
                  {numeric(
                    "purge",
                    "Temporal purge (seconds)",
                    policy.purge_seconds ?? 0,
                    (value) =>
                      nested("partition_policy", "purge_seconds", value),
                    { minimum: 0 },
                  )}
                  <LabChoice
                    label="Temporal boundary action"
                    value={String(policy.boundary_action ?? "error")}
                    options={choices(["error", "purge"])}
                    onChange={(value) =>
                      nested("partition_policy", "boundary_action", value)
                    }
                  />
                </>
              )}
            </div>
            {fractionError && (
              <p role="alert">
                Training and validation fractions must be positive and leave a
                positive test fraction.
              </p>
            )}
            <WorkspaceDisclosure title="Advanced partition policy">
              {json(
                "partition_policy",
                "Partition policy JSON",
                policy,
                (value) => patch("partition_policy", value),
              )}
            </WorkspaceDisclosure>
          </>
        )}
      </WorkspaceDisclosure>
      <WorkspaceDisclosure
        title="Matched representations"
        summary={`${representations.length} explicit codecs · no fitting`}
      >
        <p>
          Every codec encodes the same prepared observations. Wire bytes require
          recorded wire evidence; engineering state requires authored bins.
          Changing kind preserves explicit codec options for review.
        </p>
        {representations.map((raw, index) => {
          const codec = typeof raw === "string" ? { kind: raw } : object(raw);
          return (
            <section
              key={index}
              className="flex flex-col gap-2"
              aria-label={`Codec ${index + 1}`}
            >
              <div className="lab-fields">
                <LabChoice
                  label={`Codec ${index + 1} kind`}
                  value={String(codec.kind ?? "")}
                  options={choices(codecKinds)}
                  onChange={(kind) => updateCodec(index, { ...codec, kind })}
                />
                <Input
                  label={`Codec ${index + 1} name`}
                  value={String(codec.name ?? codec.kind ?? "")}
                  onChange={(name) => updateCodec(index, { ...codec, name })}
                />
              </div>
              <WorkspaceDisclosure
                title={`Codec ${index + 1} explicit configuration`}
              >
                {json(
                  `codec-${index}`,
                  `Codec ${index + 1} configuration JSON`,
                  codec,
                  (value) => updateCodec(index, object(value)),
                )}
              </WorkspaceDisclosure>
              <Button
                onClick={() =>
                  patch(
                    "representations",
                    representations.filter((_, i) => i !== index),
                  )
                }
              >
                Remove codec {index + 1}
              </Button>
            </section>
          );
        })}
        <Button
          disabled={representations.length >= 8 || !!invalid.size}
          onClick={() =>
            patch("representations", [
              ...representations,
              {
                kind: "numeric_text",
                name: `numeric_${representations.length + 1}`,
              },
            ])
          }
        >
          Add matched representation
        </Button>
      </WorkspaceDisclosure>
      <WorkspaceDisclosure
        title="Annotations and advanced acquisition"
        summary="Explicit reviewed record mappings and source options"
      >
        <p>
          Annotations require transformed record_id, observation_sha256 and
          label_source (kind, actor, reference), plus labels or authored
          messages. Review transformed observations before mapping supervision.
        </p>
        {json(
          "annotations",
          "Reviewed annotation mappings JSON",
          p.annotations,
          (value) => patch("annotations", value),
          "array",
        )}
        {json("formats", "Per-source formats JSON", p.formats, (value) =>
          patch("formats", value),
        )}
        {json(
          "records",
          "Explicit inline records JSON",
          p.records,
          (value) => patch("records", value),
          "array",
        )}
        <p>
          Other adapter settings remain in Definition JSON and are preserved by
          these controls.
        </p>
      </WorkspaceDisclosure>
      {!!invalid.size && (
        <p role="alert">
          Resolve incomplete data preparation fields before saving, checking
          compatibility or running.
        </p>
      )}
    </section>
  );
}
