import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { DatasetPage, DatasetsPage } from "./datasets";
import { RepositoriesPage } from "./repositories";
import { useRetainedResource } from "./records-workspace";
import { ComponentActionsProvider } from "../assistant/actions";
import { ComponentActionRegistry } from "../../controllers/componentActions";

afterEach(cleanup);
function mount(
  element: React.ReactNode,
  path: string,
  route: string,
  get: any,
  registry = new ComponentActionRegistry(),
) {
  const studio = {
    api: { get, del: vi.fn(async () => ({})), post: vi.fn(async () => []) },
    project: "fixture",
    scopeKey: "user:fixture",
    user: { id: "user" },
    route: path,
    evidence: new EvidenceRegistry(),
    overview: { tree: { datasets: [] } },
    toast: vi.fn(),
    confirm: vi.fn(async () => true),
    trackTask: vi.fn(),
    showTasks: vi.fn(),
    refreshOverview: vi.fn(),
    navigate: vi.fn(),
  } as any;
  registry.setScope({ user: "user", project: "fixture", route: path });
  render(
    <StudioContext.Provider value={studio}>
      <ComponentActionsProvider registry={registry}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path={route} element={element} />
          </Routes>
        </MemoryRouter>
      </ComponentActionsProvider>
    </StudioContext.Provider>,
  );
  return studio;
}
const rows = [
  {
    id: 0,
    bucket: "training",
    source: "uploaded",
    labels: ["lamps"],
    rating: 0,
    messages: [
      { role: "user", content: "Turn on lamp" },
      { role: "assistant", content: "Light on" },
    ],
  },
  {
    id: 9,
    bucket: "validation",
    source: "synthetic",
    labels: [],
    messages: [
      { role: "user", content: "Turn off lamp" },
      { role: "assistant", content: "Light off" },
    ],
  },
];

