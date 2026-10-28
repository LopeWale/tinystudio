import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { ComponentActionRegistry } from "../../controllers/componentActions";
import { ComponentActionsProvider } from "../assistant/actions";
import { JobPage } from "./training";
import userEvent from "@testing-library/user-event";
afterEach(cleanup);
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);
function mount(experiments: any[] = [], analysis: any = null) {
  let saved = experiments;
  const api = {
    get: vi.fn(async (path: string) =>
      path.startsWith("/api/jobs/")
        ? {
            job: { name: path.split("/").at(-1), snapshot: "snapshot" },
            snapshot: {
              dataset: "support",
              training_count: 80,
              validation_count: 20,
            },
            experiments: saved,
            analysis,
          }
        : path === "/api/models"
          ? []
          : { models: [] },
    ),
    post: vi.fn(async (path: string, body: any) => {
      if (path.endsWith("/experiments"))
        saved = [
          ...saved,
          ...body.experiments.map((config: any, index: number) => ({
            number: saved.length + index + 1,
            state: "pending",
            config,
          })),
        ];
      return { id: "task" };
    }),
  };
  const s = {
    api,
    project: "fixture",
    scopeKey: "u:fixture",
    user: { id: "u" },
    route: "/finetuning/first",
    evidence: new EvidenceRegistry(),
    overview: { catalog: [{ id: "fixture/model" }], tree: { datasets: [] } },
    toast: vi.fn(),
    confirm: vi.fn(async () => true),
    trackTask: vi.fn(),
    showTasks: vi.fn(),
    navigate: vi.fn(),
  } as any;
  const registry = new ComponentActionRegistry();
  registry.setScope({
    user: "u",
    project: "fixture",
    route: "/finetuning/first",
  });
  const router = createMemoryRouter(
    [{ path: "/finetuning/:job", element: <JobPage /> }],
    { initialEntries: ["/finetuning/first"] },
  );
  render(
    <StudioContext.Provider value={s}>
      <ComponentActionsProvider registry={registry}>
        <RouterProvider router={router} />
      </ComponentActionsProvider>
    </StudioContext.Provider>,
  );
  return { api, s, registry, router };
}
async function openDraft() {
  fireEvent.click(
    await screen.findByRole("button", { name: "Configure experiments" }),
  );
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Add experiment",
    }),
  );
}
describe("focused training experiment setup", () => {
  it("retains one editor’s edits across draft selection, review/back, and close", async () => {
    mount();
    await openDraft();
    fireEvent.change(screen.getByLabelText("Base model"), {
      target: { value: "custom/model" },
    });
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Add experiment",
      }),
    );
    expect(screen.getAllByLabelText("Base model")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: /Draft 1/ }));
    expect(screen.getByLabelText("Base model")).toHaveValue("custom/model");
    fireEvent.click(screen.getByRole("button", { name: "Review experiments" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Back to configuration" }),
    );
    expect(screen.getByLabelText("Base model")).toHaveValue("custom/model");
    fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Configure experiments · 2 drafts" }),
    );
    expect(screen.getByLabelText("Base model")).toHaveValue("custom/model");
  });
  it("prevents invalid numeric drafts reaching the API and saves the exact valid scientific payload without launching", async () => {
    const { api } = mount();
    await openDraft();
    fireEvent.change(screen.getByLabelText("Learning rate"), {
      target: { value: "0" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review experiments" }));
    expect(screen.getByRole("alert")).toHaveTextContent("greater than 0");
    expect(api.post).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Learning rate"), {
      target: { value: "0.000005" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review experiments" }));
    fireEvent.click(screen.getByRole("button", { name: "Save experiments" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(api.post.mock.calls).toEqual([
      [
        "/api/jobs/first/experiments",
        {
          experiments: [
            {
              base_model: "fixture/model",
              training_type: "lora",
              epochs: 3,
              learning_rate: 0.000005,
              batch_size: 8,
              gradient_accumulation_steps: 2,
              max_length: 512,
              lora_r: 16,
              lora_alpha: 32,
              assistant_only_loss: false,
              refinetune_from: null,
              checkpoint_every_steps: 0,
              keep_checkpoints: 0,
            },
          ],
        },
      ],
    ]);
  });
  it("launches existing pending experiments without saving duplicate configurations", async () => {
    const { api } = mount([
      {
        number: 7,
        state: "pending",
        config: { base_model: "saved/model", training_type: "lora", epochs: 2 },
      },
    ]);
    fireEvent.click(
      await screen.findByRole("button", { name: "Review & start" }),
    );
    expect(
      screen.getByText("Saved experiment 7 · pending"),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", {
        name: /(?:Save & start|Start saved) experiments/,
      }),
    );
    await waitFor(() =>
      expect(api.post.mock.calls).toEqual([["/api/jobs/first/start", {}]]),
    );
  });
  it("retries a failed start using already saved pending experiments", async () => {
    const { api } = mount();
    await openDraft();
    const original = api.post.getMockImplementation()!;
    let fail = true;
    api.post.mockImplementation(async (path, body) => {
      if (path.endsWith("/start") && fail) {
        fail = false;
        throw Error("Queue unavailable");
      }
      return original(path, body);
    });
    fireEvent.click(screen.getByRole("button", { name: "Review experiments" }));
    fireEvent.click(
      screen.getByRole("button", {
        name: /(?:Save & start|Start saved) experiments/,
      }),
    );
    await within(screen.getByRole("dialog")).findByRole("alert");
    expect(
      screen.getByText("Saved experiment 1 · pending"),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", {
        name: /(?:Save & start|Start saved) experiments/,
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(api.post.mock.calls.map(([path]) => path)).toEqual([
      "/api/jobs/first/experiments",
      "/api/jobs/first/start",
      "/api/jobs/first/start",
    ]);
  });
  it("does not start an old job after its save resolves in a newer route", async () => {
    const { api, router } = mount();
    await openDraft();
    let resolve!: (value: any) => void;
    api.post.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Review experiments" }));
    fireEvent.click(
      screen.getByRole("button", {
        name: /(?:Save & start|Start saved) experiments/,
      }),
    );
    await act(async () => {
      await router.navigate("/finetuning/second");
    });
    await act(async () => resolve({ experiments: [1] }));
    await screen.findByRole("heading", { name: "second" });
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("keeps admission locked when evidence refresh fails after a successful start", async () => {
    const { api } = mount([
      {
        number: 1,
        state: "pending",
        config: { base_model: "saved/model", training_type: "lora", epochs: 2 },
      },
    ]);
    fireEvent.click(
      await screen.findByRole("button", { name: "Review & start" }),
    );
    api.get.mockRejectedValue(Error("Refresh offline"));
    fireEvent.click(
      screen.getByRole("button", { name: "Start saved experiments" }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await screen.findByText("Task submitted");
    expect(screen.queryByRole("button", { name: "Review & start" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Configure experiments" }),
    ).toBeNull();
    expect(api.post.mock.calls).toEqual([["/api/jobs/first/start", {}]]);
  });
  it("allows dismissal while saving and preserves a reopened dialog when that save completes", async () => {
    const { api } = mount();
    await openDraft();
    let resolve!: (value: any) => void;
    api.post.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Review experiments" }));
    fireEvent.click(screen.getByRole("button", { name: "Save experiments" }));
    fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "Configure experiments · 1 drafts" }),
    );
    expect(screen.getByLabelText("Base model")).toBeDisabled();
    await act(async () => resolve({ experiments: [1] }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByLabelText("Base model")).toBeNull();
    expect(api.post).toHaveBeenCalledTimes(1);
  });
  it("blocks every add entrypoint while a dismissed save is pending", async () => {
    const { api } = mount([], {
      recommendation: {
        recommended: { base_model: "recommended/model", training_type: "lora" },
      },
    });
    await openDraft();
    let resolve!: (value: any) => void;
    api.post.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Review experiments" }));
    fireEvent.click(screen.getByRole("button", { name: "Save experiments" }));
    fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
    const add = screen.getByRole("button", { name: "Add experiment" });
    expect(add).toBeDisabled();
    fireEvent.click(add);
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "Configure experiments · 1 drafts" }),
    );
    expect(
      screen.getByRole("button", { name: "Add experiment" }),
    ).toBeDisabled();
    const recommended = screen.getByRole("button", {
      name: "Add recommended experiment",
    });
    expect(recommended).toBeDisabled();
    fireEvent.click(recommended);
    await act(async () => resolve({ experiments: [1] }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Draft 2/ })).toBeNull();
    expect(api.post).toHaveBeenCalledTimes(1);
  });
  it("repairs a retained invalid adapter value directly after switching to full training", async () => {
    const { api } = mount();
    await openDraft();
    const user = userEvent.setup();
    fireEvent.change(screen.getByLabelText("LoRA rank"), {
      target: { value: "" },
    });
    await user.click(screen.getByRole("button", { name: /Training type/ }));
    await user.click(screen.getByRole("option", { name: "full" }));
    fireEvent.click(screen.getByRole("button", { name: "Review experiments" }));
    expect(screen.getByRole("alert")).toHaveTextContent("LoRA rank");
    expect(api.post).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("LoRA rank"), {
      target: { value: "32" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review experiments" }));
    fireEvent.click(screen.getByRole("button", { name: "Save experiments" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledOnce());
    expect(api.post.mock.calls[0]).toEqual([
      "/api/jobs/first/experiments",
      {
        experiments: [
          {
            base_model: "fixture/model",
            training_type: "full",
            epochs: 3,
            learning_rate: 0.00005,
            batch_size: 8,
            gradient_accumulation_steps: 2,
            max_length: 512,
            lora_r: 32,
            lora_alpha: 32,
            assistant_only_loss: false,
            refinetune_from: null,
            checkpoint_every_steps: 0,
            keep_checkpoints: 0,
          },
        ],
      },
    ]);
  });
  it("keeps modal heading/footer outside its scroll body and fades only after scrolling", async () => {
    mount();
    await openDraft();
    const dialog = screen.getByRole("dialog");
    const scroller = dialog.querySelector(".training-job-scroll-body")!;
    const fade = dialog.querySelector(".training-scroll-fade")!;
    const layers = fade.querySelectorAll("span");
    expect(
      dialog.querySelector(".modal-content > .training-job-scroll"),
    ).toBeTruthy();
    expect(fade).toHaveAttribute("aria-hidden", "true");
    expect(scroller.contains(dialog.querySelector("header"))).toBe(false);
    expect(scroller.contains(dialog.querySelector("footer"))).toBe(false);
    for (const layer of layers) expect(layer).toHaveStyle({ opacity: "0" });
    fireEvent.scroll(scroller, { target: { scrollTop: 12 } });
    for (const layer of layers) expect(layer).toHaveStyle({ opacity: "0.5" });
    fireEvent.scroll(scroller, { target: { scrollTop: 24 } });
    for (const layer of layers) expect(layer).toHaveStyle({ opacity: "1" });
    fireEvent.scroll(scroller, { target: { scrollTop: 0 } });
    for (const layer of layers) expect(layer).toHaveStyle({ opacity: "0" });
  });
  it("hides start controls for an already admitted pending training task", async () => {
    mount([
      {
        number: 7,
        state: "pending",
        task_id: "queued-task",
        config: { base_model: "saved/model", training_type: "lora", epochs: 2 },
      },
    ]);
    await screen.findByText("Task submitted");
    expect(screen.queryByRole("button", { name: "Review & start" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Configure experiments" }),
    ).toBeNull();
  });
  it("retains curve controls and revokes cached assistant selection actions when their pane hides", async () => {
    const { registry } = mount([
      {
        number: 1,
        state: "succeeded",
        config: { base_model: "saved/model", training_type: "lora", epochs: 2 },
        loss: [{ step: 1, loss: 1 }],
      },
    ]);
    await screen.findByRole("button", { name: "Experiment 1" });
    const cached = registry
      .inspect()
      .actions.find((action) => action.id === "job.experiment")!;
    expect(cached).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Training curves" }));
    await expect(registry.execute(cached.ref, "1", false)).rejects.toThrow(
      "stale",
    );
    expect(screen.queryByRole("button", { name: "Copy" })).toBeNull();
    const showEval = screen.getByRole("checkbox", {
      name: "Show evaluation loss",
    });
    fireEvent.click(showEval);
    expect(showEval).not.toBeChecked();
    fireEvent.click(screen.getByRole("tab", { name: "Dataset analysis" }));
    fireEvent.click(screen.getByRole("tab", { name: "Training curves" }));
    expect(
      screen.getByRole("checkbox", { name: "Show evaluation loss" }),
    ).not.toBeChecked();
    fireEvent.click(screen.getByRole("tab", { name: "Experiments" }));
    expect(
      registry
        .inspect()
        .actions.find((action) => action.id === "job.experiment")?.ref,
    ).not.toBe(cached.ref);
  });
});
