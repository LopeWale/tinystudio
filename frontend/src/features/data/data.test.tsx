import { StrictMode } from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
  act,
} from "@testing-library/react";
import {
  MemoryRouter,
  createMemoryRouter,
  RouterProvider,
} from "react-router-dom";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import {
  DatasetWizard,
  compatibleFiles,
  resolveQuality,
  DatasetPage,
} from "./datasets";
import {
  EvaluationDialog,
  EvaluationPage,
  scoreBins,
  filterScoreRows,
} from "./evaluations";
import { JobDialog, JobPage, defaultExperiment } from "./training";
import { Routes, Route } from "react-router-dom";
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);
afterEach(cleanup);
function runtime(api: any) {
  return {
    api,
    project: "fixture",
    scopeKey: "u:fixture",
    user: { id: "u" },
    route: "/datasets",
    evidence: new EvidenceRegistry(),
    overview: {
      tree: {
        datasets: [
          {
            name: "support",
            snapshots: [
              {
                name: "support-v1",
                dataset: "support",
                training_count: 80,
                validation_count: 20,
              },
            ],
          },
        ],
      },
      catalog: [],
    },
    toast: vi.fn(),
    confirm: vi.fn(async () => true),
    trackTask: vi.fn(),
    showTasks: vi.fn(),
    navigate: vi.fn(),
    refreshOverview: vi.fn(async () => {}),
  } as any;
}
function wrap(child: any, s: any) {
  return render(
    <StudioContext.Provider value={s}>
      <MemoryRouter>{child}</MemoryRouter>
    </StudioContext.Provider>,
  );
}
describe("independent data fixtures", () => {
  it("keeps JSONL and source file compatibility distinct", () => {
    const json = new File(["{}"], "rows.jsonl"),
      pdf = new File(["p"], "manual.pdf"),
      txt = new File(["a"], "notes.TXT");
    expect(compatibleFiles([json, pdf, txt], "upload")).toEqual([json]);
    expect(compatibleFiles([json, pdf, txt], "synthetic")).toEqual([pdf, txt]);
  });
  it("counts zero and exactly 100% and excludes missing scores", () => {
    const rows = [
      { model: "a", metric: "m", score: 0 },
      { model: "a", metric: "m", score: 0.19 },
      { model: "a", metric: "m", score: 1 },
      { model: "a", metric: "m", score: null },
      { model: "a", metric: "m" },
      { model: "b", metric: "m", score: 0.5 },
    ];
    expect(scoreBins(rows, "a", "m")).toEqual([1, 1, 0, 0, 0, 0, 0, 0, 0, 1]);
    expect(filterScoreRows(rows, "m", "a", 1)).toEqual([rows[1]]);
    expect(filterScoreRows(rows, "m", "a", 9)).toEqual([rows[2]]);
  });
  it("retains full training defaults independently prescribed by backend ExperimentSpec", () => {
    expect(defaultExperiment("a")).toMatchObject({
      base_model: "a",
      epochs: 3,
      learning_rate: 0.0002,
      batch_size: 8,
      gradient_accumulation_steps: 2,
      max_length: 512,
      lora_r: 16,
      lora_alpha: 32,
      checkpoint_every_steps: 0,
      keep_checkpoints: 0,
      refinetune_from: null,
    });
  });
  it("waits for queued quality completion instead of rendering task as profile", async () => {
    const complete = { count: 24000, approx_tokens: { total: 1200000 } },
      get = vi
        .fn()
        .mockResolvedValueOnce({
          queued: true,
          task: { id: "q", state: "queued" },
        })
        .mockResolvedValueOnce({ id: "q", state: "running" })
        .mockResolvedValueOnce({
          id: "q",
          state: "succeeded",
          result: complete,
        });
    const wait = vi.fn(async () => {}),
      task = vi.fn();
    expect(
      await resolveQuality(
        { get },
        "/quality",
        new AbortController().signal,
        task,
        wait,
      ),
    ).toEqual(complete);
    expect(get.mock.calls.map((x) => x[0])).toEqual([
      "/quality",
      "/api/tasks/q",
      "/api/tasks/q",
    ]);
    expect(wait).toHaveBeenCalledTimes(2);
  });
  it("rejects truncated/failed quality and cancels polling", async () => {
    await expect(
      resolveQuality(
        { get: async () => ({ truncated: true }) },
        "/q",
        new AbortController().signal,
        () => {},
      ),
    ).rejects.toThrow("complete result");
    await expect(
      resolveQuality(
        {
          get: async () => ({
            queued: true,
            task: { id: "q", state: "failed", error: "worker offline" },
          }),
        },
        "/q",
        new AbortController().signal,
        () => {},
      ),
    ).rejects.toThrow("worker offline");
    const c = new AbortController();
    await expect(
      resolveQuality(
        {
          get: async () => ({
            queued: true,
            task: { id: "q", state: "running" },
          }),
        },
        "/q",
        c.signal,
        () => {},
        async () => {
          c.abort();
        },
      ),
    ).rejects.toThrow("Cancelled");
  });
});
describe("actual React submission controls", () => {
  it("uses direct R2 transfer then separate import request", async () => {
    const api = {
        get: vi.fn(async () => ({ direct_upload: true })),
        post: vi
          .fn()
          .mockResolvedValueOnce({
            url: "https://storage.invalid/grant",
            key: "private/object",
            headers: { "x-grant": "ok" },
          })
          .mockResolvedValueOnce({ name: "uploaded" }),
        upload: vi.fn(async (..._args: any[]) => ({})),
      },
      s = runtime(api),
      close = vi.fn();
    wrap(<DatasetWizard onClose={close} />, s);
    fireEvent.click(screen.getByRole("button", { name: "upload" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Dataset name" }), {
      target: { value: "uploaded" },
    });
    const input = document.querySelector("input[type=file]")!;
    fireEvent.change(input, {
      target: { files: [new File(['{"messages":[]}'], "training.jsonl")] },
    });
    fireEvent.click(
      await screen.findByRole("button", { name: "Create dataset" }),
    );
    await waitFor(() =>
      expect(s.navigate).toHaveBeenCalledWith("#/datasets/uploaded"),
    );
    expect(api.post.mock.calls[0][0]).toBe("/api/artifacts/uploads");
    expect(api.upload.mock.calls[0][0]).toBe("https://storage.invalid/grant");
    expect(api.upload.mock.calls[0][2]).toMatchObject({
      external: true,
      method: "PUT",
      headers: { "x-grant": "ok" },
    });
    expect(api.post.mock.calls[1].slice(0, 2)).toEqual([
      "/api/datasets/from-object",
      { name: "uploaded", key: "private/object" },
    ]);
  });
  it("submits synthetic URL and zero creativity without replacing zero by defaults", async () => {
    const api = {
        upload: vi.fn(async (..._args: any[]) => ({ id: "synthetic-task" })),
      },
      s = runtime(api);
    wrap(<DatasetWizard onClose={() => {}} />, s);
    fireEvent.change(screen.getByRole("textbox", { name: "Dataset name" }), {
      target: { value: "generated" },
    });
    fireEvent.change(
      screen.getByRole("textbox", { name: "Web page URLs (one per line)" }),
      { target: { value: "https://example.org/manual" } },
    );
    fireEvent.click(await screen.findByRole("button", { name: "Next" }));
    fireEvent.change(
      screen.getByRole("spinbutton", { name: "Creativity level" }),
      { target: { value: "0" } },
    );
    fireEvent.click(await screen.findByRole("button", { name: "Next" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Create dataset" }),
    );
    await waitFor(() => expect(api.upload).toHaveBeenCalled());
    const [path, body] = api.upload.mock.calls[0];
    expect(path).toBe("/api/datasets/synthetic");
    expect(body.get("temperature")).toBe("0");
    expect(body.get("urls")).toBe("https://example.org/manual");
    expect(body.get("name")).toBe("generated");
  });
  it("preserves job snapshot and reasoning in actual request", async () => {
    const api = {
        get: vi.fn(async () => [
          { name: "support-v1", training_count: 80, validation_count: 20 },
        ]),
        post: vi.fn(async () => ({ name: "new-job" })),
      },
      s = runtime(api);
    wrap(<JobDialog onClose={() => {}} />, s);
    await screen.findByRole("option", { name: "support-v1 — 80/20" });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "new-job" },
    });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Create job" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/jobs", {
        name: "new-job",
        snapshot: "support-v1",
        reasoning: true,
      }),
    );
    expect(s.navigate).toHaveBeenCalledWith("#/finetuning/new-job");
  });
  it("keeps evaluation model and code metric choice across mode changes", async () => {
    const api = {
        get: vi.fn(async (path: string) =>
          path === "/api/models"
            ? [{ id: "base/a" }]
            : [
                { name: "Exact", kind: "code" },
                { name: "Tone", kind: "rubric" },
              ],
        ),
        post: vi.fn(async () => ({ id: "eval-task" })),
      },
      s = runtime(api);
    wrap(<EvaluationDialog onClose={() => {}} />, s);
    await screen.findByRole("button", { name: "base/a" });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "trial" },
    });
    fireEvent.click(screen.getByRole("button", { name: "base/a" }));
    fireEvent.click(screen.getByRole("button", { name: "Exact" }));
    fireEvent.click(screen.getByRole("button", { name: "byoe" }));
    fireEvent.click(screen.getByRole("button", { name: "agentic" }));
    fireEvent.click(screen.getByRole("button", { name: "Create evaluation" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/evals", {
        name: "trial",
        snapshot: "support-v1",
        models: ["base/a"],
        metrics: ["Tone", "Exact"],
        limit: null,
      }),
    );
  });
  it("uses server bucket paging above the backend 1000-row cap", async () => {
    const api = {
        get: vi.fn(async (path: string) =>
          path === "/api/snapshots"
            ? []
            : {
                meta: {
                  buckets: { training: 2200, validation: 5, uncategorized: 0 },
                },
                total: 2205,
                rows: Array.from({ length: 15 }, (_, i) => ({
                  id: i + 1,
                  messages: [{ role: "user", content: `row ${i}` }],
                  bucket: "training",
                })),
              },
        ),
      },
      s = runtime(api);
    render(
      <StudioContext.Provider value={s}>
        <MemoryRouter initialEntries={["/datasets/support"]}>
          <Routes>
            <Route path="/datasets/:name" element={<DatasetPage />} />
          </Routes>
        </MemoryRouter>
      </StudioContext.Provider>,
    );
    await screen.findAllByText("row 0");
    fireEvent.click(screen.getByRole("radio", { name: /^training/ }));
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith(
        "/api/datasets/support?offset=0&limit=15&bucket=training",
        expect.any(AbortSignal),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith(
        "/api/datasets/support?offset=15&limit=15&bucket=training",
        expect.any(AbortSignal),
      ),
    );
  });
});

