import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { KeysPage } from "./keys";

beforeEach(() =>
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  ),
);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
function setup() {
  const api = {
    get: vi.fn(async () => [
      { id: "k1", name: "client", prefix: "ts-example" },
    ]),
    post: vi.fn(),
    del: vi.fn(async () => ({})),
  };
  const runtime = {
    api,
    project: "alpha",
    scopeKey: "self:alpha",
    user: { id: "self", email: "owner@example.test", role: "admin" },
    route: "/keys",
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
    confirm: vi.fn(),
  } as any;
  const tree = (s: any) => (
    <StudioContext.Provider value={s}>
      <MemoryRouter>
        <KeysPage />
      </MemoryRouter>
    </StudioContext.Provider>
  );
  const view = render(tree(runtime));
  return {
    api,
    runtime,
    change: (patch: any) => view.rerender(tree({ ...runtime, ...patch })),
  };
}
function create(name = "integration") {
  fireEvent.click(screen.getByRole("button", { name: "Create API key" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: name },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
}
it("ignores a revoke confirmation accepted after leaving its scope", async () => {
  const { api, runtime, change } = setup();
  const answer = deferred<boolean>();
  runtime.confirm.mockReturnValue(answer.promise);
  fireEvent.click(await screen.findByRole("button", { name: "Revoke client" }));
  change({ project: "beta", scopeKey: "self:beta" });
  await act(async () => answer.resolve(true));
  expect(api.del).not.toHaveBeenCalled();
});
it("never reveals a deferred key into a different account or a later return to the old scope", async () => {
  const { api, change } = setup();
  const response = deferred<any>();
  api.post.mockReturnValue(response.promise);
  create();
  await waitFor(() => expect(api.post).toHaveBeenCalledOnce());
  change({ user: { id: "other", role: "admin" }, scopeKey: "other:alpha" });
  change({ scopeKey: "self:alpha" });
  await act(async () => response.resolve({ key: "ts-late-private" }));
  expect(screen.queryByText("ts-late-private")).toBeNull();
  expect(screen.queryByRole("dialog", { name: "Your new API key" })).toBeNull();
});
it("retains a dismissed pending creation, blocks duplicate requests and preserves a newer draft", async () => {
  const { api } = setup();
  const response = deferred<any>();
  api.post.mockReturnValue(response.promise);
  create("first");
  await waitFor(() => expect(api.post).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
  fireEvent.click(screen.getByRole("button", { name: "Create API key" }));
  const input = screen.getByRole("textbox", {
    name: "Name",
  }) as HTMLInputElement;
  expect(input.value).toBe("first");
  fireEvent.change(input, { target: { value: "next integration" } });
  const pending = screen.getByRole("button", {
    name: "Working…",
  }) as HTMLButtonElement;
  expect(pending.disabled).toBe(true);
  fireEvent.click(pending);
  expect(api.post).toHaveBeenCalledOnce();
  await act(async () => response.resolve({ key: "ts-retained-once" }));
  expect(await screen.findByText("ts-retained-once")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
  fireEvent.click(screen.getByRole("button", { name: "Create API key" }));
  expect(
    (screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value,
  ).toBe("next integration");
});
it("keeps SDK guidance contextual to the heading and the collection dominant", async () => {
  setup();
  await screen.findByRole("button", { name: "Revoke client" });
  const table = screen.getByRole("table", { name: "Your API keys" });
  expect(table.closest(".data-table")?.getAttribute("data-table-layout")).toBe(
    "fill",
  );
  expect(
    screen.queryByText("Send it as a Bearer token with each request."),
  ).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Using your key" }));
  expect(screen.getByRole("dialog", { name: "Using your key" })).toBeTruthy();
});
it("preserves the named draft after server failure and allows a deliberate retry", async () => {
  const { api } = setup();
  api.post
    .mockRejectedValueOnce(Error("Service unavailable"))
    .mockResolvedValueOnce({ key: "ts-retry-once" });
  create("retry client");
  await screen.findByText("Service unavailable");
  expect(screen.getAllByText("Service unavailable")).toHaveLength(1);
  expect(
    (screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value,
  ).toBe("retry client");
  expect(screen.queryByRole("dialog", { name: "Your new API key" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  expect(await screen.findByText("ts-retry-once")).toBeTruthy();
  expect(api.post).toHaveBeenCalledTimes(2);
  expect(api.post).toHaveBeenLastCalledWith("/api/keys", {
    name: "retry client",
  });
});

it("does not act on a confirmation from a route that has been abandoned", async () => {
  const { api, runtime, change } = setup();
  const answer = deferred<boolean>();
  runtime.confirm.mockReturnValue(answer.promise);
  fireEvent.click(await screen.findByRole("button", { name: "Revoke client" }));
  change({ route: "/datasets" });
  change({ route: "/keys" });
  await act(async () => answer.resolve(true));
  expect(api.del).not.toHaveBeenCalled();
});
it("offers recovery when the key directory fails to load", async () => {
  const { api, change } = setup();
  await screen.findByRole("button", { name: "Revoke client" });
  api.get.mockRejectedValueOnce(Error("Directory unavailable"));
  change({ scopeKey: "self:retry" });
  fireEvent.click(
    await screen.findByRole("button", { name: "Retry API keys" }),
  );
  await screen.findByRole("button", { name: "Revoke client" });
});
