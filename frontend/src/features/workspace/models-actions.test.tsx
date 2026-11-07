import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ModelsPage } from "./models";
import { TracesPage } from "./traces";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { ComponentActionRegistry } from "../../controllers/componentActions";
import { ComponentActionsProvider } from "../assistant/actions";
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
it.each([
  { kind: "models", remove: 0 },
  { kind: "traces", remove: 0 },
  { kind: "models", remove: 1 },
  { kind: "traces", remove: 1 },
  { kind: "models", remove: 2 },
  { kind: "traces", remove: 2 },
] as const)(
  "revokes hidden $kind actions and restores retained state/focus after removing $remove compared records",
  async ({ kind, remove }) => {
    const modelView = kind === "models";
    const tableName = modelView ? "Model inventory" : "Recent inference traces";
    const searchName = modelView
      ? "Find an inventory model"
      : "Find a trace on this page";
    const sortColumn = modelView ? "Model" : "User message";
    const filterColumn = modelView ? "Status" : "Score";
    const rowLabels = modelView
      ? ["Select model Keep Alpha", "Select model Keep Beta"]
      : ["Select trace Alpha", "Select trace Beta"];
    const rows = ["Alpha", "Beta"].map((name) => ({
      id: `org/${name}`,
      alias: `Keep ${name}`,
      kind: "base",
      base_model: "org/base",
      status: "loaded",
    }));
    const traces = ["Alpha", "Beta"].map((name) => ({
      id: name,
      model: "org/base",
      messages: [{ role: "user", content: `Keep ${name}` }],
      response: "Retained answer",
      score: 1,
    }));
    const api = {
      get: vi.fn(async (path: string) =>
        path === "/api/models"
          ? rows
          : path.startsWith("/api/traces")
            ? traces
            : path === "/api/datasets"
              ? []
              : { models: [], token: { configured: false } },
      ),
      post: vi.fn(),
      put: vi.fn(),
      del: vi.fn(),
    };
    const registry = new ComponentActionRegistry();
    registry.setScope({ user: "u", project: "p", route: `/${kind}` });
    const studio = {
      api,
      project: "p",
      scopeKey: "u:p",
      user: { id: "u" },
      route: `/${kind}`,
      evidence: new EvidenceRegistry(),
      refreshOverview: vi.fn(),
      confirm: vi.fn(),
      toast: vi.fn(),
    } as any;
    render(
      <StudioContext.Provider value={studio}>
        <MemoryRouter>
          <ComponentActionsProvider registry={registry}>
            {modelView ? <ModelsPage /> : <TracesPage />}
          </ComponentActionsProvider>
        </MemoryRouter>
      </StudioContext.Provider>,
    );
    await screen.findByRole("checkbox", { name: rowLabels[0] });
    const table = screen.getByRole("table", { name: tableName });
    const actions = () =>
      registry
        .inspect()
        .actions.filter((item) => item.label.startsWith(`${tableName}:`));
    const action = (label: string) =>
      actions().find((item) => item.label === `${tableName}: ${label}`)!;
    fireEvent.change(screen.getByRole("searchbox", { name: searchName }), {
      target: { value: "Keep" },
    });
    await act(async () => {
      await registry.execute(action("row density").ref, "compact", false);
    });
    await act(async () => {
      await registry.execute(
        action(`sort ${sortColumn}`).ref,
        "descending",
        false,
      );
    });
    const statusFilter = action(`filter ${filterColumn}`);
    const loaded = statusFilter.options!.find(
      (option) => option.label === (modelView ? "loaded" : "1"),
    )!;
    await act(async () => {
      await registry.execute(statusFilter.ref, loaded.value, false);
    });
    fireEvent.click(screen.getByRole("checkbox", { name: rowLabels[0] }));
    fireEvent.click(screen.getByRole("checkbox", { name: rowLabels[1] }));
    const before = actions().map(({ label, state }) => ({ label, state }));
    const oldReset = action("reset table view").ref;
    const oldDensity = action("row density").ref;
    let inspectorRef: string | undefined, closeRef: string | undefined;
    if (!modelView) {
      inspectorRef = registry
        .inspect()
        .actions.find((item) => item.id === "traces.inspect")!.ref;
      await act(async () => {
        await registry.execute(inspectorRef, "Alpha", false);
      });
      closeRef = registry
        .inspect()
        .actions.find((item) => item.id === "traces.close")!.ref;
    }
    const reads = api.get.mock.calls.length;
    const compareButton = screen.getByRole("button", {
      name: "Compare selected",
    });
    fireEvent.click(compareButton);
    expect(
      screen.getByRole("region", { name: "Compare records" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: tableName })).toBeNull();
    expect(actions()).toEqual([]);
    if (modelView) {
      for (const id of ["models.hub", "models.query", "models.refresh"]) {
        expect(
          registry.inspect().actions.find((item) => item.id === id),
        ).toMatchObject({ disabled: false });
      }
    }
    if (!modelView) {
      expect(
        registry.inspect().actions.find((item) => item.id === "traces.inspect"),
      ).toMatchObject({ disabled: true, state: { selected: "Alpha" } });
      expect(
        registry.inspect().actions.find((item) => item.id === "traces.close"),
      ).toMatchObject({ disabled: true });
      await expect(
        registry.execute(inspectorRef, "Beta", false),
      ).rejects.toThrow("currently disabled");
      await expect(
        registry.execute(closeRef, undefined, false),
      ).rejects.toThrow("currently disabled");
    }

    await expect(registry.execute(oldReset, undefined, false)).rejects.toThrow(
      /stale|disabled/,
    );
    await expect(
      registry.execute(oldDensity, "spacious", false),
    ).rejects.toThrow(/stale|disabled/);
    for (let index = 0; index < remove; index++) {
      fireEvent.click(
        within(
          screen.getByRole("region", { name: "Compare records" }),
        ).getByRole("button", {
          name: `Remove ${modelView ? `Keep ${index ? "Beta" : "Alpha"}` : index ? "Beta" : "Alpha"}`,
        }),
      );
    }
    const focusAttempts: boolean[] = [];
    const focus = compareButton.focus.bind(compareButton);
    vi.spyOn(compareButton, "focus").mockImplementation(() => {
      const hidden = Boolean(compareButton.closest("[hidden]"));
      focusAttempts.push(hidden);
      // Unlike jsdom, a browser refuses focus into a hidden ancestor.
      if (!hidden) focus();
    });
    fireEvent.click(screen.getByRole("button", { name: "Back to collection" }));
    expect(focusAttempts).toEqual(remove ? [] : [false]);
    expect(
      remove
        ? screen.getByRole("searchbox", { name: searchName })
        : compareButton,
    ).toHaveFocus();
    expect(screen.getByRole("table", { name: tableName })).toBe(table);
    expect(table.closest(".data-table")).toHaveAttribute(
      "data-density",
      "compact",
    );
    expect(
      within(table).getByRole("columnheader", { name: new RegExp(sortColumn) }),
    ).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByRole("searchbox", { name: searchName })).toHaveValue(
      "Keep",
    );
    expect(
      (screen.getByRole("checkbox", { name: rowLabels[0] }) as HTMLInputElement)
        .checked,
    ).toBe(!remove);
    expect(
      (screen.getByRole("checkbox", { name: rowLabels[1] }) as HTMLInputElement)
        .checked,
    ).toBe(remove !== 2);
    expect(actions().map(({ label, state }) => ({ label, state }))).toEqual(
      before,
    );
    expect(action("row density").ref).not.toBe(oldDensity);
    if (!modelView) {
      expect(
        registry.inspect().actions.find((item) => item.id === "traces.inspect"),
      ).toMatchObject({ disabled: false, state: { selected: "Alpha" } });
      expect(
        registry.inspect().actions.find((item) => item.id === "traces.close"),
      ).toMatchObject({ disabled: false });
    }

    expect(api.get).toHaveBeenCalledTimes(reads);
    expect(api.post).not.toHaveBeenCalled();
    expect(api.put).not.toHaveBeenCalled();
    expect(api.del).not.toHaveBeenCalled();
  },
);