describe("linked evaluation records", () => {
  it("selects an actual score bin and opens the matching result", async () => {
    const rows = [
      {
        datapoint: 0,
        model: "a",
        metric: "m",
        score: 0.12,
        user: "Low answer",
        prediction: "Bad result",
        reason: "Missing rule",
      },
      {
        datapoint: 1,
        model: "a",
        metric: "m",
        score: 0.95,
        user: "High answer",
        prediction: "Good result",
      },
    ];
    const api = {
        get: vi.fn(async () => ({
          summary: {
            snapshot: "support-v1",
            datapoints: 2,
            models: ["a"],
            metrics: ["m"],
            state: "succeeded",
            leaderboard: [],
          },
          rows,
        })),
      },
      s = runtime(api);
    render(
      <StudioContext.Provider value={s}>
        <MemoryRouter initialEntries={["/evaluations/trial"]}>
          <Routes>
            <Route path="/evaluations/:name" element={<EvaluationPage />} />
          </Routes>
        </MemoryRouter>
      </StudioContext.Provider>,
    );
    await screen.findByText("High answer");
    const lowBin = screen.getByRole("button", {
      name: "10 ≤ value < 20: 1 observation (50.0%)",
    });
    fireEvent.click(lowBin);
    expect(lowBin).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText("High answer")).toBeNull();
    expect(screen.getByText("Low answer")).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Inspect score for datapoint 1, a" }),
    );
    await screen.findByText("Bad result");
    expect(screen.getByText("Missing rule")).toBeTruthy();
  });
});

