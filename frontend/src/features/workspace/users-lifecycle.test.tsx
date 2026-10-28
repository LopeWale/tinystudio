import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { UsersPage } from "./users";
const users = [
  { id: "self", email: "owner@example.test", role: "admin" },
  { id: "other", email: "member@example.test", role: "member" },
];
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
}
function setup() {
  const api = {
    get: vi.fn(async () => users),
    post: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
  };
  const s: any = {
    api,
    user: { id: "self", role: "admin" },
    project: "fixture",
    scopeKey: "self:fixture",
    route: "/users",
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
    confirm: vi.fn(async () => true),
  };
  const node = (state: any) => (
    <StudioContext.Provider value={state}>
      <MemoryRouter>
        <UsersPage />
      </MemoryRouter>
    </StudioContext.Provider>
  );
  const view = render(node(s));
  return {
    api,
    s,
    view,
    change: (changes: any) => view.rerender(node({ ...s, ...changes })),
  };
}
function createDraft(email = "new@example.test") {
  fireEvent.click(screen.getByRole("button", { name: "Add user" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Email" }), {
    target: { value: email },
  });
  fireEvent.change(screen.getByLabelText("Password (8+ characters)"), {
    target: { value: "secret-password" },
  });
}
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
it("does not delete after an administrator context changes while confirmation is pending", async () => {
  const { s, api, change } = setup();
  const pending = deferred<boolean>();
  s.confirm.mockReturnValue(pending.promise);
  fireEvent.click(
    await screen.findByRole("button", { name: "Delete member@example.test" }),
  );
  change({ scopeKey: "self:another" });
  await act(async () => pending.resolve(true));
  expect(api.del).not.toHaveBeenCalled();
});
it("does not delete after unmount while confirmation is pending", async () => {
  const { s, api, view } = setup();
  const pending = deferred<boolean>();
  s.confirm.mockReturnValue(pending.promise);
  fireEvent.click(
    await screen.findByRole("button", { name: "Delete member@example.test" }),
  );
  view.unmount();
  await act(async () => pending.resolve(true));
  expect(api.del).not.toHaveBeenCalled();
});
it("late create success cannot close or clear a subsequently opened draft", async () => {
  const { api, s } = setup();
  const pending = deferred<object>();
  api.post.mockReturnValue(pending.promise);
  createDraft();
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
  createDraft("second@example.test");
  await act(async () => pending.resolve({}));
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(
    (screen.getByRole("textbox", { name: "Email" }) as HTMLInputElement).value,
  ).toBe("second@example.test");
  expect(s.toast).not.toHaveBeenCalled();
});
it("failed creation retains the draft and reports one error inside the dialog", async () => {
  const { api } = setup();
  api.post.mockRejectedValue(Error("Account could not be created"));
  createDraft();
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await screen.findByText("Account could not be created");
  expect(screen.getAllByRole("alert")).toHaveLength(1);
  expect(
    within(screen.getByRole("dialog")).getByRole("alert").textContent,
  ).toContain("Account could not be created");
  expect(
    (screen.getByLabelText("Password (8+ characters)") as HTMLInputElement)
      .value,
  ).toBe("secret-password");
});
it("failed initial load offers a retry and does not claim an empty account directory", async () => {
  const { api, s, view } = setup();
  view.unmount();
  api.get.mockRejectedValueOnce(Error("Directory unavailable"));
  render(
    <StudioContext.Provider value={s}>
      <MemoryRouter>
        <UsersPage />
      </MemoryRouter>
    </StudioContext.Provider>,
  );
  await screen.findByText("Directory unavailable");
  expect(
    screen.queryByText("No accounts to show. Add a user to grant access."),
  ).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByRole("button", { name: "Delete member@example.test" });
});

it.each([
  { user: { id: "next-admin", role: "admin" } },
  { user: { id: "self", role: "member" } },
])(
  "invalidates pending deletion when user identity or administrator authority changes: %j",
  async (changes) => {
    const { s, api, change } = setup();
    const pending = deferred<boolean>();
    s.confirm.mockReturnValue(pending.promise);
    fireEvent.click(
      await screen.findByRole("button", { name: "Delete member@example.test" }),
    );
    change(changes);
    await act(async () => pending.resolve(true));
    expect(api.del).not.toHaveBeenCalled();
  },
);
it("suppresses duplicate submissions while the same dialog save is pending", async () => {
  const { api } = setup();
  const pending = deferred<object>();
  api.post.mockReturnValue(pending.promise);
  createDraft();
  const save = screen.getByRole("button", { name: "Create" });
  fireEvent.click(save);
  fireEvent.click(save);
  expect(api.post).toHaveBeenCalledOnce();
  await act(async () => pending.resolve({}));
  expect(screen.queryByRole("dialog")).toBeNull();
});
it("keeps obsolete save failures out of a subsequent dialog and clears cancelled passwords", async () => {
  const { api } = setup();
  const pending = deferred<object>();
  api.post.mockReturnValue(pending.promise);
  createDraft();
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
  fireEvent.click(screen.getByRole("button", { name: "Add user" }));
  expect(
    (screen.getByLabelText("Password (8+ characters)") as HTMLInputElement)
      .value,
  ).toBe("");
  await act(async () => pending.reject(Error("Obsolete failure")));
  expect(screen.queryByText("Obsolete failure")).toBeNull();
  expect(screen.getByRole("dialog")).toBeTruthy();
});
it("edits roles through the installed Select while preserving exact API and self protection", async () => {
  const { api } = setup();
  const user = userEvent.setup();
  const ownRole = await screen.findByRole("button", {
    name: /Role for owner@example.test/,
  });
  expect((ownRole as HTMLButtonElement).disabled).toBe(true);
  expect(
    screen.queryByRole("button", { name: "Delete owner@example.test" }),
  ).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Disable owner@example.test" }),
  ).toBeNull();
  await user.click(
    screen.getByRole("button", { name: /Role for member@example.test/ }),
  );
  await user.click(await screen.findByRole("option", { name: "Admin" }));
  await waitFor(() =>
    expect(api.patch).toHaveBeenCalledWith("/api/users/other", {
      role: "admin",
    }),
  );
});
it("uses one shared collection toolbar and filters roles with the installed Select", async () => {
  const { view } = setup();
  const user = userEvent.setup();
  await screen.findByRole("button", { name: "Delete member@example.test" });
  const table = screen.getByRole("table", { name: "User accounts" });
  expect(
    view.container.querySelector(
      ".records-table-host > .data-table[data-table-layout=fill]",
    ),
  ).toBeTruthy();
  expect(
    screen.getAllByRole("searchbox", { name: "Find a user" }),
  ).toHaveLength(1);
  await user.click(screen.getByRole("button", { name: /Account role/ }));
  await user.click(
    await screen.findByRole("option", { name: "Administrators" }),
  );
  expect(within(table).queryByText("member@example.test")).toBeNull();
  expect(within(table).getByText("owner@example.test")).toBeTruthy();
});
it("sets a password through the exact endpoint and never registers it as evidence", async () => {
  const { api, s } = setup();
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Set password for member@example.test",
    }),
  );
  fireEvent.change(screen.getByLabelText("Password (8+ characters)"), {
    target: { value: "private-new-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save password" }));
  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith("/api/users/other/password", {
      password: "private-new-password",
    }),
  );
  expect(JSON.stringify(s.evidence.snapshot())).not.toContain(
    "private-new-password",
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
it("ignores a successful save after scope replacement and permits a fresh dialog operation", async () => {
  const { api, s, change } = setup();
  const pending = deferred<object>();
  api.post.mockReturnValueOnce(pending.promise);
  createDraft();
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  change({ scopeKey: "self:replacement" });
  expect(screen.queryByRole("dialog")).toBeNull();
  createDraft("fresh@example.test");
  await act(async () => pending.resolve({}));
  expect(s.toast).not.toHaveBeenCalled();
  expect(
    (screen.getByRole("textbox", { name: "Email" }) as HTMLInputElement).value,
  ).toBe("fresh@example.test");
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
it("reconciles a dismissed successful create while preserving a newer dialog", async () => {
  const { api } = setup();
  const pending = deferred<object>();
  api.post.mockReturnValueOnce(pending.promise);
  await screen.findByText("owner@example.test");
  createDraft();
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
  createDraft("newer@example.test");
  api.get.mockResolvedValue([
    ...users,
    { id: "new", email: "new@example.test", role: "member" },
  ]);
  await act(async () => pending.resolve({}));
  await screen.findByText("new@example.test");
  expect(
    (screen.getByRole("textbox", { name: "Email" }) as HTMLInputElement).value,
  ).toBe("newer@example.test");
});
it("preserves edits made while a save is pending and admits the revised draft after settlement", async () => {
  const { api } = setup();
  const pending = deferred<object>();
  api.post.mockReturnValueOnce(pending.promise);
  createDraft();
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "Revised name" },
  });
  await act(async () => pending.resolve({}));
  expect(
    (screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value,
  ).toBe("Revised name");
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2));
  expect(api.post).toHaveBeenLastCalledWith("/api/users", {
    email: "new@example.test",
    name: "Revised name",
    password: "secret-password",
    role: "member",
  });
});
it("blocks overlapping password writes after dismissing and reopening the same target until settlement", async () => {
  const { api } = setup();
  const pending = deferred<object>();
  api.post.mockReturnValueOnce(pending.promise);
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Set password for member@example.test",
    }),
  );
  fireEvent.change(screen.getByLabelText("Password (8+ characters)"), {
    target: { value: "first-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save password" }));
  fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
  fireEvent.click(
    screen.getByRole("button", {
      name: "Set password for member@example.test",
    }),
  );
  fireEvent.change(screen.getByLabelText("Password (8+ characters)"), {
    target: { value: "second-password" },
  });
  const pendingSave = screen.getByRole("button", { name: "Working…" });
  expect((pendingSave as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(pendingSave);
  expect(api.post).toHaveBeenCalledOnce();
  await act(async () => pending.resolve({}));
  expect(
    (screen.getByLabelText("Password (8+ characters)") as HTMLInputElement)
      .value,
  ).toBe("second-password");
  fireEvent.click(screen.getByRole("button", { name: "Save password" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2));
  expect(api.post).toHaveBeenLastCalledWith("/api/users/other/password", {
    password: "second-password",
  });
});
it("admits a password request in a different context while an old-context write remains pending", async () => {
  const { api, change, s } = setup();
  const first = deferred<object>(),
    second = deferred<object>();
  api.post
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(second.promise);
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Set password for member@example.test",
    }),
  );
  fireEvent.change(screen.getByLabelText("Password (8+ characters)"), {
    target: { value: "old-context-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save password" }));
  change({ scopeKey: "self:new-context" });
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Set password for member@example.test",
    }),
  );
  fireEvent.change(screen.getByLabelText("Password (8+ characters)"), {
    target: { value: "new-context-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save password" }));
  expect(api.post).toHaveBeenCalledTimes(2);
  await act(async () => first.resolve({}));
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(
    (screen.getByLabelText("Password (8+ characters)") as HTMLInputElement)
      .value,
  ).toBe("new-context-password");
  expect(s.toast).not.toHaveBeenCalled();
  await act(async () => second.resolve({}));
  expect(screen.queryByRole("dialog")).toBeNull();
});
