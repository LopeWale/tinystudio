import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import { WorkspacePreview } from "./WorkspacePreview";
import {
  createWorkspacePreviewApi,
  denyPreviewNetwork,
  previewRefusal,
} from "./workspace-preview-api";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
beforeEach(() => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
    })),
  );
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
    setTimeout(() => callback(0), 0),
  );
  vi.stubGlobal("cancelAnimationFrame", clearTimeout);
});
describe("isolated illustrative workspace transport", () => {
  it("serves a cloned quality profile only for known fixture datasets", async () => {
    const api = createWorkspacePreviewApi();
    const path = "/api/datasets/Sample%20support/quality";
    const profile = await api.get<any>(path);
    expect(profile.count).toBe(3);
    profile.count = 999;
    expect((await api.get<any>(path)).count).toBe(3);
    await expect(api.get("/api/datasets/unknown/quality")).rejects.toThrow(
      "No server request was sent",
    );
    await expect(
      api.post("/api/datasets/Sample%20support/clean", {}),
    ).rejects.toThrow(previewRefusal);
  });
  it("matches the production Interpretability overview keys and clones its collections", async () => {
    const api = createWorkspacePreviewApi();
    const overview = await api.get<{
      captures: unknown[];
      comparisons: unknown[];
    }>("/api/interp");
    // services/interp.py:overview returns captures plus comparisons, never diffs.
    expect(overview).toEqual({ captures: [], comparisons: [] });
    overview.comparisons.push("changed caller copy");
    expect(await api.get("/api/interp")).toEqual({
      captures: [],
      comparisons: [],
    });
  });
  it("serves independent fixture copies and retains real bucket/pagination semantics", async () => {
    const api = createWorkspacePreviewApi();
    const datasets = await api.get<any[]>("/api/datasets");
    expect(datasets).toHaveLength(6);
    datasets[0].name = "Changed copy";
    expect((await api.get<any[]>("/api/datasets"))[0].name).toBe(
      "Sample support",
    );
    const page = await api.get<any>(
      "/api/datasets/Sample%20support?bucket=training&offset=1&limit=1",
    );
    expect(page.meta.buckets).toEqual({
      training: 2,
      validation: 1,
      uncategorized: 0,
    });
    expect(page.rows.map((row: any) => row.id)).toEqual([2]);
    expect(page.total).toBe(3);
  });
  it("refuses every mutation, binary request, arbitrary URL and unavailable read with no transport fallback", async () => {
    const api = createWorkspacePreviewApi();
    for (const request of [
      () => api.post("/api/jobs", {}),
      () => api.put("/api/settings", {}),
      () => api.patch("/api/users/user", {}),
      () => api.del("/api/datasets/Sample%20support"),
      () => api.upload("/api/datasets", new FormData()),
      () => api.download("/api/datasets/Sample%20support/download"),
      () => api.get("https://example.invalid/api/datasets"),
      () => denyPreviewNetwork("/api/p/illustrative-support/playground/chat"),
    ])
      await expect(request()).rejects.toThrow(previewRefusal);
    await expect(api.get("/api/model/execute")).rejects.toThrow(
      "No server request was sent",
    );
    const controller = new AbortController();
    controller.abort();
    await expect(
      api.get("/api/datasets", controller.signal),
    ).rejects.toMatchObject({ name: "AbortError" });
  });
});
describe("actual shell in the local illustrative preview", () => {
  it.each([
    ["/datasets", "Datasets"],
    ["/repositories", "Repositories"],
    ["/evaluations", "Evaluations"],
    ["/finetuning", "Finetuning jobs"],
    ["/finetuning/Sample%20support%20run", "Experiments"],
    ["/metrics", "Metrics"],
    ["/models", "Model inventory"],
    ["/traces", "Recent inference traces"],
    ["/projects", "Projects"],
    ["/api-keys", "Your API keys"],
    ["/users", "User accounts"],
    ["/lab", "Engine capabilities"],
    ["/stats", "Requests served per model"],
    ["/evaluations/Sample%20support%20evaluation", "Evaluation scores"],
  ])(
    "loads the actual %s screen with its allowlisted read fixtures",
    async (route, table) => {
      const network = vi.fn(async () => {
        throw Error("Unexpected network request");
      });
      const errors = vi.spyOn(console, "error");
      vi.stubGlobal("fetch", network);
      render(<WorkspacePreview initialRoute={route} />);
      if (route === "/lab") {
        fireEvent.click(
          await screen.findByRole("tab", { name: "Capabilities" }),
        );
      }
      expect(
        await screen.findByRole("table", { name: table }),
      ).toBeInTheDocument();
      await waitFor(() =>
        expect(screen.queryAllByText(/^Loading/)).toHaveLength(0),
      );
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(errors).not.toHaveBeenCalled();
      expect(network).not.toHaveBeenCalled();
      errors.mockRestore();
    },
  );
  it("loads the real Settings screen from the deployment settings and config envelopes", async () => {
    const network = vi.fn(async () => {
      throw Error("Unexpected network request");
    });
    vi.stubGlobal("fetch", network);
    render(<WorkspacePreview initialRoute="/settings" />);
    expect(
      await screen.findByRole("region", { name: "Deployment configuration" }),
    ).toBeInTheDocument();
    fireEvent.click(
      within(
        screen.getByRole("navigation", { name: "Settings sections" }),
      ).getByRole("button", { name: "Helper LLM" }),
    );
    expect(
      await screen.findByRole("textbox", { name: "Base URL" }),
    ).toHaveValue("https://illustrative.invalid/v1");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(network).not.toHaveBeenCalled();
  });
  it("renders Interpretability through the real preview route with the production overview envelope", async () => {
    const network = vi.fn(async () => {
      throw Error("Unexpected network request");
    });
    vi.stubGlobal("fetch", network);
    render(<WorkspacePreview initialRoute="/interp" />);
    expect(
      await screen.findByRole("table", { name: "Activation captures" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("table", { name: "Model comparisons" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Capture a model on a dataset to begin exploring its neurons.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Try this page again" }),
    ).not.toBeInTheDocument();
    expect(network).not.toHaveBeenCalled();
  });
  it("renders sample records, follows native detail links internally, and refuses export links", async () => {
    const network = vi.fn(async () => {
      throw Error("Unexpected network request");
    });
    vi.stubGlobal("fetch", network);
    window.location.hash = "#/__preview";
    render(<WorkspacePreview />);
    expect(screen.getByRole("note").textContent).toBe(
      "Illustrative preview · no server connection",
    );
    const datasets = await screen.findByRole("table", { name: "Datasets" });
    fireEvent.click(
      await within(datasets).findByRole("link", { name: "Sample support" }),
    );
    const records = await screen.findByRole("table", {
      name: "Dataset records",
    });
    expect(records.textContent).toContain("The customer cannot sign in");
    expect(window.location.hash).toBe("#/__preview");
    fireEvent.click(screen.getByRole("link", { name: "Download" }));
    expect(await screen.findByText(previewRefusal)).toBeTruthy();
    expect(window.location.hash).toBe("#/__preview");
    expect(network).not.toHaveBeenCalled();
  });
  it("uses real sidebar routing for training and models with illustrative evidence", async () => {
    const network = vi.fn(async () => {
      throw Error("Unexpected network request");
    });
    vi.stubGlobal("fetch", network);
    render(<WorkspacePreview />);
    fireEvent.click(
      within(
        screen.getByRole("navigation", { name: "Main navigation" }),
      ).getByRole("link", { name: "Fine tuning" }),
    );
    expect(
      await screen.findByRole("row", {
        name: "Training job Sample support run",
      }),
    ).toBeTruthy();
    fireEvent.click(
      within(
        screen.getByRole("navigation", { name: "Main navigation" }),
      ).getByRole("link", { name: "Models" }),
    );
    expect(
      await screen.findByRole("table", { name: "Model inventory" }),
    ).toBeTruthy();
    expect(network).not.toHaveBeenCalled();
  });
  it("refuses assistant persistence before a model request and keeps the stream transport isolated", async () => {
    const network = vi.fn(async () => {
      throw Error("Unexpected network request");
    });
    vi.stubGlobal("fetch", network);
    render(<WorkspacePreview />);
    fireEvent.click(screen.getByRole("button", { name: "Open assistant" }));
    const assistant = screen.getByRole("complementary", {
      name: "Workspace assistant",
    });
    const message = await within(assistant).findByRole("textbox", {
      name: "Message",
    });
    fireEvent.change(message, { target: { value: "Do not contact a model" } });
    fireEvent.click(within(assistant).getByRole("button", { name: "Send" }));
    expect(await within(assistant).findByText(previewRefusal)).toBeTruthy();
    expect(network).not.toHaveBeenCalled();
  });
  it("injects a refusing transport into the actual Playground stream controller", async () => {
    const network = vi.fn(async () => {
      throw Error("Unexpected network request");
    });
    vi.stubGlobal("fetch", network);
    render(<WorkspacePreview initialRoute="/playground" />);
    const message = await screen.findByRole("textbox", {
      name: "Message your model",
    });
    fireEvent.change(message, {
      target: { value: "This must never invoke a model" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText(previewRefusal)).toBeTruthy();
    expect(network).not.toHaveBeenCalled();
  });
});