describe("quality dialog lifecycle", () => {
  it("does not admit duplicate quality requests under Strict Mode replay", async () => {
    const api = {
        get: vi.fn(async (path: string) =>
          path === "/api/snapshots"
            ? []
            : path.endsWith("/quality")
              ? {
                  queued: true,
                  task: {
                    id: "q",
                    state: "failed",
                    error: "Profile failed fixture",
                  },
                }
              : { meta: { buckets: {} }, total: 0, rows: [] },
        ),
      },
      s = runtime(api);
    render(
      <StudioContext.Provider value={s}>
        <MemoryRouter initialEntries={["/datasets/support"]}>
          <Routes>
            <Route
              path="/datasets/:name"
              element={
                <StrictMode>
                  <DatasetPage />
                </StrictMode>
              }
            />
          </Routes>
        </MemoryRouter>
      </StudioContext.Provider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Quality" }));
    await screen.findByText("Profile failed fixture");
    expect(
      api.get.mock.calls.filter((x) => x[0].endsWith("/quality")),
    ).toHaveLength(1);
  });
});

describe("multipart import contract", () => {
  it("uploads one file with its name and adopts the actual imported dataset", async () => {
    const api = {
        get: vi.fn(async () => ({ direct_upload: false })),
        upload: vi.fn(async (..._args: any[]) => ({ name: "imported" })),
      },
      s = runtime(api);
    wrap(<DatasetWizard onClose={() => {}} />, s);
    fireEvent.click(screen.getByRole("button", { name: "upload" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Dataset name" }), {
      target: { value: "imported" },
    });
    const file = new File(["{}"], "data.jsonl");
    fireEvent.change(document.querySelector("input[type=file]")!, {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create dataset" }));
    await waitFor(() =>
      expect(s.navigate).toHaveBeenCalledWith("#/datasets/imported"),
    );
    const [path, body] = api.upload.mock.calls[0];
    expect(path).toBe("/api/datasets/upload");
    expect(body.get("name")).toBe("imported");
    expect(body.get("file")).toBe(file);
  });
});

const jobFixture = (name: string) => ({
  job: { name, snapshot: "support-v1" },
  snapshot: { dataset: "support", training_count: 80, validation_count: 20 },
  experiments: [
    {
      number: 1,
      state: "succeeded",
      config: {
        base_model: "fixture/model",
        training_type: "lora",
        epochs: 1,
        learning_rate: 0.0002,
      },
      checkpoints: [10],
      loss: [],
      alias: `ft-${name}-1`,
    },
  ],
  analysis: null,
});
function renderTraining() {
  const api = {
    get: vi.fn(async (path: string) =>
      path.startsWith("/api/jobs/")
        ? jobFixture(path.split("/").at(-1)!)
        : path === "/api/models"
          ? []
          : path === "/api/metrics"
            ? []
            : { models: [] },
    ),
    post: vi.fn(async () => ({ id: "capture-task" })),
  };
  const s = runtime(api);
  const router = createMemoryRouter(
    [{ path: "/finetuning/:job", element: <JobPage /> }],
    { initialEntries: ["/finetuning/first"] },
  );
  render(
    <StudioContext.Provider value={s}>
      <RouterProvider router={router} />
    </StudioContext.Provider>,
  );
  return { api, s, router };
}
describe("training route lifecycle and checkpoint contract", () => {
  it.each(["", "all"])(
    "sends all layers as an empty array for input %j",
    async (layers) => {
      const { api } = renderTraining();
      fireEvent.click(
        await screen.findByRole("button", {
          name: "1 checkpoints · capture for drift",
        }),
      );
      if (layers)
        fireEvent.change(
          screen.getByRole("textbox", { name: "Layers (e.g. 8,12,16 or all)" }),
          { target: { value: layers } },
        );
      fireEvent.click(
        screen.getByRole("button", { name: "Capture", exact: true } as any),
      );
      await waitFor(() =>
        expect(api.post).toHaveBeenCalledWith(
          "/api/interp/checkpoint-captures",
          { job: "first", number: 1, dataset: "support", layers: [] },
        ),
      );
    },
  );
  it.each(["Start evaluation", "1 checkpoints · capture for drift"])(
    "closes stale %s dialog when job route changes",
    async (button) => {
      const { router } = renderTraining();
      fireEvent.click(await screen.findByRole("button", { name: button }));
      expect(screen.getByRole("dialog")).toBeTruthy();
      await act(async () => {
        await router.navigate("/finetuning/second");
      });
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      await screen.findByRole("heading", { name: "second" });
    },
  );
});
