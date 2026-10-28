import { makeDefinition } from "../features/lab/definition";
/** Illustrative records for the development workspace preview. Never server data. */
export const previewProject = "illustrative-support";
const prompts = [
  [
    "Summarize this note: The customer cannot sign in after a password reset.",
    "The customer cannot sign in after resetting their password.",
  ],
  ["Classify: Where can I download my receipt?", "billing"],
  [
    "Extract the requested temperature: Set the office to 23 degrees.",
    '{"temperature":23}',
  ],
];
export const previewDatasets = [
  "Sample support",
  "Sample instructions",
  "Sample knowledge",
  "Sample validation",
  "Sample synthetic",
  "Sample research",
].map((name, index) => ({
  name,
  datapoints: 3,
  snapshot_count: 1,
  origin: { kind: index === 4 ? "synthetic" : "upload" },
  created: `2026-10-${String(20 + index).padStart(2, "0")}T10:00:00Z`,
}));
export const previewRecords = prompts.map(([prompt, answer], index) => ({
  id: index + 1,
  bucket: index === 2 ? "validation" : "training",
  messages: [
    { role: "user", content: prompt },
    { role: "assistant", content: answer },
  ],
  labels: [index === 1 ? "billing" : "support"],
  source: "illustrative",
  rating: 9,
}));
export const previewSnapshots = previewDatasets.map((dataset, index) => ({
  name: `sample-v${index + 1}`,
  dataset: dataset.name,
  training_count: 2,
  validation_count: 1,
  created: dataset.created,
}));
export const previewModels = [
  {
    id: "sample-support/1",
    alias: "Sample support LoRA",
    kind: "fine-tuned",
    base_model: "sample/base-1b",
    parameters: 1_000_000_000,
    status: "up",
    training_type: "lora",
    created: "2026-10-26T10:00:00Z",
    notes: "Illustrative model record; inference is disabled in this preview.",
  },
  {
    id: "sample/base-1b",
    alias: "Sample base model",
    kind: "base",
    base_model: "sample/base-1b",
    parameters: 1_000_000_000,
    status: "down",
    created: "2026-10-25T10:00:00Z",
  },
];
export const previewJobs = ["Sample support run", "Sample instruction run"].map(
  (name, index) => ({
    job: {
      name,
      snapshot: previewSnapshots[index].name,
      goal: "Illustrative comparison of answer quality",
      created: "2026-10-26T10:00:00Z",
    },
    snapshot: previewSnapshots[index],
    experiments: [1, 2].map((number) => ({
      number,
      state: "succeeded",
      config: {
        base_model: "sample/base-1b",
        training_type: "lora",
        epochs: number,
        learning_rate: 0.0002,
        batch_size: 4,
        max_seq_length: 512,
        lora_rank: 8,
        lora_alpha: 16,
      },
      metrics: {
        train_loss: number === 1 ? 0.7 : 0.52,
        eval_loss: number === 1 ? 0.82 : 0.68,
      },
      train_loss: number === 1 ? 0.7 : 0.52,
      loss: [0, 50, 100, 150, 200].map((step, i) => ({
        step,
        loss: 1.8 - i * (number === 1 ? 0.275 : 0.32),
        ...(i % 2 === 0
          ? { eval_loss: 1.95 - i * (number === 1 ? 0.2825 : 0.3175) }
          : {}),
      })),
      checkpoints: [100, 200],
      log: "Illustrative recorded run. No training executed.",
    })),
  }),
);
export const previewMetrics = [
  {
    name: "Sample exact match",
    kind: "code",
    check: "exact_match",
    description: "Compare a prediction with its reference answer.",
  },
  {
    name: "Sample helpfulness",
    kind: "rubric",
    description: "Review whether an answer resolves the request.",
    rules: {
      should: [
        "Address the user's request directly",
        "Keep the answer concise",
      ],
      should_not: ["Invent account information"],
    },
  },
];
export const previewEvaluations = [
  "Sample support evaluation",
  "Sample instruction evaluation",
].map((name, index) => {
  const rows = previewRecords.flatMap((record, i) =>
    previewModels.flatMap((model, mi) =>
      previewMetrics.map((metric) => ({
        datapoint: i,
        model: model.id,
        metric: metric.name,
        score: mi === 0 || i !== 1 ? 1 : 0,
        user: record.messages[0].content,
        reference: record.messages[1].content,
        prediction:
          mi === 0 || i !== 1 ? record.messages[1].content : "account",
        reason: "Illustrative scored case; no evaluator was invoked.",
      })),
    ),
  );
  const summary = {
    name,
    snapshot: previewSnapshots[index].name,
    datapoints: 3,
    models: previewModels.map((m) => m.id),
    metrics: previewMetrics.map((m) => m.name),
    state: "succeeded",
    leaderboard: previewModels.map((m, mi) => ({
      model: m.id,
      overall: mi === 0 ? 1 : 2 / 3,
      scores: Object.fromEntries(
        previewMetrics.map((metric) => [metric.name, mi === 0 ? 1 : 2 / 3]),
      ),
    })),
  };
  return { summary, rows };
});
export const previewProjects = [
  {
    slug: previewProject,
    name: "Illustrative support",
    goal: "Review sample support data, experiments and evaluation cases.",
    datasets: 6,
    jobs: 2,
    created: "2026-10-20T10:00:00Z",
  },
  {
    slug: "illustrative-research",
    name: "Illustrative research",
    goal: "A second local example project; the same explicitly illustrative records are used.",
    datasets: 6,
    jobs: 2,
    created: "2026-10-21T10:00:00Z",
  },
];
export const previewRepositories = [
  {
    name: "Sample knowledge",
    description: "Illustrative support documentation",
    embedding_model: "sample/embedding",
    chunk_count: 4,
    documents: [
      { name: "account-help.md", kind: "md", chunks: 2 },
      { name: "billing-help.md", kind: "md", chunks: 2 },
    ],
  },
];
export const previewSettings = {
  settings: [
    {
      key: "HELPER_BASE_URL",
      label: "Base URL",
      group: "helper",
      secret: false,
      value: "https://illustrative.invalid/v1",
      set: false,
      source: "default",
    },
    {
      key: "HELPER_MODEL",
      label: "Model",
      group: "helper",
      secret: false,
      value: "sample-helper",
      set: false,
      source: "default",
    },
    {
      key: "OPENAI_API_KEY",
      label: "API key",
      group: "openai",
      secret: true,
      set: false,
      source: "default",
    },
  ],
};
export const previewOverview = {
  tree: {
    datasets: previewDatasets.map((dataset, i) => ({
      ...dataset,
      snapshots: [previewSnapshots[i]],
    })),
  },
  catalog: previewModels,
  llm: { model: "sample-helper" },
};
// The production overview returns `comparisons` (services/interp.py:overview).
// Keep its envelope explicit even when this isolated preview has no captures.
const previewInterpretability: {
  captures: unknown[];
  comparisons: unknown[];
} = { captures: [], comparisons: [] };
const illustrativeDefinition = {
  id: "illustrative-signal",
  name: "Sample signal study",
  revision: 1,
  definition: { ...makeDefinition(), name: "Sample signal study" },
};
const illustrativeRun = {
  id: "illustrative-run",
  definition_id: illustrativeDefinition.id,
  target_id: "local",
  state: "succeeded",
  spec_digest: "illustrative-specification",
  spec: { definition: illustrativeDefinition.definition, schedule: {} },
  result: {
    artifacts: [
      {
        id: "illustrative-processed",
        name: "processed.jsonl",
        role: "transformed_dataset",
      },
    ],
  },
};
const illustrativeRecords = Array.from({ length: 48 }, (_, index) => ({
  time: index * 0.1,
  available_at: index * 0.1,
  episode_id: "illustrative-episode",
  clock_domain: "illustrative",
  clock_unit: "seconds",
  clock_origin: "illustrative-origin",
  clock_session: "illustrative-session",
  value: Math.sin(index * 0.3),
  raw: Math.sin(index * 0.3) + Math.cos(index) * 0.08,
}));
export const previewReads = {
  "/api/overview": previewOverview,
  "/api/projects": previewProjects,
  "/api/datasets": previewDatasets,
  "/api/snapshots": previewSnapshots,
  "/api/jobs": previewJobs,
  "/api/autopilot": [],
  "/api/evals": previewEvaluations.map(({ summary }) => summary),
  "/api/metrics": previewMetrics,
  "/api/models": previewModels,
  "/api/repositories": previewRepositories,
  "/api/hub/models": {
    models: [{ id: "sample/base-1b", size_gb: 2, transformers_loadable: true }],
    free_disk_gb: 64,
    token: { configured: false },
  },
  "/api/settings": previewSettings,
  "/api/config": {
    database_url: "illustrative · disconnected",
    redis_url: "illustrative · disconnected",
    qdrant_url: "illustrative · disconnected",
    database_version: "illustrative",
    inference: { mode: "disabled in preview" },
    device: "preview",
    dtype: "illustrative",
    data_dir: "illustrative",
  },
  "/api/users": [
    {
      id: "preview-user",
      name: "Sample reviewer",
      email: "reviewer@example.invalid",
      role: "admin",
      disabled: false,
      created: "2026-10-20T10:00:00Z",
    },
  ],
  "/api/keys": [],
  "/api/tasks": [],
  "/api/stats": {
    requests: 24,
    prompt_tokens: 1500,
    completion_tokens: 600,
    training_seconds: 240,
    models_loaded: ["sample-support/1"],
    requests_per_day: [
      ["2026-10-24", 6],
      ["2026-10-25", 8],
      ["2026-10-26", 10],
    ],
    requests_per_model: [
      ["sample-support/1", 18],
      ["sample/base-1b", 6],
    ],
  },
  "/api/traces": previewRecords.map((r, i) => ({
    id: `illustrative-trace-${i + 1}`,
    time: "2026-10-26T10:00:00Z",
    model: "sample-support/1",
    messages: [r.messages[0]],
    response: r.messages[1].content,
    score: 1,
    seconds: 0.8,
    origin: "illustrative",
    document_chunks: [],
  })),
  "/api/assistant/models": {
    openai: {
      configured: false,
      default: "sample-helper",
      models: [
        { id: "sample-helper", name: "Sample helper · inference disabled" },
      ],
    },
  },
  "/api/interp": previewInterpretability,
  "/api/lab/catalog": {
    adapters: [
      {
        id: "synthetic.signal",
        name: "Synthetic signals",
        domain: "signals",
        implemented: true,
        available: false,
        reason: "Illustrative only — no execution target connected",
        modes: ["offline", "online"],
        version: "illustrative",
      },
      {
        id: "recorded.replay",
        name: "Recorded replay",
        domain: "recorded",
        implemented: true,
        available: false,
        reason: "Illustrative only — no execution target connected",
        modes: ["offline"],
        version: "illustrative",
      },
    ],
    methods: [],
    domains: [],
  },
  "/api/lab/definitions": [illustrativeDefinition],
  "/api/lab/runs": [illustrativeRun],
  "/api/lab/runs/illustrative-run": illustrativeRun,
  "/api/lab/runs/illustrative-run/events": {
    events: [
      {
        seq: 1,
        time: "2026-10-27T12:00:00Z",
        kind: "completed",
        payload: { note: "Illustrative completion; no execution occurred" },
      },
    ],
    next_after: 1,
  },
  "/api/lab/runs/illustrative-run/records": {
    records: illustrativeRecords,
    next_offset: null,
  },
  "/api/lab/targets": [
    { id: "local", name: "Illustrative local target", available: false },
  ],
  "/api/lab/operations": [
    {
      id: "illustrative-export",
      kind: "export",
      state: "succeeded",
      created_at: "2026-10-27T12:00:00Z",
    },
  ],
  "/api/lab/operations/illustrative-export": {
    id: "illustrative-export",
    kind: "export",
    state: "succeeded",
    result: { note: "Illustrative operation; downloads are disabled" },
  },
  "/api/lab/inputs": [
    {
      id: "illustrative-input",
      name: "sample-observations.jsonl",
      sha256: "illustrative-checksum",
      size: 2048,
      media_type: "application/x-ndjson",
    },
  ],
};

export type WorkspacePreviewReadPath = keyof typeof previewReads;
export function isWorkspacePreviewReadPath(
  path: string,
): path is WorkspacePreviewReadPath {
  return Object.prototype.hasOwnProperty.call(previewReads, path);
}
