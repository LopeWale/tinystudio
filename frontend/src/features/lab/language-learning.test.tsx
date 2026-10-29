import { useState } from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { LabPage } from "./index";
import { LanguageLearning } from "./language-learning";
import { makeDefinition, type ExperimentDefinition } from "./definition";

afterEach(cleanup);
function Harness({
  initial = makeDefinition("learning.language"),
}: {
  initial?: ExperimentDefinition;
}) {
  const [definition, setDefinition] = useState(initial);
  const [valid, setValid] = useState(true);
  return (
    <>
      <LanguageLearning
        definition={definition}
        onChange={(patch) =>
          setDefinition((current) =>
            JSON.parse(JSON.stringify({ ...current, ...patch })),
          )
        }
        onValidityChange={setValid}
      />
      <output data-testid="definition">{JSON.stringify(definition)}</output>
      <button disabled={!valid}>Save definition</button>
      <textarea
        aria-label="External JSON"
        value={JSON.stringify(definition)}
        onChange={(event) => setDefinition(JSON.parse(event.target.value))}
      />
    </>
  );
}
const current = (): ExperimentDefinition =>
  JSON.parse(screen.getByTestId("definition").textContent!);
const edit = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
async function select(label: string, name: string) {
  await userEvent.click(
    screen.getByRole("button", { name: new RegExp(`${label}$`) }),
  );
  await userEvent.click(await screen.findByRole("option", { name }));
}
const open = async (title: string) =>
  userEvent.click(screen.getByText(title, { selector: "summary span" }));

it.each(["weights", "resume"])(
  "preserves omitted inherited dimensions for %s and checks genuine explicit conflicts",
  async (initialization) => {
    const initial = makeDefinition("learning.language");
    initial.learning = {
      ...initial.learning,
      initialization,
      architecture: { heads: 3 },
    };
    initial.inputs = [
      {
        artifact_id: "parent",
        sha256: "a".repeat(64),
        role: initialization === "weights" ? "initial_weights" : "resume",
      },
    ];
    render(<Harness initial={initial} />);
    expect(screen.getByLabelText("Hidden size")).toHaveValue("");
    expect(
      screen.getByRole("button", { name: "Save definition" }),
    ).toBeEnabled();
    expect(current().learning!.architecture).toEqual({ heads: 3 });
    edit("Hidden size", "8");
    expect(
      screen.getByRole("button", { name: "Save definition" }),
    ).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("divisible by heads");
    edit("Hidden size", "12");
    expect(
      screen.getByRole("button", { name: "Save definition" }),
    ).toBeEnabled();
  },
);

it("shows omitted model and tokenizer axes as inherited rather than claiming scratch defaults", () => {
  const initial = makeDefinition("learning.language");
  initial.learning = {
    initialization: "weights",
    objective: "causal_lm",
    architecture: {},
  };
  initial.inputs = [
    { artifact_id: "parent", sha256: "a".repeat(64), role: "initial_weights" },
  ];
  render(<Harness initial={initial} />);
  expect(
    screen.getByRole("button", { name: /From checkpoint Model family$/ }),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: /From checkpoint Representation$/ }),
  ).toBeVisible();
  expect(screen.getByLabelText("Hidden size")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Save definition" })).toBeEnabled();
  expect(current().learning).toEqual(initial.learning);
});

it("requires an explicit Transformer heads removal for GRU while preserving all other axes and metadata", async () => {
  const initial = makeDefinition("learning.language");
  initial.parameters.vendor = { keep: true };
  initial.learning!.architecture.vendor = { keep: true };
  initial.objectives = { goal: "retain" };
  initial.inputs = [
    { artifact_id: "corpus", sha256: "a".repeat(64), role: "corpus" },
  ];
  render(<Harness initial={initial} />);
  await select("Model family", "gru");
  expect(screen.getByRole("alert")).toHaveTextContent(
    "GRU does not support heads",
  );
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  expect(current().learning!.architecture.heads).toBe(2);
  await userEvent.click(
    screen.getByRole("button", { name: "Remove Transformer heads for GRU" }),
  );
  expect(current()).toEqual({
    ...initial,
    learning: {
      ...initial.learning,
      family: "gru",
      architecture: {
        hidden_size: 32,
        layers: 1,
        context: 32,
        vendor: { keep: true },
      },
    },
  });
  expect(screen.getByRole("button", { name: "Save definition" })).toBeEnabled();
});

