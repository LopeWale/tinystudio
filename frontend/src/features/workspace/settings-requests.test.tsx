import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { SettingsPage } from "./settings";

afterEach(cleanup);
const specs = [
  {
    key: "KEY",
    label: "Provider key",
    group: "openai",
    secret: true,
    set: true,
    source: "database",
  },
  {
    key: "MODEL",
    label: "Helper model",
    group: "helper",
    secret: false,
    set: true,
    source: "database",
    value: "original",
  },
];
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function setup(put = vi.fn(async () => ({})), post = vi.fn(async () => ({}))) {
  const api = {
    get: vi.fn(async (path: string) =>
      path === "/api/settings"
        ? { settings: specs }
        : { inference: { mode: "local" } },
    ),
    put,
    post,
  };
  const studio = {
    api,
    scopeKey: "self:first",
    project: "first",
    user: { id: "self", role: "admin" },
    route: "/settings",
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
    refreshOverview: vi.fn(async () => {}),
  } as any;
  const tree = (s = studio) => (
    <StudioContext.Provider value={s}>
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    </StudioContext.Provider>
  );
  const view = render(tree());
  return { api, studio, tree, view };
}
it("retains newer edits and the selected provider after a pending save completes", async () => {
  const pending = deferred<any>();
  const { api } = setup(vi.fn(() => pending.promise));
  fireEvent.change(
    await screen.findByLabelText("Provider key", { selector: "input" }),
    {
      target: { value: "submitted" },
    },
  );
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  fireEvent.change(
    screen.getByLabelText("Provider key", { selector: "input" }),
    {
      target: { value: "newer-draft" },
    },
  );
  fireEvent.click(screen.getByRole("button", { name: /Helper LLM/ }));
  await act(async () => pending.resolve({ settings: specs }));
  await screen.findByLabelText("Helper model", { selector: "input" });
  expect(
    screen.queryByLabelText("Provider key", { selector: "input" }),
  ).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /OpenAI \(assistant\)/ }));
  expect(
    (
      screen.getByLabelText("Provider key", {
        selector: "input",
      }) as HTMLInputElement
    ).value,
  ).toBe("newer-draft");
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(api.put).toHaveBeenLastCalledWith("/api/settings", {
      values: { KEY: "newer-draft" },
    }),
  );
});
it.each(["resolve", "reject"] as const)(
  "ignores a late save %s after the scope changes",
  async (outcome) => {
    const pending = deferred<any>();
    const { studio, tree, view } = setup(vi.fn(() => pending.promise));
    fireEvent.change(
      await screen.findByLabelText("Provider key", { selector: "input" }),
      {
        target: { value: "old-scope" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    view.rerender(
      tree({ ...studio, scopeKey: "self:second", project: "second" }),
    );
    fireEvent.change(
      await screen.findByLabelText("Provider key", { selector: "input" }),
      {
        target: { value: "new-scope" },
      },
    );
    await act(async () => {
      if (outcome === "resolve") pending.resolve({ settings: specs });
      else pending.reject(Error("Old save failed"));
    });
    expect(
      (
        screen.getByLabelText("Provider key", {
          selector: "input",
        }) as HTMLInputElement
      ).value,
    ).toBe("new-scope");
    expect(studio.toast).not.toHaveBeenCalled();
    expect(studio.refreshOverview).not.toHaveBeenCalled();
    expect(screen.queryByText("Old save failed")).toBeNull();
  },
);
it("ignores late save completion after unmount", async () => {
  const pending = deferred<any>();
  const { studio, view } = setup(vi.fn(() => pending.promise));
  fireEvent.change(
    await screen.findByLabelText("Provider key", { selector: "input" }),
    {
      target: { value: "submitted" },
    },
  );
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  view.unmount();
  await act(async () => pending.resolve({ settings: specs }));
  expect(studio.toast).not.toHaveBeenCalled();
  expect(studio.refreshOverview).not.toHaveBeenCalled();
});
it("does not show a connection result from a provider that was left", async () => {
  const pending = deferred<any>();
  setup(
    undefined,
    vi.fn(() => pending.promise),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Test openai connection" }),
  );
  fireEvent.click(screen.getByRole("button", { name: /Helper LLM/ }));
  await act(async () =>
    pending.resolve({ ok: true, detail: "Old provider result" }),
  );
  fireEvent.click(screen.getByRole("button", { name: /OpenAI \(assistant\)/ }));
  expect(screen.queryByText(/Old provider result/)).toBeNull();
});
it("offers recovery when initial settings loading fails", async () => {
  const { api, view, tree } = setup();
  view.unmount();
  api.get.mockRejectedValueOnce(Error("Settings unavailable"));
  render(tree());
  fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
  await screen.findByLabelText("Provider key", { selector: "input" });
});

it("adopts effective saved values, including environment fallback after clearing", async () => {
  const effective = specs.map((spec) =>
    spec.key === "MODEL"
      ? { ...spec, value: "environment-model", source: "environment" }
      : spec,
  );
  setup(vi.fn(async () => ({ settings: effective })));
  await screen.findByLabelText("Provider key", { selector: "input" });
  fireEvent.click(screen.getByRole("button", { name: /Helper LLM/ }));
  fireEvent.change(
    screen.getByLabelText("Helper model", { selector: "input" }),
    { target: { value: "" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(
      (
        screen.getByLabelText("Helper model", {
          selector: "input",
        }) as HTMLInputElement
      ).value,
    ).toBe("environment-model"),
  );
  expect(screen.getByText("environment · configured")).toBeTruthy();
  expect(screen.getByText("No unsaved changes")).toBeTruthy();
});

it("preserves a newer nonsecret edit that restores the old baseline while saving", async () => {
  const pending = deferred<any>();
  setup(vi.fn(() => pending.promise));
  await screen.findByLabelText("Provider key", { selector: "input" });
  fireEvent.click(screen.getByRole("button", { name: /Helper LLM/ }));
  fireEvent.change(
    screen.getByLabelText("Helper model", { selector: "input" }),
    { target: { value: "submitted-model" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  fireEvent.change(
    screen.getByLabelText("Helper model", { selector: "input" }),
    { target: { value: "original" } },
  );
  await act(async () =>
    pending.resolve({
      settings: specs.map((spec) =>
        spec.key === "MODEL" ? { ...spec, value: "submitted-model" } : spec,
      ),
    }),
  );
  expect(
    (
      screen.getByLabelText("Helper model", {
        selector: "input",
      }) as HTMLInputElement
    ).value,
  ).toBe("original");
  expect(screen.getByText("1 unsaved change across providers")).toBeTruthy();
});

it("reports a summary refresh failure separately from the completed save", async () => {
  const { studio } = setup(vi.fn(async () => ({ settings: specs })));
  studio.refreshOverview.mockRejectedValue(Error("Summary unavailable"));
  fireEvent.change(
    await screen.findByLabelText("Provider key", { selector: "input" }),
    { target: { value: "submitted" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByText(
    "Settings were saved, but the workspace summary could not be refreshed.",
  );
  expect(studio.toast).toHaveBeenCalledWith("Settings saved");
  expect(screen.queryByText("Summary unavailable")).toBeNull();
  expect(screen.getByText("No unsaved changes")).toBeTruthy();
});

it.each(["scope", "unmount"] as const)(
  "ignores a late connection response after %s",
  async (end) => {
    const pending = deferred<any>();
    const { studio, tree, view } = setup(
      undefined,
      vi.fn(() => pending.promise),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Test openai connection" }),
    );
    if (end === "scope") {
      view.rerender(
        tree({ ...studio, scopeKey: "self:second", project: "second" }),
      );
      await screen.findByLabelText("Provider key", { selector: "input" });
    } else view.unmount();
    await act(async () =>
      pending.resolve({ ok: false, detail: "Expired request" }),
    );
    expect(screen.queryByText(/Expired request/)).toBeNull();
    expect(studio.toast).not.toHaveBeenCalled();
  },
);

it("refreshes the baseline when an adapter omits settings after an earlier canonical save", async () => {
  const first = specs.map((spec) =>
    spec.key === "MODEL" ? { ...spec, value: "first-saved" } : spec,
  );
  const refreshed = specs.map((spec) =>
    spec.key === "MODEL" ? { ...spec, value: "effective-second" } : spec,
  );
  const put = vi
    .fn()
    .mockResolvedValueOnce({ settings: first })
    .mockResolvedValueOnce({});
  const { api } = setup(put);
  await screen.findByLabelText("Provider key", { selector: "input" });
  fireEvent.click(screen.getByRole("button", { name: /Helper LLM/ }));
  fireEvent.change(
    screen.getByLabelText("Helper model", { selector: "input" }),
    { target: { value: "first-saved" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(screen.getByText("No unsaved changes")).toBeTruthy(),
  );
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Reset draft" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true),
  );
  api.get.mockImplementation(async (path: string) =>
    path === "/api/settings"
      ? { settings: refreshed }
      : { inference: { mode: "local" } },
  );
  fireEvent.change(
    screen.getByLabelText("Helper model", { selector: "input" }),
    { target: { value: "second-submitted" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(
      (
        screen.getByLabelText("Helper model", {
          selector: "input",
        }) as HTMLInputElement
      ).value,
    ).toBe("effective-second"),
  );
  expect(put).toHaveBeenCalledTimes(2);
  expect(screen.getByText("No unsaved changes")).toBeTruthy();
});
