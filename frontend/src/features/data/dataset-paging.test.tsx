import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { DatasetPage } from "./datasets";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
function setup() {
  let total = 63,
    rejected = false;
  const get = vi.fn(async (path: string) => {
    if (path === "/api/snapshots") return [];
    if (rejected) throw Error("Unavailable");
    const url = new URL(path, "http://fixture");
    const offset = Number(url.searchParams.get("offset")),
      limit = Number(url.searchParams.get("limit"));
    const bucket = url.searchParams.get("bucket");
    const count = bucket === "validation" ? 0 : bucket ? 3 : total;
    return {
      total,
      meta: { buckets: { training: 3, validation: 0 } },
      rows: Array.from(
        { length: Math.max(0, Math.min(limit, count - offset)) },
        (_, i) => ({
          id: offset + i,
          bucket: bucket || "training",
          source: (offset + i) % 2 === 0 ? "uploaded" : "synthetic",
          messages: [{ role: "user", content: `Question ${offset + i}` }],
          labels: [],
        }),
      ),
    };
  });
  const studio = {
    api: { get },
    project: "alpha",
    scopeKey: "u:alpha",
    user: { id: "u" },
    route: "/datasets/demo",
    evidence: new EvidenceRegistry(),
    overview: {},
    toast: vi.fn(),
  } as any;
  const tree = () => (
    <StudioContext.Provider value={studio}>
      <MemoryRouter initialEntries={["/datasets/demo"]}>
        <Routes>
          <Route path="/datasets/:name" element={<DatasetPage />} />
        </Routes>
      </MemoryRouter>
    </StudioContext.Provider>
  );
  const view = render(tree());
  return {
    get,
    studio,
    rerender: () => view.rerender(tree()),
    total: (n: number) => {
      total = n;
    },
    reject: (b: boolean) => {
      rejected = b;
    },
  };
}
const table = () => screen.getByRole("table", { name: "Dataset records" });
const footer = () =>
  document.querySelector<HTMLElement>(".data-table__footer")!;
