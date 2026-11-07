import { useEffect, useRef, useState } from "react";
import { Input } from "../../components/boardui/base/input/input";
import { Button } from "../../components/studio/primitives";
import { SettingsSection } from "../../components/studio/settings-section";
import { WorkspaceDisclosure } from "../../components/studio/workspace";
import { DraftField } from "./data-preparation";
import { LabChoice } from "./inputs";
import type { ExperimentDefinition } from "./definition";

type Options = Record<string, unknown>;
const provided = (value: unknown, fallback: unknown) =>
  value === undefined ? fallback : value;
const object = (value: unknown): Options =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Options)
    : {};
const selector = (value: unknown) =>
  typeof value === "object" && value ? object(value).kind : value;
const validSelector = (value: unknown) => {
  const kind = selector(value);
  return (
    typeof kind === "string" &&
    kind.length > 0 &&
    (typeof value === "string" ||
      (!!value &&
        typeof value === "object" &&
        !Array.isArray(value) &&
        Object.keys(object(value)).length === 1 &&
        "kind" in object(value)))
  );
};
const learningFields = new Set([
  "family",
  "initialization",
  "architecture",
  "representation",
  "objective",
  "update_strategy",
  "device",
  "dtype",
  "training_dtype",
  "leakage_policy",
]);
const display = (value: unknown) =>
  typeof value === "string" ? value : (JSON.stringify(value) ?? "undefined");

