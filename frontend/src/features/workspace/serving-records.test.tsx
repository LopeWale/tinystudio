import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
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
    fireEvent.click(screen.getByRole("button", { name: "Two" }));
    expect(screen.getByRole("complementary", { name: "Two" })).toBeTruthy();
    expect(
      (
        screen.getByRole("checkbox", {
          name: "Select model Two",
        }) as HTMLInputElement
      ).checked,
    ).toBe(false);
    fireEvent.click(screen.getByRole("checkbox", { name: "Select model Two" }));
    const collection = screen.getByRole("region", { name: "Model collection" });
    collection.scrollTop = 75;
    fireEvent.scroll(collection);
    fireEvent.click(screen.getByRole("button", { name: "Compare selected" }));
    const table = screen.getByRole("table", { name: "Record comparison" });
    const params = within(table).getByRole("row", { name: /Parameters/ });
    expect(within(params).getByText("1.0 M")).toBeTruthy();
    expect(within(params).getByText("—")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Back to collection" }));
    expect(screen.getByRole("region", { name: "Model collection" })).toBe(
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
    await screen.findByRole("button", { name: "Ten" });
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