it("retains invalid drafts through parent patches and folded disclosures, gating actions and axis changes", async () => {
  render(<Harness />);
  edit("Hidden size", "-");
  edit("Generation prompt", "feedback");
  expect(screen.getByLabelText("Hidden size")).toHaveValue("-");
  expect(current().learning!.architecture.hidden_size).toBe(32);
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  expect(screen.getByRole("button", { name: /Model family$/ })).toBeDisabled();
  await open("Architecture and vocabulary");
  await open("Architecture and vocabulary");
  expect(screen.getByLabelText("Hidden size")).toHaveValue("-");
  edit("Hidden size", "64");
  expect(screen.getByRole("button", { name: "Save definition" })).toBeEnabled();
});

it("authors engineering vocabulary and requires explicit removal when switching representations", async () => {
  render(<Harness />);
  await select("Representation", "engineering");
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  edit("Engineering vocabulary JSON", '["psi", "valve"]');
  expect(current().learning!.representation).toEqual({
    kind: "engineering",
    vocabulary: ["psi", "valve"],
  });
  edit("Engineering vocabulary JSON", '["psi",');
  expect(screen.getByLabelText("Engineering vocabulary JSON")).toHaveValue(
    '["psi",',
  );
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  edit("Engineering vocabulary JSON", '["psi", "valve"]');
  await select("Representation", "byte");
  expect(current().learning!.representation.vocabulary).toEqual([
    "psi",
    "valve",
  ]);
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  await userEvent.click(
    screen.getByRole("button", {
      name: "Remove retained engineering vocabulary",
    }),
  );
  expect(screen.getByRole("button", { name: "Save definition" })).toBeEnabled();
});

it("preserves checkpoint bindings and gates incompatible initialization and objective transitions", async () => {
  render(<Harness />);
  await select("Initialization", "weights");
  expect(screen.getByRole("alert")).toHaveTextContent(
    "initial_weights binding",
  );
  const weights = {
    artifact_id: "weights",
    sha256: "b".repeat(64),
    role: "initial_weights",
    visibility: "model",
  };
  edit("External JSON", JSON.stringify({ ...current(), inputs: [weights] }));
  expect(screen.getByRole("button", { name: "Save definition" })).toBeEnabled();
  await select("Initialization", "resume");
  expect(current().inputs).toEqual([weights]);
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  const resume = { ...weights, role: "resume" };
  edit("External JSON", JSON.stringify({ ...current(), inputs: [resume] }));
  expect(screen.getByRole("button", { name: "Save definition" })).toBeEnabled();
  await select("Learning objective", "distillation");
  expect(screen.getByRole("alert")).toHaveTextContent("teacher binding");
  const teacher = { ...weights, role: "teacher" };
  edit(
    "External JSON",
    JSON.stringify({ ...current(), inputs: [resume, teacher] }),
  );
  expect(screen.getByRole("button", { name: "Save definition" })).toBeEnabled();
  edit("Distillation alpha", "0.7");
  edit("Distillation temperature", "3");
  expect(current().parameters).toMatchObject({
    distillation_alpha: 0.7,
    temperature: 3,
  });
  expect(current().parameters).not.toHaveProperty("alpha");
  expect(current().learning!.update_strategy).toBe("adamw");
  await select("Learning objective", "causal_lm");
  expect(current().inputs).toEqual([resume, teacher]);
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  await select("Initialization", "scratch");
  expect(current().inputs).toEqual([resume, teacher]);
  expect(
    screen
      .getAllByRole("alert")
      .map((row) => row.textContent)
      .join(" "),
  ).toContain("retained resume binding conflicts");
});

