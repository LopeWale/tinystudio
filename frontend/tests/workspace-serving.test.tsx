import { StrictMode } from "react";
import { MemoryRouter, Routes, Route, useNavigate } from "react-router-dom";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { ModelsPage } from "../src/features/workspace/models";
import { TracesPage } from "../src/features/workspace/traces";
import {
  PlaygroundController,
  PlaygroundPage,
  PlaygroundProvider,
} from "../src/features/workspace/playground";
import { StudioContext } from "../src/app/StudioContext";
import { EvidenceRegistry } from "../src/app/evidence";
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
function runtime(api: any) {
  return {
    api,
    project: "fixture",
    user: { id: "u", role: "admin" },
    scopeKey: "u:fixture",
    route: "/models",
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
    confirm: vi.fn(async () => true),
    trackTask: vi.fn(),
    showTasks: vi.fn(),
    refreshOverview: vi.fn(async () => {}),
  } as any;
}
function wrap(child: any, s: any) {
  return render(
    <StrictMode>
      <StudioContext.Provider value={s}>
        <MemoryRouter>{child}</MemoryRouter>
      </StudioContext.Provider>
    </StrictMode>,
  );
}
const inventory = [
  {
    id: "job/1",
    alias: "Tuned",
    kind: "fine-tuned",
    base_model: "base/model",
    status: "down",
    parameters: 150000000,
  },
];
const cache = {
  models: [{ id: "org/base", size_gb: 0.3, transformers_loadable: true }],
  token: { configured: false },
  free_disk_gb: 12,
};
describe("model controls use real API contracts", () => {
  it("loads models, saves and clears a Hub token, and submits explicit upload privacy/merge settings", async () => {
    const api = {
      get: vi.fn(async (path: string) =>
        path === "/api/models" ? inventory : cache,
      ),
      post: vi.fn(async (path: string) =>
        path.includes("token")
          ? { user: "fixture-user" }
          : { id: "task1", kind: "hub.upload", state: "queued" },
      ),
    };
    const s = runtime(api);
    wrap(<ModelsPage />, s);
    await screen.findByText("Tuned");
    fireEvent.click(screen.getByRole("button", { name: "Load" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/models/job~1/up", {}),
    );
    fireEvent.change(screen.getByLabelText("Hub access token"), {
      target: { value: "hf_private-test-fixture" },
    });
    fireEvent.click(screen.getByText("Save token"));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/hub/token", {
        token: "hf_private-test-fixture",
      }),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Hub access token")).toHaveValue(""),
    );
    fireEvent.click(screen.getByText("Upload to Hub"));
    fireEvent.change(screen.getByLabelText("Repository ID (user/name)"), {
      target: { value: "tester/weights" },
    });
    fireEvent.click(screen.getByLabelText("Merge LoRA into base model"));
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        "/api/hub/upload",
        {
          project: "fixture",
          kind: "experiment",
          name: "job/1",
          repo_id: "tester/weights",
          private: true,
          merge: true,
        },
        expect.any(AbortSignal),
      ),
    );
    expect(s.trackTask).toHaveBeenCalledWith(
      expect.objectContaining({ id: "task1" }),
    );
  });
  it("sends Hub filters and only offers downloads for loadable results", async () => {
    const api = {
      get: vi.fn(async (path: string) =>
        path === "/api/models"
          ? inventory
          : path === "/api/hub/models"
            ? cache
            : [
                { id: "org/valid", transformers_loadable: true },
                {
                  id: "org/unsupported",
                  transformers_loadable: false,
                  format_tags: ["gguf"],
                },
              ],
      ),
      post: vi.fn(async () => ({
        id: "download1",
        kind: "hub.download",
        state: "queued",
      })),
    };
    wrap(<ModelsPage />, runtime(api));
    await screen.findByText("Tuned");
    fireEvent.change(screen.getByLabelText("Search the Hugging Face Hub"), {
      target: { value: "tiny model" },
    });
    fireEvent.change(screen.getByLabelText("Task"), {
      target: { value: "embeddings" },
    });
    fireEvent.change(screen.getByLabelText("Maximum parameters (billions)"), {
      target: { value: "1.5" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await screen.findByText("org/valid");
    expect(api.get).toHaveBeenCalledWith(
      "/api/hub/search?q=tiny+model&limit=15&task=embeddings&max_params_b=1.5",
      expect.any(AbortSignal),
    );
    expect(screen.getAllByRole("button", { name: "Download" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Download" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/hub/download", {
        repo_id: "org/valid",
      }),
    );
  });
});
describe("review traces", () => {
  it("preserves correction and feedback in a score mutation, promotes only >=90%, confirms deletion", async () => {
    const traces = [
      {
        id: "trace-1",
        model: "job/1",
        messages: [{ role: "user", content: "Question" }],
        response: "Wrong",
        score: null,
      },
    ];
    const api = {
      get: vi.fn(async (path: string) =>
        path.startsWith("/api/traces") ? traces : [{ name: "training" }],
      ),
      post: vi.fn(async () => ({ promoted: 1 })),
      del: vi.fn(async () => ({})),
    };
    const s = runtime(api);
    wrap(<TracesPage />, s);
    await screen.findByText("Question");
    fireEvent.click(screen.getByText("Fix"));
    fireEvent.change(screen.getByLabelText("Corrected answer"), {
      target: { value: "Correct answer" },
    });
    fireEvent.change(screen.getByLabelText("Feedback (optional)"), {
      target: { value: "Uses actual evidence" },
    });
    fireEvent.click(screen.getByText("Save as correct"));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        "/api/traces/trace-1/score",
        { score: 1, corrected: "Correct answer", note: "Uses actual evidence" },
        expect.any(AbortSignal),
      ),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText("Add scored ≥ 0.9 to dataset"));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/traces/promote", {
        dataset: "training",
        min_score: 0.9,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect(api.del).toHaveBeenCalledWith("/api/traces/trace-1"),
    );
    expect(s.confirm).toHaveBeenCalled();
  });
});
const chunk = (text: string) => new TextEncoder().encode(text);
describe("Playground stream ownership", () => {
  it("streams split frames, includes real repository settings and reports server completion latency", async () => {
    const fetcher = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(
          new ReadableStream({
            start(c) {
              c.enqueue(
                chunk(
                  'data: {"document_chunks":[{"document":"manual","similarity_score":0.8}]}\r\n\r\ndata: {"del',
                ),
              );
              c.enqueue(
                chunk(
                  'ta":"Hel"}\n\ndata: {"delta":"lo"}\n\ndata: {"done":true,"seconds":1.25}\n\n',
                ),
              );
              c.close();
            },
          }),
        ),
    );
    const controller = new PlaygroundController({
      project: "a/b",
      user: "u",
      onUnauthorized: vi.fn(),
      fetcher,
    });
    controller.activate();
    controller.configure({
      model: "job/1",
      system: "Be accurate",
      repositories: ["manual"],
      repoLimit: 4,
      repoThreshold: 0.5,
      draft: "Question",
    });
    await controller.send();
    expect(fetcher.mock.calls[0][0]).toBe("/api/p/a%2Fb/playground/chat");
    expect(JSON.parse((fetcher.mock.calls[0] as any)[1].body)).toMatchObject({
      model: "job/1",
      messages: [
        { role: "system", content: "Be accurate" },
        { role: "user", content: "Question" },
      ],
      repositories: { ids: ["manual"], limit: 4, similarity_threshold: 0.5 },
    });
    expect(controller.getSnapshot()).toMatchObject({
      busy: false,
      seconds: 1.25,
      history: [
        { content: "Question" },
        {
          content: "Hello",
          chunks: [{ document: "manual", similarity_score: 0.8 }],
        },
      ],
    });
    controller.deactivate();
  });
  it("revokes a delayed old stream on deactivate, and allows StrictMode reactivation without duplicate sends", async () => {
    let resolve!: (value: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((r) => (resolve = r)));
    const controller = new PlaygroundController({
      project: "a",
      user: "u",
      onUnauthorized: vi.fn(),
      fetcher,
    });
    controller.activate();
    controller.configure({ model: "job/1", draft: "Old question" });
    const pending = controller.send();
    controller.deactivate();
    controller.activate();
    resolve(new Response('data: {"delta":"STALE"}\n\ndata: {"done":true}\n\n'));
    await pending;
    expect(controller.getSnapshot().history).toEqual([]);
    expect(controller.getSnapshot().busy).toBe(false);
    expect(fetcher).toHaveBeenCalledOnce();
  });
  it("rejects incomplete and error streams, restores the prompt, and recovers authentication", async () => {
    const unauthorized = vi.fn();
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response('data: {"delta":"partial"}\n\n'))
      .mockResolvedValueOnce(
        new Response('{"detail":"expired"}', {
          status: 401,
          headers: { "content-type": "application/json" },
        }),
      );
    const controller = new PlaygroundController({
      project: "a",
      user: "u",
      onUnauthorized: unauthorized,
      fetcher,
    });
    controller.activate();
    controller.configure({ model: "job/1", draft: "Retry me" });
    await controller.send();
    expect(controller.getSnapshot()).toMatchObject({
      draft: "Retry me",
      history: [],
      error: expect.stringContaining("before completion"),
    });
    await controller.send();
    expect(unauthorized).toHaveBeenCalledOnce();
    expect(controller.getSnapshot().history).toEqual([]);
    controller.deactivate();
  });
  it("reset aborts the active stream and removes its partial turn", async () => {
    const fetcher = vi.fn(
      (_path: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) =>
          init!.signal!.addEventListener("abort", () =>
            reject(new DOMException("cancelled", "AbortError")),
          ),
        ),
    );
    const controller = new PlaygroundController({
      project: "a",
      user: "u",
      onUnauthorized: vi.fn(),
      fetcher,
    });
    controller.activate();
    controller.configure({ model: "base", draft: "Run" });
    const pending = controller.send();
    controller.reset();
    await pending;
    expect((fetcher.mock.calls[0][1] as any).signal.aborted).toBe(true);
    expect(controller.getSnapshot()).toMatchObject({
      history: [],
      busy: false,
      error: "",
    });
  });
});

