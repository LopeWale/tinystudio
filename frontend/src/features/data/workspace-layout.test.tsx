import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  act,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ComponentActionsProvider } from "../assistant/actions";
import { ComponentActionRegistry } from "../../controllers/componentActions";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { DatasetPage, DatasetsPage } from "./datasets";
import { EvaluationPage } from "./evaluations";
import { RepositoriesPage } from "./repositories";
afterEach(cleanup);
function mount(
  element: React.ReactNode,
  path: string,
  route: string,
  get: any,
) {
  const studio = {
    api: { get },
    project: "fixture",
    scopeKey: "user:fixture",
    user: { id: "user" },
    route: path,
    evidence: new EvidenceRegistry(),
    overview: { tree: { datasets: [] } },
    toast: vi.fn(),
    confirm: vi.fn(),
    trackTask: vi.fn(),
    showTasks: vi.fn(),
    refreshOverview: vi.fn(),
    navigate: vi.fn(),
  } as any;
  const registry = new ComponentActionRegistry();
  registry.setScope({ user: "user", project: "fixture", route: path });
  const view = render(
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
  return Object.assign(view, { registry, studio });
}
describe("compact data workspaces", () => {
  it("keeps the inspector and assistant aligned with the table's displayed order and filters", async () => {
    const view = mount(
      <DatasetPage />,
      "/datasets/alpha",
      "/datasets/:name",
      vi.fn(async (path: string) =>
        path === "/api/snapshots"
          ? []
          : {
              total: 3,
              meta: { buckets: { training: 2, validation: 1 } },
              rows: [
                {
                  id: 1,
                  bucket: "training",
                  labels: [],
                  messages: [{ role: "user", content: "Zulu" }],
                },
                {
                  id: 2,
                  bucket: "validation",
                  labels: [],
                  messages: [{ role: "user", content: "Alpha" }],
                },
                {
                  id: 3,
                  bucket: "training",
                  labels: [],
                  messages: [{ role: "user", content: "Bravo" }],
                },
              ],
            },
      ),
    );
    const table = await screen.findByRole("table", { name: "Dataset records" });
    fireEvent.click(
      within(table).getByRole("button", { name: "User message" }),
    );
    fireEvent.click(
      within(table).getByRole("button", { name: "Inspect datapoint 2: Alpha" }),
    );
    expect(screen.getByText("1 of 3 on this page")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Next record" }));
    expect(screen.getByRole("heading", { name: "Datapoint 3" })).toBeVisible();
    const controls = screen.getByRole("group", {
      name: "Dataset records table controls",
    });
    fireEvent.click(within(controls).getByRole("button", { name: "Filters" }));
    const filters = screen.getByRole("dialog", {
      name: "Dataset records filters",
    });
    fireEvent.click(within(filters).getByRole("button", { name: /^Bucket/ }));
    fireEvent.click(
      within(filters).getByRole("checkbox", { name: "training" }),
    );
    expect(screen.getByText("1 of 2 on this page")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Previous record" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Next record" }));
    expect(screen.getByRole("heading", { name: "Datapoint 1" })).toBeVisible();
    expect(
      view.registry
        .inspect()
        .actions.find((item) => item.id === "dataset.select")
        ?.options?.map((item) => item.value),
    ).toEqual(["3", "1"]);
  });
  it("keeps dataset search in a real table with working detail links", async () => {
    const view = mount(
      <DatasetsPage />,
      "/datasets",
      "/datasets",
      vi.fn(async () => [
        { name: "alpha", datapoints: 11, snapshot_count: 1 },
        { name: "beta", datapoints: 2, snapshot_count: 0 },
      ]),
    );
    const table = await screen.findByRole("table", { name: "Datasets" });
    expect(
      within(table).getByRole("link", { name: "alpha" }).getAttribute("href"),
    ).toBe("#/datasets/alpha");
    fireEvent.change(
      screen.getByRole("searchbox", { name: "Find a dataset" }),
      { target: { value: "beta" } },
    );
    expect(within(table).queryByRole("link", { name: "alpha" })).toBeNull();
    expect(within(table).getByRole("link", { name: "beta" })).toBeTruthy();
    const action = view.registry
      .inspect()
      .actions.find((item) => item.id === "datasets.filter")!;
    await act(async () => {
      await view.registry.execute(action.ref, "alpha", false);
    });
    expect(within(table).getByRole("link", { name: "alpha" })).toBeTruthy();
    expect(within(table).queryByRole("link", { name: "beta" })).toBeNull();
    view.unmount();
    expect(view.registry.inspect().actions).toEqual([]);
    await expect(
      view.registry.execute(action.ref, "beta", false),
    ).rejects.toThrow("stale");
  });
  it("wires real dataset values into shared sorting, filters, density and protected actions while retaining evidence and selection", async () => {
    const view = mount(
      <DatasetsPage />,
      "/datasets",
      "/datasets",
      vi.fn(async () => [
        {
          name: "alpha",
          datapoints: 11,
          snapshot_count: 1,
          origin: { kind: "synthetic" },
        },
        { name: "beta", datapoints: 2, snapshot_count: 0 },
      ]),
    );
    const alpha = await screen.findByRole("checkbox", {
      name: "Select dataset alpha",
    });
    fireEvent.click(alpha);
    const table = screen.getByRole("table", { name: "Datasets" });
    fireEvent.click(within(table).getByRole("button", { name: "Datapoints" }));
    expect(within(table).getAllByRole("row")[1].textContent).toContain("beta");
    expect(
      within(table)
        .getByRole("columnheader", { name: "Datapoints" })
        .getAttribute("aria-sort"),
    ).toBe("ascending");
    const controls = screen.getByRole("group", {
      name: "Datasets table controls",
    });
    fireEvent.click(within(controls).getByRole("radio", { name: "Compact" }));
    expect(table.closest("[data-density]")?.getAttribute("data-density")).toBe(
      "compact",
    );
    fireEvent.click(within(controls).getByText("Columns"));
    const columnDialog = await screen.findByRole("dialog", {
      name: "Datasets visible columns",
    });
    fireEvent.click(
      within(columnDialog).getByRole("checkbox", { name: "Snapshots" }),
    );
    expect(
      within(table).queryByRole("columnheader", { name: "Snapshots" }),
    ).toBeNull();
    expect(
      (
        within(columnDialog).getByRole("checkbox", {
          name: "Select",
        }) as HTMLInputElement
      ).disabled,
    ).toBe(true);
    expect(
      (
        within(columnDialog).getByRole("checkbox", {
          name: "Inspect",
        }) as HTMLInputElement
      ).disabled,
    ).toBe(true);
    fireEvent.click(within(controls).getByRole("button", { name: "Filters" }));
    fireEvent.click(
      within(
        screen.getByRole("dialog", { name: "Datasets filters" }),
      ).getByRole("button", { name: /^Origin/ }),
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "Uploaded" }));
    expect(within(table).queryByRole("link", { name: "alpha" })).toBeNull();
    const beta = within(table).getByRole("link", { name: "beta" });
    expect(view.studio.evidence.pick(beta)?.data).toMatchObject({
      name: "beta",
      datapoints: 2,
    });
    fireEvent.click(
      within(controls).getByRole("button", { name: "Reset view" }),
    );
    expect(
      (
        screen.getByRole("checkbox", {
          name: "Select dataset alpha",
        }) as HTMLInputElement
      ).checked,
    ).toBe(true);
    expect(
      within(table).getByRole("columnheader", { name: "Snapshots" }),
    ).toBeTruthy();
    expect(table.closest("[data-density]")?.getAttribute("data-density")).toBe(
      "comfortable",
    );
  });
  it("keeps shared dataset view controls within the loaded server window and preserves the independent server pager", async () => {
    const get = vi.fn(async (path: string) =>
      path === "/api/snapshots"
        ? []
        : {
            meta: { buckets: { training: 100 } },
            total: 100,
            rows: [
              {
                id: 8,
                bucket: "training",
                messages: [{ role: "user", content: "Loaded first" }],
                labels: [],
              },
              {
                id: 9,
                bucket: "training",
                messages: [{ role: "user", content: "Loaded second" }],
                labels: [],
              },
            ],
          },
    );
    mount(<DatasetPage />, "/datasets/alpha", "/datasets/:name", get);
    await screen.findByRole("checkbox", { name: "Select datapoint 8" });
    expect(
      screen.queryByRole("combobox", { name: "Dataset records rows per page" }),
    ).toBeNull();
    expect(screen.getByText(/Loaded rows: 2 of 2/).textContent).toContain(
      "this loaded window",
    );
    const reads = get.mock.calls.length;
    fireEvent.click(
      within(screen.getByRole("table", { name: "Dataset records" })).getByRole(
        "button",
        { name: "User message" },
      ),
    );
    expect(get.mock.calls).toHaveLength(reads);
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() =>
      expect(
        get.mock.calls.some(([path]) => path.includes("offset=15&limit=15")),
      ).toBe(true),
    );
  });
  it("retains record state across keyboard tabs and revokes hidden assistant table controls", async () => {
    const view = mount(
      <DatasetPage />,
      "/datasets/alpha",
      "/datasets/:name",
      vi.fn(async (path: string) =>
        path === "/api/snapshots"
          ? []
          : {
              total: 1,
              meta: { buckets: { training: 1 } },
              rows: [
                {
                  id: 42,
                  bucket: "training",
                  labels: [],
                  messages: [
                    { role: "user", content: "Question" },
                    { role: "assistant", content: "Actual answer" },
                    { role: "tool", content: "Later tool result" },
                  ],
                },
              ],
            },
      ),
    );
    const table = await screen.findByRole("table", { name: "Dataset records" });
    expect(within(table).getByText("Actual answer")).toBeVisible();
    expect(within(table).queryByText("Later tool result")).toBeNull();
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select datapoint 42" }),
    );
    fireEvent.click(screen.getByRole("radio", { name: "Compact" }));
    const oldDensity = view.registry
      .inspect()
      .actions.find((item) => item.label === "Dataset records: row density")!;
    const recordsTab = screen.getByRole("tab", { name: "Records" });
    recordsTab.focus();
    fireEvent.keyDown(recordsTab, { key: "ArrowRight" });
    expect(screen.getByRole("tabpanel", { name: "Snapshots" })).toBeVisible();
    expect(screen.queryByRole("table", { name: "Dataset records" })).toBeNull();
    await expect(
      view.registry.execute(oldDensity.ref, "spacious", false),
    ).rejects.toThrow("stale");
    const select = view.registry
      .inspect()
      .actions.find((item) => item.id === "dataset.select")!;
    expect(select.disabled).toBe(true);
    fireEvent.keyDown(screen.getByRole("tab", { name: "Snapshots" }), {
      key: "ArrowLeft",
    });
    expect(screen.getByRole("radio", { name: "Compact" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(
      screen.getByRole("checkbox", { name: "Select datapoint 42" }),
    ).toBeChecked();
    expect(
      screen.getByRole("button", { name: "Compare selected" }),
    ).toBeDisabled();
  });
  it("opens a linked datapoint inspector without obscuring or changing the table filter", async () => {
    const get = vi.fn(async (path: string) =>
      path === "/api/snapshots"
        ? []
        : {
            meta: { buckets: { training: 1, validation: 0, uncategorized: 0 } },
            total: 1,
            rows: [
              {
                id: 42,
                bucket: "training",
                messages: [
                  { role: "user", content: "Turn on lamp" },
                  { role: "assistant", content: '{"on":true}' },
                ],
                labels: ["fixture"],
                source: "synthetic",
              },
            ],
          },
    );
    mount(<DatasetPage />, "/datasets/alpha", "/datasets/:name", get);
    fireEvent.click(
      await screen.findByRole("button", { name: /Turn on lamp/ }),
    );
    const inspector = screen.getByRole("complementary", {
      name: "Datapoint 42",
    });
    expect(within(inspector).getByText("Turn on lamp")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("table", { name: "Dataset records" })).toBeTruthy();
    fireEvent.click(
      within(inspector).getByRole("button", { name: "Back to results" }),
    );
    expect(
      screen.queryByRole("complementary", { name: "Datapoint 42" }),
    ).toBeNull();
    expect(screen.getByRole("radio", { name: /^All buckets/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });
  it("keeps dataset selection and bucket context when opening snapshots from local navigation", async () => {
    const get = vi.fn(async (path: string) =>
      path === "/api/snapshots"
        ? [
            {
              name: "frozen-v1",
              dataset: "alpha",
              training_count: 8,
              validation_count: 2,
            },
          ]
        : {
            meta: { buckets: { training: 1, validation: 0, uncategorized: 0 } },
            total: 1,
            rows: [
              {
                id: 42,
                bucket: "training",
                messages: [{ role: "user", content: "Retained record" }],
                labels: [],
              },
            ],
          },
    );
    mount(<DatasetPage />, "/datasets/alpha", "/datasets/:name", get);
    fireEvent.click(
      await screen.findByRole("checkbox", { name: "Select datapoint 42" }),
    );
    fireEvent.click(screen.getByRole("radio", { name: /^training/ }));
    const navigation = screen.getByRole("tablist", {
      name: "Dataset views",
    });
    expect(
      within(navigation)
        .getByRole("tab", { name: "Records" })
        .getAttribute("aria-selected"),
    ).toBe("true");
    fireEvent.click(within(navigation).getByRole("tab", { name: "Snapshots" }));
    const snapshots = await screen.findByRole("tabpanel", {
      name: "Snapshots",
    });
    expect(screen.queryByRole("table", { name: "Dataset records" })).toBeNull();
    expect(
      within(snapshots).getByRole("table", { name: "Snapshots" }).textContent,
    ).toContain("frozen-v1");
    fireEvent.click(within(navigation).getByRole("tab", { name: "Records" }));
    expect(screen.getByRole("radio", { name: /^training/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(
      (
        screen.getByRole("checkbox", {
          name: "Select datapoint 42",
        }) as HTMLInputElement
      ).checked,
    ).toBe(true);
    expect(
      screen.getByRole("table", { name: "Dataset records" }).textContent,
    ).toContain("Retained record");
  });
  it("links comparison bars, real metric rows, and a reference-aware case inspector", async () => {
    const rows = [
      {
        datapoint: 30,
        model: "lora",
        metric: "json",
        score: 0.5,
        user: "Set heating to 23",
        reference: '{"value":23}',
        prediction: '{"value":"23"}',
        reason: "Typed JSON mismatch",
      },
      {
        datapoint: 30,
        model: "lora",
        metric: "exact",
        score: 1,
        user: "Set heating to 23",
        reference: '{"value":23}',
        prediction: '{"value":"23"}',
        reason: "Normalized exact",
      },
    ];
    mount(
      <EvaluationPage />,
      "/evaluations/trial",
      "/evaluations/:name",
      vi.fn(async () => ({
        summary: {
          snapshot: "v1",
          datapoints: 1,
          models: ["lora"],
          metrics: ["json", "exact"],
          state: "succeeded",
          leaderboard: [
            { model: "lora", scores: { json: 0.5, exact: 1 }, overall: 0.75 },
          ],
        },
        rows,
      })),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Compare lora, exact" }),
    );
    expect(
      screen.getByRole("heading", { name: "Results · exact" }),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", {
        name: "Inspect score for datapoint 31, lora",
      }),
    );
    const inspector = screen.getByRole("complementary", {
      name: "Evaluation datapoint 31",
    });
    expect(within(inspector).getByText('{"value":23}')).toBeTruthy();
    expect(within(inspector).getByText("Normalized exact")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(
      within(inspector).getByRole("button", { name: "Back to results" }),
    );
    expect(
      screen.getByRole("heading", { name: "Results · exact" }),
    ).toBeTruthy();
  });
  it("selects one repository document collection without hiding its real add and query actions", async () => {
    const get = vi.fn(async (path: string) =>
      path === "/api/hub/models"
        ? {}
        : [
            {
              name: "alpha",
              description: "First collection",
              embedding_model: "embed",
              chunk_count: 2,
              documents: [{ name: "first.txt", kind: "txt", chunks: 2 }],
            },
            {
              name: "beta",
              description: "Second collection",
              embedding_model: "embed",
              chunk_count: 3,
              documents: [{ name: "second.txt", kind: "txt", chunks: 3 }],
            },
          ],
    );
    mount(<RepositoriesPage />, "/repositories", "/repositories", get);
    fireEvent.click(await screen.findByRole("button", { name: "beta" }));
    expect(
      screen.queryByRole("table", { name: "Documents in alpha" }),
    ).toBeNull();
    expect(
      within(
        screen.getByRole("table", { name: "Documents in beta" }),
      ).getByText("second.txt"),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add documents" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Query" })).toBeTruthy();
  });
});