it("keeps BPE options and unfamiliar representation fields until explicit conflict resolution", async () => {
  const initial = makeDefinition("learning.language");
  initial.learning!.representation = {
    kind: "bpe",
    vocab_size: 320,
    vendor: { retained: true },
  };
  render(<Harness initial={initial} />);
  edit("BPE vocabulary size", "400");
  await select("Representation", "byte");
  expect(current().learning!.representation).toEqual({
    kind: "byte",
    vocab_size: 400,
    vendor: { retained: true },
  });
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  await userEvent.click(
    screen.getByRole("button", { name: "Remove retained BPE vocabulary size" }),
  );
  expect(current().learning!.representation).toEqual({
    kind: "byte",
    vendor: { retained: true },
  });
  expect(current().parameters).toEqual(initial.parameters);
});

it("saves, reopens and submits the actual Lab revision with JSON and advanced metadata preserved", async () => {
  const definition = makeDefinition("learning.language");
  definition.parameters.vendor = { retained: [1, 2] };
  definition.learning!.device = "cpu";
  definition.learning!.objective = "distillation";
  definition.inputs = [
    {
      artifact_id: "teacher",
      sha256: "c".repeat(64),
      role: "teacher",
      visibility: "model",
    },
  ];
  let saved = {
    id: "language",
    name: "Language revision",
    revision: 1,
    definition,
  };
  const api = {
    get: vi.fn(async (path: string) =>
      path.startsWith("/api/lab/runs/run/events")
        ? { events: [], next_after: 0, has_more: false }
        : path === "/api/lab/runs/run"
          ? { id: "run", state: "queued" }
          : path === "/api/lab/definitions"
            ? [saved]
            : path === "/api/lab/catalog"
              ? { adapters: [], methods: [], domains: [] }
              : path === "/api/lab/targets"
                ? [{ id: "local", name: "Local" }]
                : [],
    ),
    put: vi.fn(async (_path: string, body: any) => {
      saved = {
        ...saved,
        revision: saved.revision + 1,
        definition: body.definition,
      };
      return saved;
    }),
    post: vi.fn(async (path: string, _body?: unknown) =>
      path === "/api/lab/runs"
        ? { id: "run", state: "queued" }
        : { compatible: true },
    ),
  };
  render(
    <StudioContext.Provider
      value={
        {
          api,
          scopeKey: "language-authoring",
          project: "alpha",
          user: { id: "user" },
          route: "/lab",
          evidence: new EvidenceRegistry(),
          toast: vi.fn(),
        } as any
      }
    >
      <LabPage />
    </StudioContext.Provider>,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Open experiment" }),
  );
  const editor = within(screen.getByRole("dialog"));
  await select("Saved experiment", "Language revision · r1");
  expect(
    editor.getByRole("button", { name: "Run saved revision" }),
  ).toBeEnabled();
  edit("Hidden size", "-");
  for (const name of [
    "Save revision",
    "Check compatibility",
    "Run saved revision",
  ])
    expect(editor.getByRole("button", { name })).toBeDisabled();
  // A draft gate belongs only to its active engine. Switching through shared
  // JSON must release the language editor gate for another engine.
  const portable = JSON.parse(
    (editor.getByLabelText("Experiment definition JSON") as HTMLTextAreaElement)
      .value,
  );
  edit(
    "Experiment definition JSON",
    JSON.stringify({ ...portable, engine: "synthetic.signal" }),
  );
  expect(editor.getByRole("button", { name: "Save revision" })).toBeEnabled();
  expect(
    editor.getByRole("button", { name: "Check compatibility" }),
  ).toBeEnabled();
  edit("Experiment definition JSON", JSON.stringify(portable));
  edit("Hidden size", "64");
  edit("Distillation alpha", "0.7");
  edit("Distillation temperature", "3");
  await open("Corpus documents");
  edit(
    "Corpus documents JSON",
    '[{"text":"A measured response.","partition":"train","provenance":{"retain":true}}]',
  );
  edit("Generation prompt", "A measured");
  const authored = JSON.parse(
    (editor.getByLabelText("Experiment definition JSON") as HTMLTextAreaElement)
      .value,
  );
  expect(authored).toMatchObject({
    parameters: {
      vendor: { retained: [1, 2] },
      documents: [
        {
          text: "A measured response.",
          partition: "train",
          provenance: { retain: true },
        },
      ],
      prompt: "A measured",
      distillation_alpha: 0.7,
      temperature: 3,
    },
    learning: { architecture: { hidden_size: 64 }, device: "cpu" },
  });
  await userEvent.click(editor.getByRole("button", { name: "Save revision" }));
  await waitFor(() => expect(api.put).toHaveBeenCalled());
  expect(api.put.mock.calls[0][1]).toEqual({
    expected_revision: 1,
    definition: authored,
  });
  await userEvent.click(editor.getByRole("button", { name: "New experiment" }));
  await select("Saved experiment", "Language revision · r2");
  expect(editor.getByLabelText("Hidden size")).toHaveValue("64");
  expect(
    JSON.parse(
      (
        editor.getByLabelText(
          "Experiment definition JSON",
        ) as HTMLTextAreaElement
      ).value,
    ),
  ).toEqual(authored);
  await userEvent.click(
    editor.getByRole("button", { name: "Check compatibility" }),
  );
  await waitFor(() => expect(api.post).toHaveBeenCalled());
  await userEvent.click(
    editor.getByRole("button", { name: "Run saved revision" }),
  );
  await waitFor(() =>
    expect(
      api.post.mock.calls.some((call) => call[0] === "/api/lab/runs"),
    ).toBe(true),
  );
  expect(
    api.post.mock.calls.find((call) => call[0] === "/api/lab/preflight")?.[1],
  ).toEqual({ definition: authored, target_id: "local" });
  expect(
    api.post.mock.calls.find((call) => call[0] === "/api/lab/runs")?.[1],
  ).toMatchObject({
    definition_id: "language",
    revision: 2,
    target_id: "local",
  });
});