/** Numeric axes mirror policy_selection and timeseries; raw records retain custody in the definition. */
export function NumericLearning({
  definition,
  onChange,
  onValidityChange,
}: {
  definition: ExperimentDefinition;
  onChange: (patch: Partial<ExperimentDefinition>) => void;
  onValidityChange?: (valid: boolean) => void;
}) {
  const p = definition.parameters,
    l = definition.learning ?? {},
    a = object(l.architecture);
  const fusion = definition.engine === "learning.multimodal";
  const selected = (key: string, fallback: string) =>
    selector(key in l ? l[key] : key in p ? p[key] : fallback);
  const family = selected("family", fusion ? "multimodal" : "gru");
  const objective = selected(
    "objective",
    fusion ? "regression" : "forecasting",
  );
  const initialization = selected("initialization", "scratch");
  const dimension = (key: string, fallback: number) =>
    key in a ? a[key] : key in p ? p[key] : fallback;
  const [invalid, setInvalid] = useState<Set<string>>(() => new Set());
  const callback = useRef(onValidityChange);
  callback.current = onValidityChange;
  const [report] = useState(
    () => (key: string, valid: boolean) =>
      setInvalid((current) => {
        if (current.has(key) === !valid) return current;
        const next = new Set(current);
        if (valid) next.delete(key);
        else next.add(key);
        return next;
      }),
  );
  const parameter = (key: string, value: unknown) =>
    onChange({ parameters: { ...p, [key]: value } });
  // Existing duplicate aliases are updated together; unrelated fields remain intact.
  const axis = (key: string, value: string) => {
    const replace = (previous: unknown) =>
      previous && typeof previous === "object"
        ? { ...object(previous), kind: value }
        : value;
    onChange({
      learning: { ...l, [key]: replace(l[key]) },
      ...(key in p ? { parameters: { ...p, [key]: replace(p[key]) } } : {}),
    });
  };
  const architecture = (key: string, value: unknown) =>
    onChange({
      learning: { ...l, architecture: { ...a, [key]: value } },
      ...(key in p ? { parameters: { ...p, [key]: value } } : {}),
    });
  const conflicts: string[] = [];
  for (const key of Object.keys(l))
    if (!learningFields.has(key))
      conflicts.push(
        `Retained learning.${key} is unsupported. Remove or correct this key in Definition JSON; its value has been preserved.`,
      );
  const axes: [string, string, string[]][] = [
    [
      "family",
      "Model family",
      fusion ? ["multimodal"] : ["gru", "transformer", "mlp"],
    ],
    [
      "objective",
      "Learning objective",
      fusion ? ["regression", "classification"] : ["forecasting", "regression"],
    ],
    [
      "representation",
      "Representation",
      fusion
        ? ["multimodal", "raw_arrays", "byte_histogram_fusion"]
        : ["numeric"],
    ],
    [
      "update_strategy",
      "Update strategy",
      ["full", "adam", "full_parameter", "full_finetune"],
    ],
  ];
  const defaults: Record<string, string> = {
    family: fusion ? "multimodal" : "gru",
    objective: fusion ? "regression" : "forecasting",
    representation: fusion ? "multimodal" : "numeric",
    update_strategy: "full",
    initialization: "scratch",
  };
  for (const [key, , supported] of [
    ...axes,
    [
      "initialization",
      "Initialization",
      ["scratch", "from_scratch", "random", "resume"],
    ] as [string, string, string[]],
  ]) {
    const value = selected(key, defaults[key]);
    if (typeof value !== "string" || !supported.includes(value))
      conflicts.push(
        `Retained ${key} ${display(value)} is unsupported. Choose a supported value or review Definition JSON.`,
      );
    for (const container of [l, p])
      if (key in container && !validSelector(container[key]))
        conflicts.push(
          `${key} requires a nonempty string or an object containing only a nonempty string kind. Choose a supported value or review Definition JSON.`,
        );
    if (key in l && key in p && selector(l[key]) !== selector(p[key]))
      conflicts.push(
        `Conflicting learning.${key} and parameters.${key}. Choose a value to reconcile both aliases.`,
      );
  }
  const dimensions: [string, string, number, number, number][] = [
    ["hidden_size", "Hidden size", 16, 4, 512],
    ["layers", "Layers", 1, 1, fusion ? 1 : 8],
    ...(!fusion
      ? [
          ["context", "Context length", 8, 1, 512] as [
            string,
            string,
            number,
            number,
            number,
          ],
        ]
      : []),
    ...(family === "transformer"
      ? [
          ["heads", "Attention heads", 2, 1, 16] as [
            string,
            string,
            number,
            number,
            number,
          ],
        ]
      : []),
  ];
  const allowed = dimensions.map(([key]) => key);
  if (
    l.architecture !== undefined &&
    l.architecture !== null &&
    (typeof l.architecture !== "object" || Array.isArray(l.architecture))
  )
    conflicts.push(
      "learning.architecture must be an object. Review Definition JSON.",
    );
  for (const key of new Set([
    ...Object.keys(a),
    ...[
      "hidden_size",
      "layers",
      "heads",
      "context",
      "input_size",
      "output_size",
      "dropout",
    ].filter((key) => key in p),
  ])) {
    if (!allowed.includes(key))
      conflicts.push(
        `Retained architecture.${key} is unsupported for ${display(family)}. Remove it explicitly in Definition JSON or restore a compatible family.`,
      );
    if (key in a && key in p && a[key] !== p[key])
      conflicts.push(
        `Conflicting architecture.${key} and parameters.${key}. Edit the field to reconcile both aliases.`,
      );
  }
  const ranges: [string, unknown, number, number, boolean][] = [
    ...dimensions.map(
      ([key, , fallback, low, high]) =>
        [key, dimension(key, fallback), low, high, true] as [
          string,
          unknown,
          number,
          number,
          boolean,
        ],
    ),
    ["steps", provided(p.steps, 40), 1, 10000, true],
    [
      "learning_rate",
      provided(p.lr, provided(p.learning_rate, 0.01)),
      Number.MIN_VALUE,
      1,
      false,
    ],
    ["horizon", provided(p.horizon, 1), 1, 128, true],
    ...(!fusion
      ? [
          [
            "target_feature",
            provided(p.target_feature, 0),
            0,
            Number.MAX_SAFE_INTEGER,
            true,
          ] as [string, unknown, number, number, boolean],
        ]
      : []),
  ];
  for (const [key, value, low, high, integer] of ranges)
    if (
      typeof value !== "number" ||
      !Number.isFinite(value) ||
      value < low ||
      value > high ||
      (integer && !Number.isInteger(value))
    )
      conflicts.push(
        `${key} must be ${integer ? "an integer" : "a finite number"} between ${low} and ${high}.`,
      );
  if (
    family === "transformer" &&
    Number(dimension("hidden_size", 16)) % Number(dimension("heads", 2))
  )
    conflicts.push("Transformer hidden size must be divisible by heads.");
  if ("lr" in p && "learning_rate" in p && p.lr !== p.learning_rate)
    conflicts.push(
      "Conflicting lr and learning_rate. Edit Learning rate to reconcile both aliases.",
    );
  for (const key of [
    "batch_size",
    "environment",
    "algorithm",
    "leakage_policy",
    "model_path",
    "pretrained_model",
    "embedding_model",
    "embedding_model_path",
    "resume_from",
    "teacher_checkpoint",
    "initial_weights",
    "initial_weights_path",
  ])
    if (key in p)
      conflicts.push(
        `Retained parameters.${key} is unsupported on this numeric learner. Review Definition JSON; bind artifacts in Inputs.`,
      );
  if (definition.retrieval)
    conflicts.push(
      "Retrieval is unsupported on this numeric learner. Review Definition JSON.",
    );
  if ("leakage_policy" in l)
    conflicts.push(
      "learning.leakage_policy is unsupported; preserve partitions with group_by and temporal_split in Definition JSON.",
    );
  if (
    fusion &&
    (typeof provided(p.target_key, "target") !== "string" ||
      !String(provided(p.target_key, "target")).trim())
  )
    conflicts.push("Target key must be a nonempty record field name.");
  const modalities = provided(p.modalities, ["numeric", "text"]);
  if (
    fusion &&
    (!Array.isArray(modalities) ||
      new Set(modalities).size < 2 ||
      modalities.some(
        (value) => !["numeric", "text", "image", "audio"].includes(value),
      ))
  )
    conflicts.push(
      "Modalities must contain at least two distinct supported names: numeric, text, image, audio. image/audio require already numeric arrays, not files or encoders.",
    );
  if (fusion && p.temporal_split === true)
    conflicts.push(
      "temporal_split is only supported for forecasting. Review Definition JSON.",
    );
  for (const input of definition.inputs)
    if (
      [
        "teacher",
        "initial_weights",
        "pretrained_model",
        "embedding_model",
        "retrieval_index",
      ].includes(input.role ?? "")
    )
      conflicts.push(
        `Retained ${input.role} input is unsupported by this numeric learner. Review its role in Inputs.`,
      );
  const resumes = definition.inputs.filter((input) => input.role === "resume");
  if (
    initialization === "resume"
      ? resumes.length !== 1 ||
        resumes[0].visibility === "evaluation" ||
        resumes[0].visibility === "truth"
      : resumes.length > 0
  )
    conflicts.push(
      "Resume requires initialization=resume and exactly one model-visible resume binding in Inputs. Review Definition JSON and Inputs.",
    );
  for (const container of [l, p]) {
    for (const key of ["device", "dtype", "training_dtype"])
      if (key in container && !validSelector(container[key]))
        conflicts.push(
          `${key} requires a nonempty string or an object containing only a nonempty string kind. Correct it in Definition JSON.`,
        );
    if (
      "device" in container &&
      (typeof selector(container.device) !== "string" ||
        (selector(container.device) as string).toLowerCase() !== "cpu")
    )
      conflicts.push(
        "Requested device is unavailable; this learner runs on cpu. Review Definition JSON.",
      );
    for (const key of ["dtype", "training_dtype"])
      if (
        key in container &&
        (typeof selector(container[key]) !== "string" ||
          !["float32", "fp32"].includes(
            (selector(container[key]) as string).toLowerCase(),
          ))
      )
        conflicts.push(
          "Requested dtype is unavailable; this learner runs float32. Review Definition JSON.",
        );
  }
  if (
    "validation_fraction" in p &&
    (typeof p.validation_fraction !== "number" ||
      !Number.isFinite(p.validation_fraction) ||
      p.validation_fraction <= 0 ||
      p.validation_fraction >= 1)
  )
    conflicts.push(
      "validation_fraction must be between zero and one exclusively. Review Definition JSON.",
    );
  if ("temporal_split" in p && typeof p.temporal_split !== "boolean")
    conflicts.push("temporal_split must be boolean. Review Definition JSON.");
  const signature = conflicts.join("\n");
  useEffect(() => {
    callback.current?.(!invalid.size && !signature);
  }, [invalid, signature]);
  useEffect(() => () => callback.current?.(true), []);
  const number = (
    key: string,
    label: string,
    value: unknown,
    change: (value: unknown) => void,
    minimum = 1,
    integer = true,
  ) => (
    <DraftField
      key={key}
      fieldKey={key}
      label={label}
      value={value}
      onChange={change}
      report={report}
      minimum={minimum}
      integer={integer}
    />
  );
  return (
    <section aria-label="Numeric learning" className="flex flex-col gap-4">
      <SettingsSection
        title={fusion ? "Numeric and text fusion" : "Time-series model"}
        description="Supported numeric model and training axes; CPU float32 full-batch Adam updates."
      >
        <div className="lab-fields">
          {axes.map(([key, label, values]) => {
            const value = display(selected(key, defaults[key]));
            return (
              <LabChoice
                key={key}
                disabled={!!invalid.size}
                label={label}
                value={value}
                options={[
                  ...(!values.includes(value)
                    ? [
                        {
                          id: value,
                          name: `Unsupported: ${value}`,
                          disabled: true,
                        },
                      ]
                    : []),
                  ...values.map((id) => ({ id, name: id })),
                ]}
                onChange={(value) => axis(key, value)}
              />
            );
          })}
        </div>
        {axes
          .filter(
            ([key]) =>
              key in l && key in p && selector(l[key]) !== selector(p[key]),
          )
          .map(
            ([key, label, values]) =>
              values.includes(String(selected(key, defaults[key]))) && (
                <Button
                  key={key}
                  onClick={() =>
                    axis(key, String(selected(key, defaults[key])))
                  }
                >
                  Use displayed {label.toLowerCase()} for both aliases
                </Button>
              ),
          )}
        <p>
          Attach immutable source records and optional resume checkpoints in the
          shared Inputs section. You can author settings before attaching data.
          Initialization is retained as {display(initialization)}; edit uncommon
          options, inline records, partitions and checkpoint initialization in
          Definition JSON.
        </p>
      </SettingsSection>
      <WorkspaceDisclosure
        title="Architecture and training"
        summary="Dimensions and bounded update settings"
        defaultOpen
      >
        <div className="lab-fields">
          {dimensions.map(([key, label, fallback, low]) =>
            number(
              key,
              label,
              dimension(key, fallback),
              (value) => architecture(key, value),
              low,
            ),
          )}
          {number("steps", "Training steps", provided(p.steps, 40), (value) =>
            parameter("steps", value),
          )}
          {number(
            "lr",
            "Learning rate",
            provided(p.lr, provided(p.learning_rate, 0.01)),
            (value) =>
              onChange({
                parameters: {
                  ...p,
                  ...("learning_rate" in p ? { learning_rate: value } : {}),
                  lr: value,
                },
              }),
            Number.MIN_VALUE,
            false,
          )}
        </div>
        {family !== "transformer" && ("heads" in a || "heads" in p) && (
          <Button
            onClick={() => {
              const parameters = { ...p },
                architecture = { ...a };
              delete parameters.heads;
              delete architecture.heads;
              onChange({ parameters, learning: { ...l, architecture } });
            }}
          >
            Remove unsupported attention heads
          </Button>
        )}
      </WorkspaceDisclosure>
      <WorkspaceDisclosure
        title={fusion ? "Fusion inputs" : "Forecast inputs"}
        summary={
          fusion
            ? "Paired record modalities and supervised targets"
            : "Context windows and target feature selection"
        }
        defaultOpen
      >
        {fusion ? (
          <>
            <p>
              Each paired record needs the selected modality fields and a
              target. text is a UTF-8 byte histogram; numeric is a finite
              numeric array. image and audio names accept precomputed numeric
              arrays only. This learner does not decode images/audio or provide
              vision encoders. Dimensions must be consistent; at least four
              examples are required. Classification uses str(target) classes
              with at least two classes in training and none unseen in
              validation; regression predicts finite numeric target vectors.
            </p>
            <div className="lab-fields">
              <Input
                label="Target key"
                value={display(provided(p.target_key, "target"))}
                onChange={(value) => parameter("target_key", value)}
              />
              <DraftField
                fieldKey="modalities"
                label="Modalities JSON"
                kind="array"
                value={modalities}
                report={report}
                onChange={(value) => parameter("modalities", value)}
              />
            </div>
          </>
        ) : (
          <>
            <p>
              Source records supply finite numeric features arrays, or
              scalar/array value fields, in consistent feature order. Context
              length counts previous rows; Forecast horizon counts future target
              rows. Target feature index is zero-based and must fit the input
              vector. Input and output widths are inferred from data. Inline
              values and records remain in Definition JSON; source identities,
              partitions and provenance are preserved.
            </p>
            <div className="lab-fields">
              {number(
                "horizon",
                "Forecast horizon",
                provided(p.horizon, 1),
                (value) => parameter("horizon", value),
              )}
              {number(
                "target_feature",
                "Target feature index",
                provided(p.target_feature, 0),
                (value) => parameter("target_feature", value),
                0,
              )}
            </div>
          </>
        )}
      </WorkspaceDisclosure>
      {!!conflicts.length && (
        <div role="alert">
          {conflicts.map((message) => (
            <p key={message}>{message}</p>
          ))}
        </div>
      )}
    </section>
  );
}
