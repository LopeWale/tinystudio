import { afterEach, describe, expect, it, vi } from "vitest";
import {
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

afterEach(cleanup);
function mount(
  element: React.ReactNode,
  path: string,
  route: string,
  get: any,
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
  render(
    <StudioContext.Provider value={studio}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={route} element={element} />
        </Routes>
      </MemoryRouter>
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
      (
        screen.getByRole("button", {
          name: "Compare selected",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    const toolbar = screen
      .getByRole("searchbox", { name: "Find a dataset" })
      .closest(".records-toolbar")!;
    expect(toolbar.classList.contains("records-toolbar--unselected")).toBe(
      true,
    );
    fireEvent.click(first);
    expect(toolbar.classList.contains("records-toolbar--unselected")).toBe(
      false,
    );
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
    const inspector = screen.getByRole("complementary", { name: "alpha" });
    expect(
      within(inspector).getByRole("button", { name: "Add documents" }),
    ).toBeTruthy();
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