function distillationDefinition(initialization = "scratch") {
  const definition = makeDefinition("learning.language");
  definition.learning = {
    objective: "distillation",
    initialization,
    family: "gru",
    architecture: { hidden_size: 48, layers: 2, context: 24 },
  };
  definition.inputs = [
    { artifact_id: "teacher", sha256: "d".repeat(64), role: "teacher" },
    ...(initialization === "scratch"
      ? []
      : [
          {
            artifact_id: "student",
            sha256: "e".repeat(64),
            role: initialization === "resume" ? "resume" : "initial_weights",
          },
        ]),
  ];
  return definition;
}

it("reopens omitted distillation tokenizer as From teacher while retaining independent student architecture", () => {
  const initial = distillationDefinition();
  const view = render(<Harness initial={initial} />);
  expect(
    screen.getByRole("button", { name: /From teacher Representation$/ }),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: /gru Model family$/ }),
  ).toBeVisible();
  expect(screen.getByLabelText("Hidden size")).toHaveValue("48");
  expect(
    screen.getByText(
      /Student model family and dimensions remain independent choices/,
    ),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "Save definition" })).toBeEnabled();
  edit("Hidden size", "64");
  const saved = JSON.parse(JSON.stringify(current()));
  view.unmount();
  render(<Harness initial={saved} />);
  expect(
    screen.getByRole("button", { name: /From teacher Representation$/ }),
  ).toBeVisible();
  expect(screen.getByLabelText("Hidden size")).toHaveValue("64");
  expect(current()).toEqual(saved);
  expect(current().learning).not.toHaveProperty("representation");
});

it("explicit From teacher removes supported constraints in both locations and preserves custom data and bindings", async () => {
  const initial = distillationDefinition();
  initial.learning!.representation = {
    kind: "bpe",
    vocab_size: 400,
    vocabulary: ["valve"],
    vendor: { keep: true },
  };
  initial.parameters = {
    ...initial.parameters,
    vocab_size: 400,
    vocabulary: ["valve"],
    vendor: { keep: true },
  };
  render(<Harness initial={initial} />);
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  await select("Representation", "From teacher");
  expect(current().learning).toEqual({
    ...initial.learning,
    representation: { vendor: { keep: true } },
  });
  const {
    vocab_size: _budget,
    vocabulary: _words,
    ...parameters
  } = initial.parameters;
  expect(current().parameters).toEqual(parameters);
  expect(current().inputs).toEqual(initial.inputs);
  expect(
    screen.getByRole("button", { name: /From teacher Representation$/ }),
  ).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "custom representation fields in Definition JSON",
  );
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
});

