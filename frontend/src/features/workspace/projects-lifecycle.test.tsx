import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { ProjectsPage } from "./projects";
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
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}
function setup() {
  const api = {
    get: vi.fn(async () => [
      { slug: "first", name: "First" },
      { slug: "other", name: "Other" },
    ]),
    post: vi.fn(),
    del: vi.fn(async () => ({})),
  };
  const s = {
    api,
    scopeKey: "self:first",
    project: "first",
    user: { id: "self", role: "admin" },
    route: "/projects",
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
    confirm: vi.fn(),
    refreshProjects: vi.fn(async () => {}),
    selectProject: vi.fn(),
    navigate: vi.fn(),
  } as any;
  const tree = (v = s) => (
    <StudioContext.Provider value={v}>
      <MemoryRouter>
        <ProjectsPage />
      </MemoryRouter>
    </StudioContext.Provider>
  );
  const view = render(tree());
  return { api, s, tree, view };
}
function create(name = "Draft") {
  fireEvent.click(screen.getByRole("button", { name: "New project" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: name },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create project" }));
}
it("does not navigate into a late creation from another workspace", async () => {
  const { api, s, view, tree } = setup();
  const pending = deferred<any>();
  api.post.mockReturnValue(pending.promise);
  create();
  view.rerender(tree({ ...s, scopeKey: "self:second", project: "second" }));
  await act(async () => pending.resolve({ slug: "created-old" }));
  expect(s.selectProject).not.toHaveBeenCalled();
  expect(s.navigate).not.toHaveBeenCalled();
  expect(s.refreshProjects).not.toHaveBeenCalled();
});
it("does not let a dismissed creation erase or close a newly opened draft", async () => {
  const { api, s } = setup();
  const pending = deferred<any>();
  api.post.mockReturnValue(pending.promise);
  create();
  fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
  fireEvent.click(screen.getByRole("button", { name: "New project" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "Next draft" },
  });
  await act(async () => pending.resolve({ slug: "created-old" }));
  expect(
    (screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value,
  ).toBe("Next draft");
  expect(s.navigate).not.toHaveBeenCalled();
});
it("does not delete after a confirmation resolves in another workspace", async () => {
  const { api, s, view, tree } = setup();
  const pending = deferred<boolean>();
  s.confirm.mockReturnValue(pending.promise);
  fireEvent.click(await screen.findByRole("button", { name: "Other" }));
  fireEvent.click(screen.getByRole("button", { name: "Delete Other" }));
  view.rerender(tree({ ...s, scopeKey: "self:second", project: "second" }));
  await act(async () => pending.resolve(true));
  expect(api.del).not.toHaveBeenCalled();
});
it("provides retry rather than claiming an empty directory after load failure", async () => {
  const { api, view, tree } = setup();
  view.unmount();
  api.get.mockRejectedValueOnce(Error("Unavailable"));
  render(tree());
  fireEvent.click(
    await screen.findByRole("button", { name: "Retry projects" }),
  );
  await screen.findByRole("button", { name: "Other" });
});
it("closes an inspected project without switching the active project", async () => {
  const { s } = setup();
  fireEvent.click(await screen.findByRole("button", { name: "Other" }));
  fireEvent.click(screen.getByRole("button", { name: "Back to results" }));
  expect(
    screen.queryByRole("complementary", { name: "Project details" }),
  ).toBeNull();
  expect(s.selectProject).not.toHaveBeenCalled();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Other" })).toBe(
      document.activeElement,
    ),
  );
});
it("leaves a dismissed creation in the directory without navigating", async () => {
  const { api, s } = setup();
  const pending = deferred<any>();
  api.post.mockReturnValue(pending.promise);
  create();
  fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
  await act(async () => pending.resolve({ slug: "created" }));
  expect(s.refreshProjects).toHaveBeenCalledTimes(1);
  expect(s.navigate).not.toHaveBeenCalled();
  expect(s.toast).toHaveBeenCalledWith("Project created");
});
it("does not resubmit a successful creation when directory refresh fails", async () => {
  const { api, s } = setup();
  api.post.mockResolvedValue({ slug: "created" });
  s.refreshProjects.mockRejectedValue(Error("Refresh unavailable"));
  create();
  await screen.findByText(
    /Project created, but the directory could not refresh/,
  );
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(s.navigate).not.toHaveBeenCalled();
  expect(api.post).toHaveBeenCalledTimes(1);
});
it("retains a failed draft and reports the error only in its dialog", async () => {
  const { api } = setup();
  api.post.mockRejectedValue(Error("Create refused"));
  create();
  await screen.findByText("Create refused");
  expect(screen.getAllByRole("alert")).toHaveLength(1);
  expect(
    (screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value,
  ).toBe("Draft");
});
it("does not submit the same pending project twice", async () => {
  const { api } = setup();
  const pending = deferred<any>();
  api.post.mockReturnValue(pending.promise);
  fireEvent.click(screen.getByRole("button", { name: "New project" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "Draft" },
  });
  const submit = screen.getByRole("button", { name: "Create project" });
  fireEvent.click(submit);
  fireEvent.click(submit);
  expect(api.post).toHaveBeenCalledTimes(1);
  await act(async () => pending.resolve({ slug: "created" }));
});
it("retains edits made while project creation is pending", async () => {
  const { api, s } = setup();
  const pending = deferred<any>();
  api.post.mockReturnValue(pending.promise);
  create();
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "Newer draft" },
  });
  await act(async () => pending.resolve({ slug: "old-draft" }));
  expect(
    (screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value,
  ).toBe("Newer draft");
  expect(s.navigate).not.toHaveBeenCalled();
});