describe("record workspace interaction contracts from boards 03 and 04", () => {
  it.each(["success", "failure"])(
    "discards a late query %s after its dialog is closed and reopened",
    async (outcome) => {
      let resolveQuery!: (value: any[]) => void;
      let rejectQuery!: (error: Error) => void;
      const studio = mount(
        <RepositoriesPage />,
        "/repositories",
        "/repositories",
        vi.fn(async (path: string) =>
          path === "/api/hub/models"
            ? {}
            : [{ name: "alpha", documents: [], chunk_count: 0 }],
        ),
      );
      studio.api.post.mockImplementation(
        () =>
          new Promise((resolve, reject) => {
            resolveQuery = resolve;
            rejectQuery = reject;
          }),
      );
      fireEvent.click(await screen.findByRole("button", { name: "alpha" }));
      fireEvent.click(screen.getByRole("button", { name: /^Query$/ }));
      fireEvent.change(screen.getByRole("textbox", { name: "Text" }), {
        target: { value: "old query" },
      });
      fireEvent.keyDown(screen.getByRole("textbox", { name: "Text" }), {
        key: "Enter",
      });
      await waitFor(() =>
        expect(studio.api.post).toHaveBeenCalledWith(
          "/api/repositories/alpha/query",
          { text: "old query", limit: 5, similarity_threshold: 0 },
        ),
      );
      fireEvent.keyDown(screen.getByRole("textbox", { name: "Text" }), {
        key: "Enter",
      });
      expect(studio.api.post).toHaveBeenCalledTimes(1);
      fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
      fireEvent.click(screen.getByRole("button", { name: /^Query$/ }));
      await act(async () => {
        if (outcome === "failure") rejectQuery(Error("Old query failed"));
        else
          resolveQuery([
            {
              document: "stale.txt",
              text: "Stale passage",
              similarity_score: 0.8,
            },
          ]);
      });
      expect(studio.toast).not.toHaveBeenCalled();
      expect(screen.queryByText("Stale passage")).toBeNull();
      expect(
        screen.getByText("Search to inspect relevant passages."),
      ).toBeVisible();
      expect(screen.getByRole("textbox", { name: "Text" })).toHaveValue("");
    },
  );
  it("returns from repository documents to the same search, page, density and checked record", async () => {
    const user = userEvent.setup();
    const repositories = Array.from({ length: 26 }, (_, index) => ({
      name: `repo-${String(index).padStart(2, "0")}`,
      chunk_count: index,
      documents: [
        { name: "manual.txt", kind: "txt", chunks: 2 },
        { name: "guide.md", kind: "md", chunks: 3 },
      ],
    }));
    mount(
      <RepositoriesPage />,
      "/repositories",
      "/repositories",
      vi.fn(async (path) => (path === "/api/hub/models" ? {} : repositories)),
    );
    await screen.findByRole("button", { name: "repo-00" });
    await user.type(
      screen.getByRole("searchbox", { name: "Find a repository" }),
      "repo-",
    );
    fireEvent.change(screen.getByLabelText("Repositories rows per page"), {
      target: { value: "10" },
    });
    await user.click(screen.getByRole("radio", { name: "Compact" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(
      screen.getByRole("checkbox", { name: "Select repository repo-10" }),
    );
    const trigger = screen.getByRole("button", { name: "repo-10" });
    await user.click(trigger);
    expect(
      screen.getByRole("heading", { name: "repo-10", level: 1 }),
    ).toHaveFocus();
    expect(screen.queryByRole("table", { name: "Repositories" })).toBeNull();
    await user.type(
      screen.getByRole("searchbox", { name: "Find a document" }),
      "manual",
    );
    expect(
      screen.getByRole("table", { name: "Documents in repo-10" }),
    ).toHaveTextContent("manual.txt");
    expect(
      screen.queryByRole("button", { name: "Remove guide.md" }),
    ).toBeNull();
    await user.click(
      screen.getByRole("button", { name: /Back to repositories/ }),
    );
    expect(trigger).toHaveFocus();
    expect(
      screen.getByRole("searchbox", { name: "Find a repository" }),
    ).toHaveValue("repo-");
    expect(screen.getByText("Page 2 of 3")).toBeVisible();
    expect(
      screen.getByRole("checkbox", { name: "Select repository repo-10" }),
    ).toBeChecked();
    expect(screen.getByRole("radio", { name: "Compact" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });
  it.each(["failed", "removed"])(
    "handles a %s repository refresh without confusing stale context with removal",
    async (outcome) => {
      let loads = 0;
      const get = vi.fn(async (path) => {
        if (path === "/api/hub/models") return {};
        if (++loads > 1) {
          if (outcome === "failed")
            throw Error("Repository refresh unavailable");
          return [];
        }
        return [
          {
            name: "alpha",
            documents: [{ name: "manual.txt", kind: "txt", chunks: 1 }],
          },
        ];
      });
      mount(<RepositoriesPage />, "/repositories", "/repositories", get);
      fireEvent.click(
        await screen.findByRole("checkbox", {
          name: "Select repository alpha",
        }),
      );
      fireEvent.click(screen.getByRole("button", { name: "alpha" }));
      fireEvent.click(
        screen.getByRole("button", { name: "Remove manual.txt" }),
      );
      if (outcome === "failed") {
        await screen.findByText("Repository refresh unavailable");
        expect(
          screen.getByRole("heading", { name: "alpha", level: 1 }),
        ).toBeVisible();
        expect(
          screen.getByRole("table", { name: "Documents in alpha" }),
        ).toBeVisible();
      } else {
        await screen.findByRole("heading", { name: "Repositories", level: 1 });
        expect(
          screen.queryByRole("table", { name: "Documents in alpha" }),
        ).toBeNull();
        expect(screen.queryByText("1 selected")).toBeNull();
        expect(
          screen.getByRole("searchbox", { name: "Find a repository" }),
        ).toHaveFocus();
      }
    },
  );
  for (const kind of ["dataset", "repository"] as const) {
    for (const remaining of [0, 1, 2]) {
      it(`restores ${kind} comparison focus with ${remaining} selections remaining`, async () => {
        const repository = kind === "repository";
        const path = repository ? "/repositories" : "/datasets";
        const registry = new ComponentActionRegistry();
        mount(
          repository ? <RepositoriesPage /> : <DatasetsPage />,
          path,
          path,
          vi.fn(async (url: string) =>
            url === "/api/hub/models"
              ? {}
              : [
                  {
                    name: "alpha",
                    datapoints: 1,
                    documents: [{ name: "manual.txt", kind: "txt", chunks: 2 }],
                  },
                  { name: "beta", datapoints: 2, documents: [] },
                ],
          ),
          registry,
        );
        fireEvent.click(
          await screen.findByRole("checkbox", { name: `Select ${kind} alpha` }),
        );
        fireEvent.click(
          screen.getByRole("checkbox", { name: `Select ${kind} beta` }),
        );
        if (repository)
          fireEvent.click(screen.getByRole("button", { name: "alpha" }));
        const documentsAction = registry
          .inspect()
          .actions.find(
            (action) => action.label === "Documents in alpha: row density",
          );
        if (repository) expect(documentsAction).toBeTruthy();
        if (repository) {
          expect(
            screen.queryByRole("table", { name: "Repositories" }),
          ).toBeNull();
          expect(
            registry
              .inspect()
              .actions.some((action) =>
                action.label.startsWith("Repositories:"),
              ),
          ).toBe(false);
          fireEvent.click(
            screen.getByRole("button", { name: /Back to repositories/ }),
          );
        }
        fireEvent.click(
          screen.getByRole("button", { name: "Compare selected" }),
        );
        if (repository) {
          expect(
            registry
              .inspect()
              .actions.some((action) =>
                action.label.startsWith("Documents in alpha:"),
              ),
          ).toBe(false);
          await expect(
            registry.execute(documentsAction!.ref, "compact", false),
          ).rejects.toThrow();
        }
        if (remaining < 2)
          fireEvent.click(screen.getByRole("button", { name: "Remove alpha" }));
        if (remaining === 0)
          fireEvent.click(screen.getByRole("button", { name: "Remove beta" }));
        fireEvent.click(
          screen.getByRole("button", { name: "Back to collection" }),
        );
        expect(document.activeElement).toBe(
          remaining === 2
            ? screen.getByRole("button", { name: "Compare selected" })
            : screen.getByRole("searchbox", { name: `Find a ${kind}` }),
        );
        if (repository)
          expect(
            registry
              .inspect()
              .actions.some(
                (action) => action.label === "Documents in alpha: row density",
              ),
          ).toBe(false);
      });
    }
  }
  it("keeps dataset checkbox selection independent of inspection and search, then aligns comparisons", async () => {
    mount(
      <DatasetsPage />,
      "/datasets",
      "/datasets",
      vi.fn(async () => [
        {
          name: "alpha",
          datapoints: 0,
          snapshot_count: 1,
          created: "2026-10-01",
        },
        { name: "beta", datapoints: 3, snapshot_count: 0 },
      ]),
    );
    const first = await screen.findByRole("checkbox", {
      name: "Select dataset alpha",
    });
    expect(
      screen.queryByRole("button", { name: "Compare selected" }),
    ).toBeNull();
    const controls = screen.getByRole("group", {
      name: "Datasets table controls",
    });
    expect(
      within(controls).getByRole("searchbox", { name: "Find a dataset" }),
    ).toBeTruthy();
    fireEvent.click(first);
    expect(
      screen.getByRole("button", { name: "Compare selected" }),
    ).toBeDisabled();
    expect(screen.queryByRole("complementary")).toBeNull();
    const inspectButton = screen.getByRole("button", {
      name: "Inspect dataset beta",
    });
    fireEvent.click(inspectButton);
    expect(screen.getByRole("complementary", { name: "beta" })).toBeTruthy();
    expect((first as HTMLInputElement).checked).toBe(true);
    expect(
      (
        screen.getByRole("checkbox", {
          name: "Select dataset beta",
        }) as HTMLInputElement
      ).checked,
    ).toBe(false);
    fireEvent.keyDown(screen.getByRole("complementary"), { key: "Escape" });
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(document.activeElement).toBe(inspectButton);
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select dataset beta" }),
    );
    fireEvent.change(
      screen.getByRole("searchbox", { name: "Find a dataset" }),
      { target: { value: "missing" } },
    );
    expect(screen.getByText(/No datasets match/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Compare selected" }));
    const comparison = screen.getByRole("table", { name: "Record comparison" });
    expect(within(comparison).getByText("alpha")).toBeTruthy();
    expect(within(comparison).getByText("beta")).toBeTruthy();
    expect(within(comparison).getByText("2026-10-01")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Back to collection" }));
    expect(
      (
        screen.getByRole("searchbox", {
          name: "Find a dataset",
        }) as HTMLInputElement
      ).value,
    ).toBe("missing");
    expect(screen.getByText("2 selected")).toBeTruthy();
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Compare selected" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(
      screen.queryByRole("group", { name: "Selection actions" }),
    ).toBeNull();
    expect(document.activeElement).toBe(
      screen.getByRole("searchbox", { name: "Find a dataset" }),
    );
    expect((document.activeElement as HTMLInputElement).value).toBe("missing");
  });
  it("steps within the visible datapoint cohort without changing checked records and compares missing values honestly", async () => {
    mount(
      <DatasetPage />,
      "/datasets/alpha",
      "/datasets/:name",
      vi.fn(async (path: string) =>
        path === "/api/snapshots"
          ? []
          : {
              meta: { buckets: { training: 1, validation: 1 } },
              total: 2,
              rows,
            },
      ),
    );
    fireEvent.click(
      await screen.findByRole("checkbox", { name: "Select datapoint 0" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Inspect datapoint 0: Turn on lamp" }),
    );
    expect(
      screen.getByRole("complementary", { name: "Datapoint 0" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next record" }));
    expect(
      screen.getByRole("complementary", { name: "Datapoint 9" }),
    ).toBeTruthy();
    expect(
      (
        screen.getByRole("checkbox", {
          name: "Select datapoint 0",
        }) as HTMLInputElement
      ).checked,
    ).toBe(true);
    expect(
      (
        screen.getByRole("checkbox", {
          name: "Select datapoint 9",
        }) as HTMLInputElement
      ).checked,
    ).toBe(false);
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select datapoint 9" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Compare selected" }));
    const comparison = screen.getByRole("table", { name: "Record comparison" });
    const ratingRow = within(comparison).getByRole("row", { name: /Rating/ });
    expect(within(ratingRow).getByText("0/10")).toBeTruthy();
    expect(within(ratingRow).getByText("—")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove 9" }));
    expect(screen.getByText(/Select at least two records/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Back to collection" }));
    expect(
      screen.getByRole("complementary", { name: "Datapoint 9" }),
    ).toBeTruthy();
    expect(screen.getByText("1 selected")).toBeTruthy();
  });
  it("keeps datapoint inspection reachable when optional columns are hidden", async () => {
    mount(
      <DatasetPage />,
      "/datasets/alpha",
      "/datasets/:name",
      vi.fn(async (path: string) =>
        path === "/api/snapshots"
          ? []
          : {
              meta: { buckets: { training: 1, validation: 1 } },
              total: 2,
              rows,
            },
      ),
    );
    const inspect = await screen.findByRole("button", {
      name: "Inspect datapoint 9: Turn off lamp",
    });
    fireEvent.click(screen.getByRole("button", { name: "Columns" }));
    const columns = await screen.findByRole("dialog", {
      name: "Dataset records visible columns",
    });
    expect(
      within(columns).getByRole("checkbox", { name: "User message" }),
    ).toBeDisabled();
    fireEvent.click(
      within(columns).getByRole("checkbox", { name: "Assistant response" }),
    );
    expect(
      within(
        screen.getByRole("table", { name: "Dataset records" }),
      ).queryByRole("columnheader", { name: "Assistant response" }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Columns" }));
    inspect.focus();
    await userEvent.setup().keyboard("{Enter}");
    expect(
      screen.getByRole("complementary", { name: "Datapoint 9" }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("complementary", { name: "Datapoint 0" }),
    ).toBeNull();
  });
  it("starts repositories in browse mode and retains indexed document removal and query actions beside the collection", async () => {
    const studio = mount(
      <RepositoriesPage />,
      "/repositories",
      "/repositories",
      vi.fn(async (path: string) =>
        path === "/api/hub/models"
          ? {}
          : [
              {
                name: "alpha",
                description: "Manuals",
                embedding_model: "embed",
                chunk_count: 2,
                documents: [{ name: "manual.txt", kind: "txt", chunks: 2 }],
              },
              {
                name: "beta",
                description: "Guides",
                embedding_model: "other",
                chunk_count: 0,
                documents: [],
              },
            ],
      ),
    );
    const alpha = await screen.findByRole("button", { name: "alpha" });
    expect(screen.queryByRole("complementary")).toBeNull();
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select repository beta" }),
    );
    fireEvent.click(alpha);
    const inspector = screen.getByRole("region", { name: "Repository alpha" });
    expect(screen.getByRole("button", { name: "Add documents" })).toBeTruthy();
    fireEvent.click(
      within(inspector).getByRole("button", { name: "Remove manual.txt" }),
    );
    await waitFor(() =>
      expect(studio.api.del).toHaveBeenCalledWith(
        "/api/repositories/alpha/documents/manual.txt",
      ),
    );
    expect(studio.confirm).toHaveBeenCalledWith(
      'Remove "manual.txt" from alpha?',
    );
    fireEvent.click(await screen.findByRole("button", { name: "Query" }));
    expect(
      await screen.findByRole("dialog", { name: "Query alpha" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
    fireEvent.click(
      screen.getByRole("button", { name: /Back to repositories/ }),
    );
    expect(screen.getByText("1 selected")).toBeTruthy();
  });
});

it("retains a successful resource during same-scope refresh and clears it immediately on project scope change", async () => {
  const get = vi
    .fn()
    .mockResolvedValueOnce([{ name: "Private alpha" }])
    .mockImplementation(() => new Promise(() => {}));
  const runtime = {
    api: { get },
    project: "alpha",
    scopeKey: "u:alpha",
    user: { id: "u" },
    route: "/datasets",
    evidence: new EvidenceRegistry(),
  } as any;
  function Resource() {
    const resource = useRetainedResource<{ name: string }[]>("/api/datasets");
    return (
      <>
        <button onClick={resource.reload}>Refresh resource</button>
        {resource.data?.map((record) => (
          <p key={record.name}>{record.name}</p>
        ))}
        {resource.isStale && <p>Previous response retained</p>}
      </>
    );
  }
  const view = render(
    <StudioContext.Provider value={runtime}>
      <Resource />
    </StudioContext.Provider>,
  );
  await screen.findByText("Private alpha");
  fireEvent.click(screen.getByRole("button", { name: "Refresh resource" }));
  expect(screen.getByText("Private alpha")).toBeTruthy();
  expect(screen.getByText("Previous response retained")).toBeTruthy();
  view.rerender(
    <StudioContext.Provider
      value={{ ...runtime, project: "beta", scopeKey: "u:beta" }}
    >
      <Resource />
    </StudioContext.Provider>,
  );
  expect(screen.queryByText("Private alpha")).toBeNull();
  expect(screen.queryByText("Previous response retained")).toBeNull();
});