it.each(["scratch", "weights", "resume"])(
  "inherits optional BPE budget during %s distillation without injecting a fitting default",
  async (initialization) => {
    const initial = distillationDefinition(initialization);
    initial.learning!.representation = "bpe";
    render(<Harness initial={initial} />);
    expect(screen.getByLabelText("BPE vocabulary size")).toHaveValue("");
    expect(
      screen.getByRole("button", { name: "Save definition" }),
    ).toBeEnabled();
    edit("Generation prompt", "An independent student");
    expect(current().learning!.representation).toBe("bpe");
    expect(current().parameters).not.toHaveProperty("vocab_size");
    edit("BPE vocabulary size", "400");
    expect(current().learning!.representation).toEqual({
      kind: "bpe",
      vocab_size: 400,
    });
    edit("BPE vocabulary size", "");
    expect(current().learning!.representation).toEqual({ kind: "bpe" });
    expect(
      screen.getByRole("button", { name: "Save definition" }),
    ).toBeEnabled();
  },
);

it.each(["scratch", "weights", "resume"])(
  "allows engineering vocabulary inherited from the frozen tokenizer during %s distillation",
  (initialization) => {
    const initial = distillationDefinition(initialization);
    initial.learning!.representation = "engineering";
    render(<Harness initial={initial} />);
    expect(
      screen.getByText(
        `Engineering vocabulary is inherited from the ${initialization === "scratch" ? "teacher" : "checkpoint"}.`,
      ),
    ).toBeVisible();
    expect(
      screen.queryByLabelText("Engineering vocabulary JSON"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save definition" }),
    ).toBeEnabled();
    expect(current()).toEqual(initial);
  },
);

it("preserves authored tokenizer constraints through objective changes and warns about retained teacher binding", async () => {
  const initial = distillationDefinition();
  initial.learning!.objective = "causal_lm";
  initial.learning!.representation = {
    kind: "bpe",
    vocab_size: 400,
    vendor: true,
  };
  initial.parameters.vocab_size = 400;
  render(<Harness initial={initial} />);
  await select("Learning objective", "distillation");
  expect(current().learning!.representation).toEqual(
    initial.learning!.representation,
  );
  expect(current().parameters.vocab_size).toBe(400);
  await select("Learning objective", "causal_lm");
  expect(current()).toEqual(initial);
  expect(screen.getByRole("alert")).toHaveTextContent(
    "retained teacher binding requires distillation",
  );
});

it.each(["weights", "resume"])(
  "keeps checkpoint tokenizer inheritance for %s distillation",
  (initialization) => {
    const initial = distillationDefinition(initialization);
    delete initial.learning!.family;
    initial.learning!.architecture = {};
    render(<Harness initial={initial} />);
    expect(
      screen.getByRole("button", { name: /From checkpoint Representation$/ }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: /From checkpoint Model family$/ }),
    ).toBeVisible();
    expect(screen.getByLabelText("Hidden size")).toHaveValue("");
    expect(
      screen.getByText(/its tokenizer must match the teacher exactly/),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Save definition" }),
    ).toBeEnabled();
  },
);

it.each(["scratch", "random"])(
  "explicit From teacher omits representation for %s students when no custom fields remain",
  async (initialization) => {
    const initial = distillationDefinition();
    initial.learning!.initialization = initialization;
    initial.learning!.representation = { kind: "bpe", vocab_size: 400 };
    initial.parameters.vocab_size = 400;
    render(<Harness initial={initial} />);
    await select("Representation", "From teacher");
    expect(current().learning).not.toHaveProperty("representation");
    expect(current().parameters).not.toHaveProperty("vocab_size");
    expect(current().learning!.architecture).toEqual(
      initial.learning!.architecture,
    );
    expect(current().learning!.family).toBe("gru");
    expect(
      screen.getByRole("button", { name: "Save definition" }),
    ).toBeEnabled();
  },
);
