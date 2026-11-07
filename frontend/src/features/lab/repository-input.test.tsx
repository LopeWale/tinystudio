import { useState } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { ComponentActionRegistry } from "../../controllers/componentActions";
import { ComponentActionsProvider } from "../assistant/actions";
import { RepositoryInput } from "./repository-input";
import { makeDefinition, type InputBinding } from "./definition";
import { InputArtifacts } from "./inputs";

let post: ReturnType<typeof vi.fn<(...args: any[]) => Promise<any>>>,
  get: ReturnType<typeof vi.fn<(...args: any[]) => Promise<any>>>,
  captured: ReturnType<typeof vi.fn<() => void>>,
  changed: ReturnType<typeof vi.fn<(inputs: InputBinding[]) => void>>;
let parent: AbortController;
const binding: InputBinding = {
  artifact_id: "corpus-id",
  sha256: "a".repeat(64),
  role: "corpus",
  visibility: "evaluation",
};
const succeeded = {
  id: "capture-op",
  kind: "data_bridge",
  state: "succeeded",
  result: {
    inputs: [binding],
    corpus: { id: "corpus-id", sha256: "a".repeat(64) },
    vectors: { id: "vectors-id", sha256: "b".repeat(64) },
    manifest: { id: "manifest-id", sha256: "c".repeat(64) },
  },
};
const queued = { id: "capture-op", kind: "data_bridge", state: "queued" };
function Harness({
  initial = [],
  active = true,
}: {
  initial?: InputBinding[];
  active?: boolean;
}) {
  const [inputs, setInputs] = useState(initial);
  return (
    <>
      <RepositoryInput
        active={active}
        inputs={inputs}
        onChange={(next) => {
          changed(next);
          setInputs(next);
        }}
        onCaptured={captured}
        signal={() => parent.signal}
      />
      <button
        onClick={() =>
          setInputs((current) => [
            ...current,
            {
              artifact_id: "manual-edit",
              sha256: "manual-hash",
              role: "source",
              visibility: "truth",
            },
          ])
        }
      >
        Edit other inputs
      </button>
      <output data-testid="inputs">{JSON.stringify(inputs)}</output>
    </>
  );
}
function view(
  scopeKey = "alpha",
  initial: InputBinding[] = [],
  registry?: ComponentActionRegistry,
  active = true,
) {
  const runtime = {
    api: { post, get },
    scopeKey,
    route: "/lab",
    project: scopeKey,
    user: { id: "user" },
    evidence: new EvidenceRegistry(),
  } as unknown as StudioRuntime;
  return (
    <StudioContext.Provider value={runtime}>
      <ComponentActionsProvider
        registry={registry ?? new ComponentActionRegistry()}
      >
        <Harness key={scopeKey} initial={initial} active={active} />
      </ComponentActionsProvider>
    </StudioContext.Provider>
  );
}
beforeEach(() => {
  post = vi.fn().mockResolvedValue(queued);
  get = vi.fn(async (path: string) =>
    path === "/api/repositories" ? [{ name: "engineering" }] : succeeded,
  );
  captured = vi.fn();
  changed = vi.fn();
  parent = new AbortController();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
async function choose() {
  await userEvent.click(
    screen.getByText("Capture an existing repository", {
      selector: "summary span",
    }),
  );
  const trigger = await screen.findByRole("button", {
    name: /Repository to capture$/,
  });
  await waitFor(() =>
    expect(get).toHaveBeenCalledWith(
      "/api/repositories",
      expect.any(AbortSignal),
    ),
  );
  await userEvent.click(trigger);
  await userEvent.click(
    await screen.findByRole("option", { name: "engineering" }),
  );
}
async function capture() {
  await userEvent.click(
    screen.getByRole("button", { name: "Capture and attach repository" }),
  );
}

it("admits 202 work, polls the durable identity and attaches only returned corpus IDs and hashes", async () => {
  render(view());
  await choose();
  await capture();
  expect(post).toHaveBeenCalledWith(
    "/api/lab/inputs/from-repository",
    { repository: "engineering", idempotency_key: expect.any(String) },
    expect.any(AbortSignal),
  );
  await waitFor(() => expect(changed).toHaveBeenCalledWith([binding]), {
    timeout: 2500,
  });
  expect(get).toHaveBeenCalledWith(
    "/api/lab/operations/capture-op",
    expect.any(AbortSignal),
  );
  expect(captured).toHaveBeenCalledTimes(1);
  expect(screen.getByTestId("inputs")).not.toHaveTextContent("vectors-id");
  expect(
    screen.getByText(/optimistically validated capture/),
  ).toHaveTextContent("encoder revision is unknown");
  expect(
    screen.getByText(/optimistically validated capture/),
  ).toHaveTextContent("semantic reproducibility are unqualified");
});

it("keeps edits made during capture and suppresses duplicates while preserving more restrictive visibility", async () => {
  const protectedCorpus = { ...binding, visibility: "truth" as const };
  render(view("alpha", [protectedCorpus]));
  await choose();
  await capture();
  fireEvent.click(screen.getByRole("button", { name: "Edit other inputs" }));
  await waitFor(() => expect(changed).toHaveBeenCalled(), { timeout: 2500 });
  expect(changed).toHaveBeenLastCalledWith([
    protectedCorpus,
    {
      artifact_id: "manual-edit",
      sha256: "manual-hash",
      role: "source",
      visibility: "truth",
    },
  ]);
});

it("reuses the admission key after an ambiguous network failure", async () => {
  post.mockRejectedValueOnce(new Error("Connection lost after admission"));
  render(view());
  await choose();
  await capture();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Connection lost after admission",
  );
  await capture();
  expect(post.mock.calls[1][1]).toEqual(post.mock.calls[0][1]);
  await waitFor(() => expect(changed).toHaveBeenCalledWith([binding]), {
    timeout: 2500,
  });
});

it("does not attach when the original scope leaves while admission is pending", async () => {
  let resolve!: (value: unknown) => void;
  post.mockImplementation(
    () =>
      new Promise((result) => {
        resolve = result;
      }),
  );
  const mounted = render(view());
  await choose();
  await capture();
  const requestSignal = post.mock.calls[0][2] as AbortSignal;
  mounted.rerender(view("beta"));
  expect(requestSignal.aborted).toBe(true);
  await act(async () => resolve(succeeded));
  expect(changed).not.toHaveBeenCalled();
  expect(captured).not.toHaveBeenCalled();
});

it("aborts obsolete observation when the definition lifetime ends", async () => {
  render(view());
  await choose();
  await capture();
  parent.abort();
  expect((post.mock.calls[0][2] as AbortSignal).aborted).toBe(true);
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 1300));
  });
  expect(changed).not.toHaveBeenCalled();
});

