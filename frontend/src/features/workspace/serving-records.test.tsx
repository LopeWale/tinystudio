import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
  act,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { ModelsPage } from "./models";
import { TracesPage } from "./traces";
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function mount(component: React.ReactNode, get: any) {
  const studio = {
    api: { get, post: vi.fn(async () => ({})) },
    project: "fixture",
    scopeKey: "u:fixture",
    user: { id: "u" },
    route: "/models",
    evidence: new EvidenceRegistry(),
    refreshOverview: vi.fn(),
    confirm: vi.fn(),
    toast: vi.fn(),
  } as any;
  render(
    <StudioContext.Provider value={studio}>
      <MemoryRouter>{component}</MemoryRouter>
    </StudioContext.Provider>,
  );
  return studio;
}
describe("model and trace collection workspaces", () => {
  it("marks the initial trace read busy until a successful empty page arrives", async () => {
    let resolve!: (rows: unknown[]) => void;
    mount(
      <TracesPage />,
      vi.fn((path: string) =>
        path.startsWith("/api/traces")
          ? new Promise((done) => {
              resolve = done;
            })
          : Promise.resolve([]),
      ),
    );
    const table = screen.getByRole("table", {
      name: "Recent inference traces",
    });
    expect(table.closest(".trace-table-host")).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(
      screen.getByRole("heading", { name: "Loading traces" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "No traces available" }),
    ).toBeNull();
    await act(async () => resolve([]));
    expect(
      await screen.findByRole("heading", { name: "No traces available" }),
    ).toBeInTheDocument();
    expect(table.closest(".trace-table-host")).toHaveAttribute(
      "aria-busy",
      "false",
    );
  });
  it("shows initial trace permission failures as failed reads and retries without writing", async () => {
    const traces = vi
      .fn()
      .mockRejectedValueOnce(
        Object.assign(new Error("Trace access denied"), { status: 403 }),
      )
      .mockResolvedValueOnce([]);
    const get = vi.fn(async (path: string) =>
      path.startsWith("/api/traces") ? traces() : [],
    );
    const studio = mount(<TracesPage />, get);
    expect(
      await screen.findByRole("heading", {
        name: "Traces could not be loaded",
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "No traces available" }),
    ).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("Trace access denied");
    expect(screen.queryByRole("button", { name: /^Refresh$/ })).toBeNull();
    expect(
      screen.getAllByRole("button", { name: "Retry traces" }),
    ).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Retry traces" }));
    expect(
      await screen.findByRole("heading", { name: "No traces available" }),
    ).toBeInTheDocument();
    expect(traces).toHaveBeenCalledTimes(2);
    expect(studio.api.post).not.toHaveBeenCalled();
  });
  it("retains trace rows and inspection during refresh failure and clears local no-match search without reading", async () => {
    const row = {
      id: "retained",
      model: "base",
      messages: [{ role: "user", content: "Retained prompt" }],
      response: "Retained answer",
    };
    let reject!: (error: Error) => void;
    const read = vi
      .fn()
      .mockResolvedValueOnce([row])
      .mockImplementationOnce(
        () =>
          new Promise((_resolve, fail) => {
            reject = fail;
          }),
      )
      .mockResolvedValueOnce([row]);
    const get = vi.fn(async (path: string) =>
      path.startsWith("/api/traces") ? read() : [],
    );
    const studio = mount(<TracesPage />, get);
    fireEvent.click(
      await screen.findByRole("button", { name: "Inspect trace" }),
    );
    const inspector = screen.getByRole("complementary", {
      name: "Trace inspector",
    });
    const retainedReview = within(inspector).getByRole("button", {
      name: "Mark response correct: retained",
    });
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(screen.getByRole("complementary", { name: "Trace inspector" })).toBe(
      inspector,
    );
    expect(within(inspector).getByText("Retained answer")).toBeVisible();
    expect(retainedReview).toBeVisible();
    expect(retainedReview).toBeEnabled();
    expect(
      screen.getByRole("checkbox", { name: "Select trace retained" }),
    ).toBeInTheDocument();
    await act(async () => reject(new Error("Trace refresh offline")));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Trace refresh offline",
    );
    expect(screen.getByRole("complementary", { name: "Trace inspector" })).toBe(
      inspector,
    );
    expect(within(inspector).getByText("Retained answer")).toBeVisible();
    expect(
      within(inspector).getByRole("button", {
        name: "Mark response correct: retained",
      }),
    ).toBe(retainedReview);
    expect(retainedReview).toBeVisible();
    expect(retainedReview).toBeEnabled();
    expect(
      screen.getByRole("checkbox", { name: "Select trace retained" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    fireEvent.change(
      screen.getByRole("searchbox", { name: "Find a trace on this page" }),
      { target: { value: "missing" } },
    );
    expect(
      screen.getByRole("heading", { name: "No matching traces" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "No traces available" }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Clear trace search" }));
    expect(
      screen.getByRole("checkbox", { name: "Select trace retained" }),
    ).toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(3);
    expect(studio.api.post).not.toHaveBeenCalled();
  });
  it.each(["models", "traces"] as const)(
    "keeps %s no-match search mounted and clearable without losing focus",
    async (kind) => {
      const model = {
        id: "org/one",
        alias: "One",
        kind: "base",
        base_model: "org/one",
        status: "down",
      };
      const trace = {
        id: "first",
        model: "base",
        messages: [{ role: "user", content: "First prompt" }],
        response: "First answer",
      };
      const get = vi.fn(async (path: string) =>
        path === "/api/models"
          ? [model]
          : path.startsWith("/api/traces")
            ? [trace]
            : path === "/api/hub/models"
              ? { models: [], token: { configured: false } }
              : [],
      );
      mount(kind === "models" ? <ModelsPage /> : <TracesPage />, get);
      const label =
        kind === "models"
          ? "Find an inventory model"
          : "Find a trace on this page";
      const rowLabel =
        kind === "models" ? "Select model One" : "Select trace first";
      await screen.findByRole("checkbox", { name: rowLabel });
      const search = screen.getByRole("searchbox", { name: label });
      const table = screen.getByRole("table", {
        name: kind === "models" ? "Model inventory" : "Recent inference traces",
      });
      const user = userEvent.setup();
      await user.type(search, "missing-record");
      expect(screen.queryByRole("checkbox", { name: rowLabel })).toBeNull();
      expect(screen.getByRole("searchbox", { name: label })).toBe(search);
      expect(search).toHaveFocus();
      expect(within(table).getAllByRole("row")).toHaveLength(1);
      const fetchesBeforeClear = get.mock.calls.length;
      await user.clear(search);
      expect(search).toHaveValue("");
      expect(search).toHaveFocus();
      expect(
        screen.getByRole("checkbox", { name: rowLabel }),
      ).toBeInTheDocument();
      expect(get).toHaveBeenCalledTimes(fetchesBeforeClear);
    },
  );

  it("distinguishes model selection from inspection and preserves the collection DOM and scroll across comparison", async () => {
    mount(
      <ModelsPage />,
      vi.fn(async (path: string) =>
        path === "/api/models"
          ? [
              {
                id: "org/one",
                alias: "One",
                kind: "base",
                base_model: "org/one",
                parameters: 1000000,
                status: "down",
              },
              {
                id: "org/two",
                alias: "Two",
                kind: "base",
                base_model: "org/two",
                status: "up",
              },
            ]
          : { models: [], token: { configured: false }, free_disk_gb: 7 },
      ),
    );
    fireEvent.click(
      await screen.findByRole("checkbox", { name: "Select model One" }),
    );
    expect(screen.queryByRole("complementary")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Inspect model Two" }));
    expect(screen.getByRole("complementary", { name: "Two" })).toBeTruthy();
    expect(
      (
        screen.getByRole("checkbox", {
          name: "Select model Two",
        }) as HTMLInputElement
      ).checked,
    ).toBe(false);
    fireEvent.click(screen.getByRole("checkbox", { name: "Select model Two" }));
    const collection = screen.getByRole("region", {
      name: "Model inventory rows",
    });
    collection.scrollTop = 75;
    fireEvent.scroll(collection);
    fireEvent.click(screen.getByRole("button", { name: "Compare selected" }));
    const table = screen.getByRole("table", { name: "Record comparison" });
    const params = within(table).getByRole("row", { name: /Parameters/ });
    expect(within(params).getByText("1.0 M")).toBeTruthy();
    expect(within(params).getByText("—")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Back to collection" }));
    expect(screen.getByRole("region", { name: "Model inventory rows" })).toBe(
      collection,
    );
    expect(collection.scrollTop).toBe(75);
    expect(screen.getByRole("complementary", { name: "Two" })).toBeTruthy();
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Compare selected" }),
    );
  });
  it("keeps checked trace identities while inspecting a different response and shows unscored values separately from zero", async () => {
    mount(
      <TracesPage />,
      vi.fn(async (path: string) =>
        path.startsWith("/api/traces")
          ? [
              {
                id: "first",
                model: "base",
                messages: [{ role: "user", content: "First prompt" }],
                response: "First answer",
                score: 0,
              },
              {
                id: "second",
                model: "tuned",
                messages: [{ role: "user", content: "Second prompt" }],
                response: "Second answer",
                score: null,
                corrected: "Reviewed answer",
              },
            ]
          : [],
      ),
    );
    fireEvent.click(
      await screen.findByRole("checkbox", { name: "Select trace first" }),
    );
    fireEvent.click(
      screen.getAllByRole("button", { name: "Inspect trace" })[1],
    );
    expect(
      within(
        screen.getByRole("complementary", { name: "Trace inspector" }),
      ).getByText("Reviewed answer"),
    ).toBeTruthy();
    expect(
      (
        screen.getByRole("checkbox", {
          name: "Select trace second",
        }) as HTMLInputElement
      ).checked,
    ).toBe(false);
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select trace second" }),
    );
    fireEvent.change(
      screen.getByRole("searchbox", { name: "Find a trace on this page" }),
      { target: { value: "Second" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Compare selected" }));
    const score = within(
      screen.getByRole("table", { name: "Record comparison" }),
    ).getByRole("row", { name: /Score/ });
    expect(within(score).getByText("0.0%")).toBeTruthy();
    expect(within(score).getByText("Unscored")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Back to collection" }));
    expect(
      (
        screen.getByRole("searchbox", {
          name: "Find a trace on this page",
        }) as HTMLInputElement
      ).value,
    ).toBe("Second");
    expect(screen.getByText("2 selected")).toBeTruthy();
  });
  it("keeps exact trace inspection reachable when optional columns are hidden", async () => {
    mount(
      <TracesPage />,
      vi.fn(async (path: string) =>
        path.includes("/api/traces")
          ? [
              {
                id: "first",
                model: "base",
                messages: [{ role: "user", content: "First prompt" }],
                response: "First answer",
              },
              {
                id: "second",
                model: "tuned",
                messages: [{ role: "user", content: "Second prompt" }],
                response: "Second answer",
              },
            ]
          : [],
      ),
    );
    await screen.findByRole("checkbox", { name: "Select trace second" });
    fireEvent.click(screen.getByRole("button", { name: "Columns" }));
    const columns = await screen.findByRole("dialog", {
      name: "Recent inference traces visible columns",
    });
    expect(
      within(columns).getByRole("checkbox", { name: "User message" }),
    ).toBeDisabled();
    fireEvent.click(
      within(columns).getByRole("checkbox", { name: "Response" }),
    );
    const table = screen.getByRole("table", {
      name: "Recent inference traces",
    });
    expect(
      within(table).queryByRole("columnheader", { name: "Response" }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Columns" }));
    const row = within(table)
      .getByRole("checkbox", { name: "Select trace second" })
      .closest("tr")!;
    within(row).getByRole("button", { name: "Inspect trace" }).focus();
    await userEvent.setup().keyboard("{Enter}");
    const inspector = screen.getByRole("complementary", {
      name: "Trace inspector",
    });
    expect(within(inspector).getByText("Second answer")).toBeTruthy();
    expect(within(inspector).queryByText("First answer")).toBeNull();
  });
  it("sorts inventory by numeric parameter values while retaining row evidence and exact model actions", async () => {
    const studio = mount(
      <ModelsPage />,
      vi.fn(async (path: string) =>
        path === "/api/models"
          ? [
              {
                id: "org/ten",
                alias: "Ten",
                kind: "base",
                base_model: "org/ten",
                parameters: 10000000,
                status: "down",
              },
              {
                id: "org/two",
                alias: "Two",
                kind: "base",
                base_model: "org/two",
                parameters: 2000000,
                status: "down",
              },
              {
                id: "org/nine",
                alias: "Nine",
                kind: "base",
                base_model: "org/nine",
                parameters: 9000000,
                status: "down",
              },
            ]
          : { models: [], token: { configured: false }, free_disk_gb: 7 },
      ),
    );
    await screen.findByRole("button", { name: "Inspect model Ten" });
    const table = screen.getByRole("table", { name: "Model inventory" });
    fireEvent.click(within(table).getByRole("button", { name: "Parameters" }));
    const rows = within(table).getAllByRole("row").slice(1);
    expect(
      rows.map((row) =>
        within(row).getByRole("checkbox").getAttribute("aria-label"),
      ),
    ).toEqual(["Select model Two", "Select model Nine", "Select model Ten"]);
    expect(rows[0]).toHaveAttribute("data-evidence-label", "Model");
    expect(
      studio.evidence
        .snapshot()
        .some(
          (record: any) =>
            record.label === "Model" && record.data.id === "org/two",
        ),
    ).toBe(true);
    fireEvent.click(screen.getByRole("radio", { name: "Spacious" }));
    expect(table.closest(".data-table")).toHaveAttribute(
      "data-density",
      "spacious",
    );
    fireEvent.click(within(rows[0]).getByRole("button", { name: "Load" }));
    expect(studio.api.post).toHaveBeenCalledWith("/api/models/org~two/up", {});
  });
});