describe("Playground workspace session retention", () => {
  it("retains a completed exchange across routes and resets it when the workspace identity changes", async () => {
    const api = {
        get: vi.fn(async (path: string) =>
          path === "/api/models" ? inventory : [],
        ),
      },
      s = runtime(api);
    s.onSessionExpired = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            'data: {"delta":"Retained answer"}\n\ndata: {"done":true,"seconds":0.5}\n\n',
          ),
      ),
    );
    function Navigation() {
      const navigate = useNavigate();
      return (
        <nav>
          <button onClick={() => navigate("/elsewhere")}>
            Leave playground
          </button>
          <button onClick={() => navigate("/playground")}>
            Return to playground
          </button>
        </nav>
      );
    }
    const content = (
      <MemoryRouter initialEntries={["/playground"]}>
        <PlaygroundProvider>
          <Navigation />
          <Routes>
            <Route path="/playground" element={<PlaygroundPage />} />
            <Route path="/elsewhere" element={<p>Other page</p>} />
          </Routes>
        </PlaygroundProvider>
      </MemoryRouter>
    );
    const view = render(
      <StrictMode>
        <StudioContext.Provider value={s}>{content}</StudioContext.Provider>
      </StrictMode>,
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Model")).toHaveValue("job/1"),
    );
    fireEvent.change(screen.getByLabelText("Message your model"), {
      target: { value: "Remember this exchange" },
    });
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Retained answer");
    fireEvent.click(screen.getByText("Leave playground"));
    expect(screen.getByText("Other page")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Return to playground"));
    expect(screen.getByText("Retained answer")).toBeInTheDocument();
    view.rerender(
      <StrictMode>
        <StudioContext.Provider
          value={{ ...s, project: "other", scopeKey: "u:other" }}
        >
          {content}
        </StudioContext.Provider>
      </StrictMode>,
    );
    expect(screen.queryByText("Retained answer")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Message your model")).toHaveValue("");
  });
});