it("reconnects a retained operation after polling fails without admitting another capture", async () => {
  let polls = 0;
  get.mockImplementation(async (path: string) => {
    if (path === "/api/repositories") return [{ name: "engineering" }];
    if (polls++ === 0) throw new Error("Observation disconnected");
    return succeeded;
  });
  render(view());
  await choose();
  await capture();
  expect(
    await screen.findByRole("alert", {}, { timeout: 2500 }),
  ).toHaveTextContent("Observation disconnected");
  await userEvent.click(
    screen.getByRole("button", { name: "Reconnect capture" }),
  );
  await waitFor(() => expect(changed).toHaveBeenCalledWith([binding]), {
    timeout: 2500,
  });
  expect(post).toHaveBeenCalledTimes(1);
});

it("registers a scoped mutation action using the current selection and operation", async () => {
  const registry = new ComponentActionRegistry();
  registry.setScope({ user: "user", project: "alpha", route: "/lab" });
  render(view("alpha", [], registry));
  await choose();
  expect(registry.inspect().actions).toEqual([
    expect.objectContaining({
      id: "lab.repository-capture",
      kind: "mutate",
      disabled: false,
      state: { repository: "engineering" },
    }),
  ]);
  await capture();
  expect(registry.inspect().actions[0]).toMatchObject({
    disabled: true,
    state: {
      repository: "engineering",
      operation_id: "capture-op",
      operation_state: "queued",
    },
  });
});

