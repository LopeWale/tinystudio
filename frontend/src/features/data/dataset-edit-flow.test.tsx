import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { DatasetPage } from "./datasets";

afterEach(cleanup);

function mountDataset({
  post = vi.fn(async () => undefined),
  put = vi.fn(async () => undefined),
  labels = [] as { name: string; description: string }[],
}: {
  post?: (path: string, body: unknown) => Promise<unknown>;
  put?: (path: string, body: unknown) => Promise<unknown>;
  labels?: { name: string; description: string }[];
} = {}) {
  const api = {
    get: vi.fn(async (path: string) =>
      path === "/api/snapshots"
        ? []
        : {
            meta: {
              buckets: { uncategorized: 1, training: 10, validation: 1 },
              labels,
            },
            total: 12,
            rows: [],
          },
    ),
    post,
    put,
  };
  const runtime = {
    api,
    project: "fixture",
    scopeKey: "u:fixture",
    user: { id: "u" },
    route: "/datasets/fixture",
    evidence: new EvidenceRegistry(),
    overview: { tree: { datasets: [] } },
    toast: vi.fn(),
    confirm: vi.fn(async () => true),
    navigate: vi.fn(),
    trackTask: vi.fn(),
    showTasks: vi.fn(),
    refreshOverview: vi.fn(async () => {}),
  } as any;
  render(
    <StudioContext.Provider value={runtime}>
      <MemoryRouter initialEntries={["/datasets/fixture"]}>
        <Routes>
          <Route path="/datasets/:name" element={<DatasetPage />} />
        </Routes>
      </MemoryRouter>
    </StudioContext.Provider>,
  );
  return api;
}

async function openAction(name: string) {
  const menu = (await screen.findByText("Dataset actions")).closest("details")!;
  fireEvent.click(within(menu).getByText("Dataset actions"));
  fireEvent.click(within(menu).getByRole("button", { name }));
  return within(screen.getByRole("dialog"));
}

// Payload expectations come from the public route bodies in api/routes/data.py
// and the requested UI behavior: trimmed conversations/names, preserved descriptions,
// zero temperature, and the existing blank-number conversion delegated to API validation.
describe("dataset editor save flows", () => {
  it("submits trimmed conversation content after collapsing system context and prevents duplicate pending saves", async () => {
    let resolve!: () => void;
    const post = vi.fn(
      () =>
        new Promise<void>((yes) => {
          resolve = yes;
        }),
    );
    mountDataset({ post });
    fireEvent.click(
      await screen.findByRole("button", { name: "Add datapoint" }),
    );
    const dialog = within(screen.getByRole("dialog"));
    fireEvent.change(dialog.getByRole("textbox", { name: "user" }), {
      target: { value: "  How does it work?  " },
    });
    fireEvent.change(dialog.getByRole("textbox", { name: "assistant" }), {
      target: { value: "  Here is the answer.  " },
    });
    const summary = dialog.getByText("System context");
    fireEvent.click(summary);
    fireEvent.change(dialog.getByRole("textbox", { name: "system" }), {
      target: { value: "  Be concise.  " },
    });
    fireEvent.click(summary);
    expect(summary.closest("details")).not.toHaveAttribute("open");
    const save = dialog.getByRole("button", { name: "Save" });
    fireEvent.click(save);
    fireEvent.click(save);
    expect(post.mock.calls).toEqual([
      [
        "/api/datapoints",
        {
          dataset: "fixture",
          bucket: "uncategorized",
          messages: [
            { role: "system", content: "Be concise." },
            { role: "user", content: "How does it work?" },
            { role: "assistant", content: "Here is the answer." },
          ],
        },
      ],
    ]);
    for (const input of screen
      .getByRole("dialog")
      .querySelectorAll("input, textarea, select"))
      expect(input).toBeDisabled();
    expect(save).toBeDisabled();
    await act(async () => resolve());
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(post).toHaveBeenCalledOnce();
  });

  it("recovers from removing all vocabulary rows and submits the new label list", async () => {
    const put = vi.fn(async () => undefined);
    mountDataset({
      put,
      labels: [{ name: "Old", description: "Old description" }],
    });
    const dialog = await openAction("Labels");
    fireEvent.click(dialog.getByRole("button", { name: "Remove label 1" }));
    expect(dialog.getByText(/No labels yet/)).toBeVisible();
    fireEvent.click(dialog.getByRole("button", { name: "Add label" }));
    fireEvent.change(dialog.getByRole("textbox", { name: "Label 1 name" }), {
      target: { value: "  Support  " },
    });
    fireEvent.change(
      dialog.getByRole("textbox", { name: "Label 1 description" }),
      { target: { value: "  Customer questions  " } },
    );
    fireEvent.click(dialog.getByRole("button", { name: "Save" }));
    expect(put.mock.calls).toEqual([
      [
        "/api/datasets/fixture/labels",
        {
          labels: [{ name: "Support", description: "  Customer questions  " }],
        },
      ],
    ]);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it.each([
    { draft: "7", count: 7 },
    { draft: "", count: 0 },
  ])(
    "preserves enrichment conversion for count draft '$draft' and zero temperature",
    async ({ draft, count }) => {
      const post = vi.fn(async () => {
        throw Error("API validation response");
      });
      mountDataset({ post });
      const dialog = await openAction("Enrich");
      fireEvent.change(
        dialog.getByRole("spinbutton", { name: "New datapoints" }),
        { target: { value: draft } },
      );
      fireEvent.change(
        dialog.getByRole("spinbutton", { name: "Temperature" }),
        { target: { value: "0" } },
      );
      fireEvent.change(dialog.getByRole("textbox", { name: "Instructions" }), {
        target: { value: "Keep examples short" },
      });
      fireEvent.change(dialog.getByRole("textbox", { name: "Avoid" }), {
        target: { value: "Duplicates" },
      });
      fireEvent.click(dialog.getByRole("button", { name: "Save" }));
      expect(post.mock.calls).toEqual([
        [
          "/api/augment",
          {
            dataset: "fixture",
            count,
            temperature: 0,
            instructions: "Keep examples short",
            avoid: "Duplicates",
          },
        ],
      ]);
      expect(await dialog.findByRole("alert")).toHaveTextContent(
        "API validation response",
      );
      expect(
        dialog.getByRole("spinbutton", { name: "Temperature" }),
      ).toHaveValue(0);
      expect(
        dialog.getByRole("spinbutton", { name: "New datapoints" }),
      ).toHaveValue(draft === "" ? null : 7);
      expect(dialog.getByRole("button", { name: "Save" })).toBeEnabled();
    },
  );
});