it("requests actual manual windows, preserves selection and inspection, and uses bucket totals including zero", async () => {
  const f = setup();
  await screen.findByRole("button", {
    name: "Inspect datapoint 0: Question 0",
  });
  expect(footer()).toHaveTextContent("Rows 1–25 of 63");
  expect(within(footer()).getByRole("status")).toBeVisible();
  expect(footer()).toHaveTextContent("Filters/sort: this page");
  expect(
    screen
      .getByRole("tabpanel", { name: "Records" })
      .querySelectorAll(".data-table__footer"),
  ).toHaveLength(1);
  fireEvent.change(screen.getByLabelText("Dataset records rows per page"), {
    target: { value: "10" },
  });
  await waitFor(() =>
    expect(within(table()).getAllByRole("row")).toHaveLength(11),
  );
  fireEvent.click(screen.getByRole("checkbox", { name: "Select datapoint 0" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Inspect datapoint 0: Question 0" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("button", {
    name: "Inspect datapoint 10: Question 10",
  });
  expect(screen.getByText("Outside the current page")).toBeInTheDocument();
  expect(screen.getByText("1 selected")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Previous" }));
  expect(
    await screen.findByRole("checkbox", { name: "Select datapoint 0" }),
  ).toBeChecked();
  fireEvent.click(screen.getByRole("radio", { name: /training/ }));
  await waitFor(() => expect(footer()).toHaveTextContent("Rows 1–3 of 3"));
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  fireEvent.click(screen.getByRole("radio", { name: /validation/ }));
  await waitFor(() => expect(footer()).toHaveTextContent("Rows 0–0 of 0"));
  expect(screen.getAllByText("No datapoints in this view.")).toHaveLength(1);
  expect(screen.queryByText("No records available.")).not.toBeInTheDocument();
  const showAll = screen.getByRole("button", { name: "Show all buckets" });
  expect(showAll.closest(".data-table__scroll")).not.toBeNull();
  expect(
    showAll.compareDocumentPosition(footer()) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();

  expect(
    f.get.mock.calls.some(([path]) =>
      path.includes("limit=10&bucket=training"),
    ),
  ).toBe(true);
});
it("does not clamp a failed page and retries accessibly; successful shrink recovers the last valid page", async () => {
  const f = setup();
  await screen.findByRole("button", {
    name: "Inspect datapoint 0: Question 0",
  });
  f.reject(true);
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("button", { name: "Retry dataset records" });
  expect(footer()).toHaveTextContent("Page unavailable");
  expect(
    f.get.mock.calls.filter(([path]) => path.includes("offset=0")),
  ).toHaveLength(1);
  f.reject(false);
  f.total(8);
  fireEvent.click(
    screen.getByRole("button", { name: "Retry dataset records" }),
  );
  await waitFor(() => expect(footer()).toHaveTextContent("Rows 1–8 of 8"));
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
});
it("starts a different scope at its own first window and clears checked records", async () => {
  const f = setup();
  await screen.findByRole("button", {
    name: "Inspect datapoint 0: Question 0",
  });
  fireEvent.click(screen.getByRole("checkbox", { name: "Select datapoint 0" }));
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("button", {
    name: "Inspect datapoint 25: Question 25",
  });
  f.get.mockClear();
  f.studio.project = "beta";
  f.studio.scopeKey = "u:beta";
  f.rerender();
  await screen.findByRole("button", {
    name: "Inspect datapoint 0: Question 0",
  });
  expect(
    screen.getByRole("checkbox", { name: "Select datapoint 0" }),
  ).not.toBeChecked();
  expect(
    f.get.mock.calls.filter(([path]) => path.includes("offset=25")),
  ).toHaveLength(0);
});
it("turns measured Fit capacity into requests and realigns manual size atomically", async () => {
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(
    function (this: HTMLElement) {
      return this.classList.contains("data-table__scroll") ? 337 : 0;
    },
  );
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(900);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    function (this: HTMLElement) {
      return {
        height: this.tagName === "THEAD" ? 40 : this.tagName === "TR" ? 44 : 0,
        width: 900,
      } as DOMRect;
    },
  );
  const f = setup();
  await waitFor(() => expect(footer()).toHaveTextContent("Rows 1–6 of 63"));
  expect(within(table()).getAllByRole("row")).toHaveLength(7);
  expect(
    f.get.mock.calls.some(([path]) => path.includes("offset=0&limit=6")),
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(footer()).toHaveTextContent("Rows 7–12 of 63"));
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(footer()).toHaveTextContent("Rows 13–18 of 63"));
  f.get.mockClear();
  fireEvent.change(screen.getByLabelText("Dataset records rows per page"), {
    target: { value: "10" },
  });
  await waitFor(() => expect(footer()).toHaveTextContent("Rows 11–20 of 63"));
  expect(
    f.get.mock.calls.filter(([path]) => path.startsWith("/api/datasets/")),
  ).toEqual([
    ["/api/datasets/demo?offset=10&limit=10", expect.any(AbortSignal)],
  ]);
});
it("ignores a late window after scope replacement and disables navigation while pending", async () => {
  const f = setup();
  await screen.findByRole("button", {
    name: "Inspect datapoint 0: Question 0",
  });
  let resolve!: (value: any) => void;
  f.get.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(footer()).toHaveTextContent("Loading page");
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
  f.studio.user = { id: "other" };
  f.studio.scopeKey = "other:alpha";
  f.rerender();
  await screen.findByRole("button", {
    name: "Inspect datapoint 0: Question 0",
  });
  await act(async () =>
    resolve({
      total: 80,
      rows: [{ id: 999, messages: [{ role: "user", content: "Late row" }] }],
    }),
  );
  expect(screen.queryByText("Late row")).not.toBeInTheDocument();
  expect(footer()).toHaveTextContent("Rows 1–25 of 63");
});

it("retains local filters and sorting across server pages while reporting the fetched window", async () => {
  const f = setup();
  await screen.findByRole("button", {
    name: "Inspect datapoint 0: Question 0",
  });
  fireEvent.click(screen.getByRole("button", { name: "Filters" }));
  fireEvent.click(
    within(
      screen.getByRole("dialog", { name: "Dataset records filters" }),
    ).getByRole("button", { name: /^Data type/ }),
  );
  fireEvent.click(screen.getByRole("checkbox", { name: "uploaded" }));
  fireEvent.click(screen.getByRole("button", { name: /^Filters/ }));
  const order = within(table()).getByRole("button", { name: "#" });
  fireEvent.click(order);
  fireEvent.click(order);
  expect(footer()).toHaveTextContent("Rows 1–25 of 63 · 13 matches");
  expect(footer()).toHaveTextContent("Filters/sort: this page");
  expect(
    within(table())
      .getAllByRole("row")
      .slice(1)
      .map((row) =>
        within(row).getByRole("checkbox").getAttribute("aria-label"),
      ),
  ).toEqual(
    [24, 22, 20, 18, 16, 14, 12, 10, 8, 6, 4, 2, 0].map(
      (id) => `Select datapoint ${id}`,
    ),
  );
  // These view controls operate on the loaded page and do not query a global filter.
  expect(
    f.get.mock.calls.filter(([path]) => path.startsWith("/api/datasets/")),
  ).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() =>
    expect(footer()).toHaveTextContent("Rows 26–50 of 63 · 12 matches"),
  );
  expect(
    within(table()).getByRole("columnheader", { name: "#" }),
  ).toHaveAttribute("aria-sort", "descending");
  expect(
    screen.getByRole("button", { name: "Filters (1)" }),
  ).toBeInTheDocument();
  expect(
    within(table())
      .getAllByRole("row")
      .slice(1)
      .map((row) =>
        within(row).getByRole("checkbox").getAttribute("aria-label"),
      ),
  ).toEqual(
    [48, 46, 44, 42, 40, 38, 36, 34, 32, 30, 28, 26].map(
      (id) => `Select datapoint ${id}`,
    ),
  );
  expect(
    f.get.mock.calls.filter(([path]) => path.startsWith("/api/datasets/")),
  ).toEqual([
    ["/api/datasets/demo?offset=0&limit=25", expect.any(AbortSignal)],
    ["/api/datasets/demo?offset=25&limit=25", expect.any(AbortSignal)],
  ]);
  fireEvent.click(screen.getByRole("button", { name: "Previous" }));
  await waitFor(() =>
    expect(footer()).toHaveTextContent("Rows 1–25 of 63 · 13 matches"),
  );
  expect(within(table()).getAllByRole("row")[1]).toHaveTextContent(
    "Question 24",
  );
});