it("reloads captured corpus into the real retained input picker without a manual upload", async () => {
  let corpusPublished = false;
  get.mockImplementation(async (path: string) => {
    if (path === "/api/repositories") return [{ name: "engineering" }];
    if (path === "/api/lab/inputs")
      return corpusPublished
        ? [
            {
              id: binding.artifact_id,
              sha256: binding.sha256,
              name: "captured-corpus.jsonl",
              size: 12,
              media_type: "application/x-ndjson",
            },
          ]
        : [];
    corpusPublished = true;
    return succeeded;
  });
  function ActualPicker() {
    const [definition, setDefinition] = useState(makeDefinition());
    return (
      <InputArtifacts
        definition={definition}
        onChange={(inputs) =>
          setDefinition((current) => ({ ...current, inputs }))
        }
        signal={() => parent.signal}
      />
    );
  }
  const runtime = {
    api: { post, get },
    scopeKey: "alpha",
    route: "/lab",
    project: "alpha",
    user: { id: "user" },
    evidence: new EvidenceRegistry(),
  } as unknown as StudioRuntime;
  render(
    <StudioContext.Provider value={runtime}>
      <ActualPicker />
    </StudioContext.Provider>,
  );
  await choose();
  await capture();
  expect(
    await screen.findByRole(
      "button",
      { name: "Detach captured-corpus.jsonl" },
      { timeout: 2500 },
    ),
  ).toBeInTheDocument();
  expect(
    get.mock.calls.filter(([path]) => path === "/api/lab/inputs"),
  ).toHaveLength(2);
  expect(
    screen.getByRole("button", { name: "Import and attach input" }),
  ).toBeDisabled();
});

it("refuses invalid capture result bindings instead of attaching an artifact inferred from the manifest", async () => {
  get.mockImplementation(async (path: string) =>
    path === "/api/repositories"
      ? [{ name: "engineering" }]
      : {
          ...succeeded,
          result: {
            ...succeeded.result,
            inputs: [{ ...binding, role: "repository_vectors" }],
          },
        },
  );
  render(view());
  await choose();
  await capture();
  expect(
    await screen.findByRole("alert", {}, { timeout: 2500 }),
  ).toHaveTextContent("invalid corpus input bindings");
  expect(changed).not.toHaveBeenCalled();
  expect(captured).not.toHaveBeenCalled();
});

it("disables repository actions while its retained pane is hidden and restores its selection on return", async () => {
  const registry = new ComponentActionRegistry(async () => true);
  registry.setScope({ user: "user", project: "alpha", route: "/lab" });
  const mounted = render(view("alpha", [], registry));
  await choose();
  const ref = registry.inspect().actions[0].ref;
  mounted.rerender(view("alpha", [], registry, false));
  expect(registry.inspect().actions[0]).toMatchObject({
    disabled: true,
    state: { repository: "engineering" },
  });
  await expect(registry.execute(ref, undefined, true)).rejects.toThrow(
    "currently disabled",
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Capture and attach repository" }),
  );
  expect(post).not.toHaveBeenCalled();
  mounted.rerender(view("alpha", [], registry, true));
  await waitFor(() =>
    expect(registry.inspect().actions[0]).toMatchObject({
      disabled: false,
      state: { repository: "engineering" },
    }),
  );
  post.mockResolvedValue(succeeded);
  await act(async () => {
    await registry.execute(ref, undefined, true);
  });
  expect(changed).toHaveBeenCalledWith([binding]);
});

