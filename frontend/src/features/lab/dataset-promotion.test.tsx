import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { DatasetPromotion } from "./dataset-promotion";
import { ComponentActionsProvider } from "../assistant/actions";
import { ComponentActionRegistry } from "../../controllers/componentActions";
import type { LabRun } from "./definition";

const run: LabRun = {
  id: "run-1",
  definition_id: "definition",
  state: "succeeded",
  result: {
    artifacts: [
      {
        id: "chat-id",
        name: "messages.jsonl",
        role: "supervised_dataset",
        visibility: "model",
      },
      { id: "numeric-id", name: "numeric.jsonl", role: "transformed_dataset" },
      {
        id: "truth-id",
        name: "secret.jsonl",
        role: "chat_dataset",
        visibility: "truth",
      },
    ],
  },
};
let post: ReturnType<typeof vi.fn>,
  get: ReturnType<typeof vi.fn>,
  changed: ReturnType<typeof vi.fn<() => void>>;
function view(scopeKey = "p1", currentRun = run) {
  const runtime = {
    api: { post, get },
    scopeKey,
    route: "/lab",
  } as unknown as StudioRuntime;
  return (
    <StudioContext.Provider value={runtime}>
      <DatasetPromotion run={currentRun} onOperationChanged={() => changed()} />
    </StudioContext.Provider>
  );
}
beforeEach(() => {
  post = vi.fn();
  get = vi.fn();
  changed = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
const fill = () =>
  fireEvent.change(screen.getByLabelText("Dataset name"), {
    target: { value: "retained-chats" },
  });

it("admits a durable promotion, polls its identity, and links the resulting dataset and snapshot without starting training", async () => {
  post.mockResolvedValue({ id: "op-1", kind: "data_bridge", state: "queued" });
  get.mockResolvedValue({
    id: "op-1",
    kind: "data_bridge",
    state: "succeeded",
    result: {
      dataset: { id: "d1", name: "retained-chats" },
      snapshot: {
        id: "s1",
        name: "frozen",
        training_count: 10,
        validation_count: 2,
      },
      promoted_rows: 12,
      excluded_final_test_rows: 3,
      excluded_truth_rows: 4,
    },
  });
  render(view());
  fill();
  fireEvent.change(screen.getByLabelText("Snapshot name (optional)"), {
    target: { value: "frozen" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create dataset" }));
  await screen.findByText(/Dataset promotion: queued/);
  expect(post).toHaveBeenCalledWith(
    "/api/lab/datasets/from-run",
    expect.objectContaining({
      run_id: "run-1",
      artifact_id: "chat-id",
      name: "retained-chats",
      snapshot_name: "frozen",
      min_training: 10,
      idempotency_key: expect.any(String),
    }),
    expect.any(AbortSignal),
  );
  await waitFor(
    () =>
      expect(
        screen.getByRole("link", { name: "Open dataset retained-chats" }),
      ).toHaveAttribute("href", "#/datasets/retained-chats"),
    { timeout: 2500 },
  );
  expect(get).toHaveBeenCalledWith(
    "/api/lab/operations/op-1",
    expect.any(AbortSignal),
  );
  expect(
    screen.getByText(
      /12 chats promoted; 3 final-test rows and 4 truth rows excluded/,
    ),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Open training/ })).toHaveAttribute(
    "href",
    "#/finetuning",
  );
  expect(post).toHaveBeenCalledTimes(1);
});

it("offers no promotion for numeric or protected truth artifacts", () => {
  render(
    view("p1", {
      ...run,
      result: { artifacts: (run.result as any).artifacts.slice(1) },
    }),
  );
  expect(
    screen.getByText(/No eligible retained chat artifacts/),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Create dataset" }),
  ).not.toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
});

it("deduplicates clicks synchronously and reuses an admission key after an ambiguous network failure", async () => {
  post.mockRejectedValue(new TypeError("Connection interrupted"));
  render(view());
  fill();
  const button = screen.getByRole("button", { name: "Create dataset" });
  act(() => {
    fireEvent.click(button);
    fireEvent.click(button);
  });
  await screen.findByRole("alert");
  expect(post).toHaveBeenCalledTimes(1);
  const firstKey = post.mock.calls[0][1].idempotency_key;
  fireEvent.click(screen.getByRole("button", { name: "Create dataset" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[1][1].idempotency_key).toBe(firstKey);
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Create dataset" }),
    ).toBeEnabled(),
  );
  fireEvent.change(screen.getByLabelText("Dataset name"), {
    target: { value: "another-name" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create dataset" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(3));
  expect(post.mock.calls[2][1].idempotency_key).not.toBe(firstKey);
});

it("aborts observation across scope changes and ignores an old admission response", async () => {
  let resolve!: (value: unknown) => void;
  post.mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const rendered = render(view());
  fill();
  fireEvent.click(screen.getByRole("button", { name: "Create dataset" }));
  const signal = post.mock.calls[0][2] as AbortSignal;
  rendered.rerender(view("p2"));
  expect(signal.aborted).toBe(true);
  await act(async () =>
    resolve({
      id: "old-operation",
      state: "succeeded",
      result: { dataset: { name: "stale" } },
    }),
  );
  expect(screen.queryByText(/old-operation/)).not.toBeInTheDocument();
  expect(screen.getByLabelText("Dataset name")).toHaveValue("");
  expect(changed).not.toHaveBeenCalled();
});

it("reconnects the same known operation after polling failure instead of publishing again", async () => {
  post.mockResolvedValue({
    id: "op-reconnect",
    state: "running",
    kind: "data_bridge",
  });
  get
    .mockRejectedValueOnce(new TypeError("Poll disconnected"))
    .mockResolvedValue({
      id: "op-reconnect",
      state: "succeeded",
      result: {
        dataset: { name: "retained-chats" },
        promoted_rows: 10,
        excluded_final_test_rows: 0,
        excluded_truth_rows: 0,
      },
    });
  render(view());
  fill();
  fireEvent.click(screen.getByRole("button", { name: "Create dataset" }));
  await screen.findByText("Poll disconnected", {}, { timeout: 2500 });
  fireEvent.click(screen.getByRole("button", { name: "Reconnect promotion" }));
  await screen.findByRole(
    "link",
    { name: "Open dataset retained-chats" },
    { timeout: 2500 },
  );
  expect(post).toHaveBeenCalledTimes(1);
  expect(get).toHaveBeenCalledTimes(2);
});

it("exposes promotion through the live component action registry with the same current form intent", async () => {
  const registry = new ComponentActionRegistry(async () => true);
  registry.setScope({ user: "owner", project: "p1", route: "/lab" });
  post.mockResolvedValue({
    id: "assistant-op",
    kind: "data_bridge",
    state: "succeeded",
    result: {
      dataset: { name: "retained-chats" },
      promoted_rows: 10,
      excluded_final_test_rows: 0,
      excluded_truth_rows: 0,
    },
  });
  render(
    <ComponentActionsProvider registry={registry}>
      {view()}
    </ComponentActionsProvider>,
  );
  expect(
    registry
      .inspect()
      .actions.find((action) => action.id === "lab.dataset-promote.run-1")
      ?.disabled,
  ).toBe(true);
  fill();
  const action = registry
    .inspect()
    .actions.find((action) => action.id === "lab.dataset-promote.run-1")!;
  expect(action.disabled).toBe(false);
  await act(async () => {
    await registry.execute(action.ref, undefined, true);
  });
  expect(post).toHaveBeenCalledWith(
    "/api/lab/datasets/from-run",
    expect.objectContaining({ artifact_id: "chat-id", name: "retained-chats" }),
    expect.any(AbortSignal),
  );
  expect(
    screen.getByRole("link", { name: "Open dataset retained-chats" }),
  ).toBeInTheDocument();
});
