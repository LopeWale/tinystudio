import { useEffect, useRef, useState } from "react";
import { Input } from "../../components/boardui/base/input/input";
import { Button } from "../../components/studio/primitives";
import { SettingsSection } from "../../components/studio/settings-section";
import { WorkspaceDisclosure } from "../../components/studio/workspace";
import { DraftField } from "./data-preparation";
import { LabChoice } from "./inputs";
import type { ExperimentDefinition } from "./definition";

type Options = Record<string, unknown>;
const object = (value: unknown): Options =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Options)
    : {};
const selector = (value: unknown, fallback: string) =>
  String(
    typeof value === "object" && value
      ? (object(value).kind ?? fallback)
      : (value ?? fallback),
  );
const choices = (values: string[]) => values.map((id) => ({ id, name: id }));

/** Portable patches preserve axes the compact editor does not own. */
export function LanguageLearning({
  definition,
  onChange,
  onValidityChange,
}: {
  definition: ExperimentDefinition;
  onChange: (patch: Partial<ExperimentDefinition>) => void;
  onValidityChange?: (valid: boolean) => void;
}) {
  const p = definition.parameters,
    l = definition.learning ?? {};
  const a = object(l.architecture),
    rep = object(l.representation);
  const initialization = selector(
    l.initialization ?? p.initialization,
    "scratch",
  );
  const checkpointMode = ["weights", "resume"].includes(initialization);
  const familyInherited =
    checkpointMode && l.family === undefined && p.family === undefined;
  const representationInherited =
    checkpointMode && l.representation === undefined;
  const family = selector(
    l.family ?? p.family,
    familyInherited ? "checkpoint" : "transformer",
  );
  const representation = selector(
    l.representation,
    representationInherited ? "checkpoint" : "byte",
  );
  const familyKind =
    (
      {
        tiny_transformer: "transformer",
        decoder_transformer: "transformer",
        rnn: "gru",
      } as Record<string, string>
    )[family] ?? family;
  const representationKind =
    (
      {
        bytes: "byte",
        byte_level: "byte",
        byte_bpe: "bpe",
        domain: "engineering",
        engineering_vocabulary: "engineering",
      } as Record<string, string>
    )[representation] ?? representation;
  const dimension = (key: string, fallback: number) =>
    key in a
      ? a[key]
      : key in p
        ? p[key]
        : checkpointMode
          ? undefined
          : fallback;
  const objective = selector(l.objective, "causal_lm");
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
  const patch = (key: string, value: unknown) => {
    const parameters = { ...p };
    if (value === undefined) delete parameters[key];
    else parameters[key] = value;
    onChange({ parameters });
  };
  const axis = (key: string, value: unknown) =>
    onChange({ learning: { ...l, [key]: value } });
  const chooseAxis = (key: string, value: string) =>
    axis(
      key,
      typeof l[key] === "object" && l[key] ? { ...l[key], kind: value } : value,
    );
  const roles = definition.inputs.map((input) => input.role);
  const hasRole = (role: string) =>
    definition.inputs.some(
      (input) =>
        input.role === role &&
        !!input.artifact_id &&
        !!input.sha256 &&
        input.visibility !== "evaluation" &&
        input.visibility !== "truth",
    );
  const conflicts: string[] = [];
  const supportedAxes: [string, string, string[]][] = [
    [
      "family",
      familyKind,
      ["transformer", "gru", ...(familyInherited ? ["checkpoint"] : [])],
    ],
    [
      "representation",
      representationKind,
      [
        "byte",
        "bpe",
        "engineering",
        ...(representationInherited ? ["checkpoint"] : []),
      ],
    ],
    [
      "initialization",
      initialization,
      ["scratch", "from_scratch", "random", "weights", "resume"],
    ],
    [
      "objective",
      objective,
      ["causal_lm", "next_token", "cross_entropy", "distillation"],
    ],
    [
      "update strategy",
      selector(l.update_strategy ?? p.update_strategy, "adamw"),
      ["full", "adamw", "full_parameter", "full_finetune"],
    ],
  ];
  for (const [name, value, supported] of supportedAxes)
    if (!supported.includes(value))
      conflicts.push(
        `Retained ${name} ${value} is unsupported by this language engine. Choose a supported value or review Definition JSON.`,
      );

  if (familyKind === "gru" && ("heads" in a || "heads" in p))
    conflicts.push(
      "GRU does not support heads. Remove the retained heads setting explicitly or return to Transformer.",
    );
  if (initialization === "weights" && !hasRole("initial_weights"))
    conflicts.push(
      "Weights initialization requires an immutable initial_weights binding in Inputs.",
    );
  if (initialization === "resume" && !hasRole("resume"))
    conflicts.push(
      "Resume initialization requires an immutable resume binding in Inputs.",
    );
  for (const role of ["resume", "initial_weights"]) {
    if (
      roles.includes(role) &&
      role !==
        ({ weights: "initial_weights", resume: "resume" } as Options)[
          initialization
        ]
    )
      conflicts.push(
        `The retained ${role} binding conflicts with ${initialization}. Remove or change its role in Inputs, or restore matching initialization.`,
      );
  }
  if (objective === "distillation" && !hasRole("teacher"))
    conflicts.push(
      "Distillation requires an immutable teacher binding in Inputs.",
    );
  if (objective !== "distillation" && roles.includes("teacher"))
    conflicts.push(
      "The retained teacher binding requires distillation. Review its role in Inputs or restore distillation.",
    );
  if (
    !representationInherited &&
    representationKind !== "engineering" &&
    ("vocabulary" in rep || "vocabulary" in p)
  )
    conflicts.push(
      "The retained engineering vocabulary conflicts with this representation. Remove it explicitly or restore engineering.",
    );
  if (
    !representationInherited &&
    representationKind !== "bpe" &&
    ("vocab_size" in rep || "vocab_size" in p)
  )
    conflicts.push(
      "The retained BPE vocabulary size conflicts with this representation. Remove it explicitly or restore bpe.",
    );
  const vocabulary = rep.vocabulary ?? p.vocabulary;
  if (
    representationKind === "engineering" &&
    (!checkpointMode || vocabulary !== undefined) &&
    (!Array.isArray(vocabulary) ||
      !vocabulary.length ||
      vocabulary.some((word) => typeof word !== "string" || !word.trim()) ||
      new Set(vocabulary).size !== vocabulary.length)
  )
    conflicts.push(
      "Engineering representation requires a nonempty list of unique vocabulary strings.",
    );
  for (const key of ["family", "initialization", "update_strategy"])
    if (key in l && key in p && selector(l[key], "") !== selector(p[key], ""))
      conflicts.push(
        `Conflicting learning.${key} and parameters.${key}. Reconcile the retained values in Definition JSON.`,
      );
  for (const key of ["hidden_size", "layers", "context", "heads"])
    if (key in a && key in p && a[key] !== p[key])
      conflicts.push(
        `Conflicting architecture.${key} and parameters.${key}. Reconcile the retained values in Definition JSON.`,
      );
  if (
    familyKind === "transformer" &&
    dimension("hidden_size", 32) !== undefined &&
    dimension("heads", 2) !== undefined &&
    Number(dimension("hidden_size", 32)) % Number(dimension("heads", 2))
  )
    conflicts.push("Transformer hidden size must be divisible by heads.");
  if (p.batch_size !== undefined && p.batch_size !== 1)
    conflicts.push(
      "This language engine supports batch size 1. Set batch size to 1.",
    );
  const ranges: [string, unknown, number, number, boolean][] = [
    ["hidden_size", dimension("hidden_size", 32), 4, 512, true],
    ["layers", dimension("layers", 1), 1, 8, true],
    ["context", dimension("context", 32), 2, 512, true],
    ["steps", p.steps ?? 10, 1, 100000, true],
    [
      "learning_rate",
      p.lr ?? p.learning_rate ?? 0.003,
      Number.MIN_VALUE,
      1,
      false,
    ],
    ["max_new_tokens", p.max_new_tokens ?? 16, 0, 1024, true],
  ];
  if (familyKind === "transformer")
    ranges.push(["heads", dimension("heads", 2), 1, 16, true]);
  if (objective === "distillation")
    ranges.push([
      "distillation_alpha",
      p.distillation_alpha ?? 0.5,
      0,
      1,
      false,
    ]);
  for (const [name, value, low, high, integer] of ranges) {
    if (
      checkpointMode &&
      value === undefined &&
      ["hidden_size", "layers", "context", "heads"].includes(name)
    )
      continue;
    if (
      typeof value !== "number" ||
      !Number.isFinite(value) ||
      value < low ||
      value > high ||
      (integer && !Number.isInteger(value))
    )
      conflicts.push(
        `${name} must be ${integer ? "an integer" : "a finite number"} between ${low} and ${high}.`,
      );
  }
  if (
    p.lr !== undefined &&
    p.learning_rate !== undefined &&
    p.lr !== p.learning_rate
  )
    conflicts.push(
      "Conflicting lr and learning_rate. Reconcile the retained values in Definition JSON.",
    );
  for (const key of ["vocab_size", "vocabulary"])
    if (
      key in rep &&
      key in p &&
      JSON.stringify(rep[key]) !== JSON.stringify(p[key])
    )
      conflicts.push(
        `Conflicting representation.${key} and parameters.${key}. Reconcile in Definition JSON.`,
      );
  if (
    p.texts !== undefined &&
    (!Array.isArray(p.texts) ||
      p.texts.some((text) => typeof text !== "string"))
  )
    conflicts.push("Corpus texts must be an array of strings.");
  if (
    p.documents !== undefined &&
    (!Array.isArray(p.documents) ||
      p.documents.some(
        (row) =>
          typeof object(row).text !== "string" &&
          !Array.isArray(object(row).messages),
      ))
  )
    conflicts.push(
      "Corpus documents must contain objects with text strings or messages arrays.",
    );
  const conflictSignature = conflicts.join("\n");
  useEffect(() => {
    callback.current?.(!invalid.size && !conflictSignature);
  }, [invalid, conflictSignature]);
  useEffect(() => () => callback.current?.(true), []);
  const numeric = (
    key: string,
    label: string,
    value: unknown,
    change: (value: unknown) => void,
    minimum = 1,
    integer = true,
    optional = false,
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
      optional={optional}
    />
  );
  const removeRep = (key: string) => {
    const parameters = { ...p },
      representation = { ...rep };
    delete parameters[key];
    delete representation[key];
    onChange({
      parameters,
      learning: {
        ...l,
        representation:
          typeof l.representation === "object"
            ? representation
            : l.representation,
      },
    });
  };
  return (
    <section aria-label="Language learning" className="flex flex-col gap-4">
      <SettingsSection
        title="Language model"
        description="Independent model, initialization and training axes."
      >
        <div className="lab-fields">
          <LabChoice
            disabled={!!invalid.size}
            label="Model family"
            value={family}
            options={[
              ...(familyInherited
                ? [
                    {
                      id: "checkpoint",
                      name: "From checkpoint",
                      disabled: true,
                    },
                  ]
                : []),
              ...choices(["transformer", "gru"]),
            ]}
            onChange={(value) => chooseAxis("family", value)}
          />
          <LabChoice
            disabled={!!invalid.size}
            label="Initialization"
            value={initialization}
            options={choices(["scratch", "weights", "resume"])}
            onChange={(value) => chooseAxis("initialization", value)}
          />
          <LabChoice
            disabled={!!invalid.size}
            label="Representation"
            value={representation}
            options={[
              ...(representationInherited
                ? [
                    {
                      id: "checkpoint",
                      name: "From checkpoint",
                      disabled: true,
                    },
                  ]
                : []),
              ...choices(["byte", "bpe", "engineering"]),
            ]}
            onChange={(value) => chooseAxis("representation", value)}
          />
          <LabChoice
            disabled={!!invalid.size}
            label="Learning objective"
            value={objective}
            options={choices(["causal_lm", "distillation"])}
            onChange={(value) => chooseAxis("objective", value)}
          />
          <LabChoice
            disabled={!!invalid.size}
            label="Update strategy"
            value={selector(l.update_strategy ?? p.update_strategy, "adamw")}
            options={choices(["full", "adamw"])}
            onChange={(value) => chooseAxis("update_strategy", value)}
          />
        </div>
        <p>
          Weights reuse the saved model and frozen tokenizer with a fresh
          optimizer and RNG on a new corpus. Resume restores optimizer and RNG
          and requires identical corpus, configuration and runtime. Attach
          immutable checkpoint roles in Inputs. Authored architecture and
          tokenizer settings must match the checkpoint. The executor verifies
          checkpoint contents; preflight checks the declared binding roles.
        </p>
      </SettingsSection>
      <WorkspaceDisclosure
        title="Architecture and vocabulary"
        defaultOpen
        summary="Model dimensions and explicit tokenizer configuration"
      >
        {checkpointMode && (
          <p>
            Blank model dimensions and omitted tokenizer constraints inherit the
            checkpoint; the executor verifies the resolved configuration.
          </p>
        )}
        <div className="lab-fields">
          {(
            [
              ["hidden_size", "Hidden size", 32, 4],
              ["layers", "Layers", 1, 1],
              ["context", "Context length", 32, 2],
              ...(familyKind === "transformer"
                ? [["heads", "Attention heads", 2, 1]]
                : []),
            ] as [string, string, number, number][]
          ).map(([key, label, fallback, minimum]) =>
            numeric(
              key,
              label,
              dimension(key, fallback),
              (value) => axis("architecture", { ...a, [key]: value }),
              minimum,
              true,
              checkpointMode,
            ),
          )}
          {representationKind === "bpe" &&
            numeric(
              "vocab_size",
              "BPE vocabulary size",
              rep.vocab_size ??
                p.vocab_size ??
                (checkpointMode ? undefined : 320),
              (value) =>
                axis("representation", {
                  ...rep,
                  kind: representation,
                  vocab_size: value,
                }),
              257,
              true,
              checkpointMode,
            )}
        </div>
        {representationKind === "engineering" &&
          (!checkpointMode || vocabulary !== undefined) && (
            <DraftField
              label="Engineering vocabulary JSON"
              fieldKey="vocabulary"
              kind="array"
              value={vocabulary ?? []}
              report={report}
              onChange={(value) =>
                axis("representation", {
                  ...rep,
                  kind: representation,
                  vocabulary: value,
                })
              }
            />
          )}
        {representationKind === "engineering" &&
          checkpointMode &&
          vocabulary === undefined && (
            <div>
              <p>Engineering vocabulary is inherited from the checkpoint.</p>
              <Button
                onClick={() =>
                  axis("representation", {
                    ...rep,
                    kind: representation,
                    vocabulary: [],
                  })
                }
              >
                Specify vocabulary constraint
              </Button>
            </div>
          )}
        {representationKind === "engineering" &&
          checkpointMode &&
          vocabulary !== undefined && (
            <Button onClick={() => removeRep("vocabulary")}>
              Inherit engineering vocabulary
            </Button>
          )}
      </WorkspaceDisclosure>
      <WorkspaceDisclosure
        title="Training and generation"
        defaultOpen
        summary="Bounded updates and generated sample"
      >
        <div className="lab-fields">
          {numeric("steps", "Training steps", p.steps ?? 10, (value) =>
            patch("steps", value),
          )}
          {numeric(
            "learning_rate",
            "Learning rate",
            p.lr ?? p.learning_rate ?? 0.003,
            (value) =>
              patch(p.lr !== undefined ? "lr" : "learning_rate", value),
            1e-12,
            false,
          )}
          {numeric("batch_size", "Batch size", p.batch_size ?? 1, (value) =>
            patch("batch_size", value),
          )}
          <Input
            label="Generation prompt"
            value={String(p.prompt ?? "")}
            onChange={(value) => patch("prompt", value)}
          />
          {numeric(
            "max_new_tokens",
            "Generated token limit",
            p.max_new_tokens ?? 16,
            (value) => patch("max_new_tokens", value),
            0,
          )}
          {objective === "distillation" && (
            <>
              {numeric(
                "temperature",
                "Distillation temperature",
                p.temperature ?? 2,
                (value) => patch("temperature", value),
                1e-12,
                false,
              )}
              {numeric(
                "alpha",
                "Distillation alpha",
                p.distillation_alpha ?? 0.5,
                (value) => patch("distillation_alpha", value),
                0,
                false,
              )}
            </>
          )}
        </div>
      </WorkspaceDisclosure>
      <WorkspaceDisclosure
        title="Corpus documents"
        summary="Inline texts and structured documents; retained artifacts attach in Inputs"
      >
        <DraftField
          label="Corpus texts JSON"
          fieldKey="texts"
          kind="array"
          value={p.texts ?? []}
          report={report}
          onChange={(value) => patch("texts", value)}
        />
        <DraftField
          label="Corpus documents JSON"
          fieldKey="documents"
          kind="array"
          value={p.documents ?? []}
          report={report}
          onChange={(value) => patch("documents", value)}
        />
        <p>
          Texts are strings. Documents retain text, partition and provenance
          fields. Corpus, source and validation artifacts attach in Inputs;
          local file paths do not belong in parameters. Other advanced fields
          remain in Definition JSON.
        </p>
      </WorkspaceDisclosure>
      {conflicts.map((message) => (
        <p key={message} role="alert">
          {message}
        </p>
      ))}
      {familyKind === "gru" && ("heads" in a || "heads" in p) && (
        <Button
          onClick={() => {
            const architecture = { ...a },
              parameters = { ...p };
            delete architecture.heads;
            delete parameters.heads;
            onChange({ parameters, learning: { ...l, architecture } });
          }}
        >
          Remove Transformer heads for GRU
        </Button>
      )}
      {representationKind !== "engineering" &&
        ("vocabulary" in rep || "vocabulary" in p) && (
          <Button onClick={() => removeRep("vocabulary")}>
            Remove retained engineering vocabulary
          </Button>
        )}
      {representationKind !== "bpe" &&
        ("vocab_size" in rep || "vocab_size" in p) && (
          <Button onClick={() => removeRep("vocab_size")}>
            Remove retained BPE vocabulary size
          </Button>
        )}
      {!!invalid.size && (
        <p role="alert">
          Resolve incomplete language fields before changing axes, saving,
          checking compatibility or running.
        </p>
      )}
    </section>
  );
}