it("does not admit repository capture if its pane hides while assistant confirmation is pending", async () => {
  let confirm!: (value: boolean) => void;
  const registry = new ComponentActionRegistry(
    () =>
      new Promise((resolve) => {
        confirm = resolve;
      }),
  );
  registry.setScope({ user: "user", project: "alpha", route: "/lab" });
  const mounted = render(view("alpha", [], registry));
  await choose();
  const pending = registry.execute(
    registry.inspect().actions[0].ref,
    undefined,
    true,
  );
  const rejected = expect(pending).rejects.toThrow(
    "changed while waiting for confirmation",
  );
  mounted.rerender(view("alpha", [], registry, false));
  await act(async () => {
    confirm(true);
    await rejected;
  });
  expect(post).not.toHaveBeenCalled();
  expect(changed).not.toHaveBeenCalled();
});

it("does not attach a late capture result after the pane hides", async () => {
  let finish!: (value: unknown) => void;
  post.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const mounted = render(view());
  await choose();
  await capture();
  const requestSignal = post.mock.calls[0][2] as AbortSignal;
  mounted.rerender(view("alpha", [], undefined, false));
  expect(requestSignal.aborted).toBe(true);
  await act(async () => finish(succeeded));
  expect(changed).not.toHaveBeenCalled();
  expect(captured).not.toHaveBeenCalled();
});

it("tracks the mobile import branch and viewport without clearing repository selection", async () => {
  let narrow = true;
  const listeners = new Set<() => void>();
  vi.stubGlobal("matchMedia", () => ({
    get matches() {
      return narrow;
    },
    addEventListener: (_: string, listener: () => void) =>
      listeners.add(listener),
    removeEventListener: (_: string, listener: () => void) =>
      listeners.delete(listener),
  }));
  const registry = new ComponentActionRegistry(async () => true);
  registry.setScope({ user: "user", project: "alpha", route: "/lab" });
  const runtime = {
    api: {
      post,
      get: vi.fn(async (path: string) =>
        path === "/api/repositories" ? [{ name: "engineering" }] : [],
      ),
    },
    scopeKey: "alpha",
    route: "/lab",
    project: "alpha",
    user: { id: "user" },
    evidence: new EvidenceRegistry(),
  } as unknown as StudioRuntime;
  render(
    <StudioContext.Provider value={runtime}>
      <ComponentActionsProvider registry={registry}>
        <InputArtifacts
          definition={makeDefinition()}
          onChange={changed}
          signal={() => parent.signal}
        />
      </ComponentActionsProvider>
    </StudioContext.Provider>,
  );
  expect(registry.inspect().actions[0].disabled).toBe(true);
  fireEvent.click(screen.getByRole("radio", { name: "Import input" }));
  // Use this fixture's repository request rather than the standalone harness spy.
  await userEvent.click(
    screen.getByText("Capture an existing repository", {
      selector: "summary span",
    }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: /Repository to capture$/ }),
  );
  await userEvent.click(
    await screen.findByRole("option", { name: "engineering" }),
  );
  expect(registry.inspect().actions[0].disabled).toBe(false);
  fireEvent.click(screen.getByRole("radio", { name: "Input library" }));
  expect(registry.inspect().actions[0].disabled).toBe(true);
  await expect(
    registry.execute(registry.inspect().actions[0].ref, undefined, true),
  ).rejects.toThrow("currently disabled");
  expect(post).not.toHaveBeenCalled();
  act(() => {
    narrow = false;
    listeners.forEach((listener) => listener());
  });
  expect(registry.inspect().actions[0]).toMatchObject({
    disabled: false,
    state: { repository: "engineering" },
  });
  act(() => {
    narrow = true;
    listeners.forEach((listener) => listener());
  });
  expect(registry.inspect().actions[0].disabled).toBe(true);
});
