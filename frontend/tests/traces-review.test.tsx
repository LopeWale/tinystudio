import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { StudioContext } from "../src/app/StudioContext";
import { EvidenceRegistry } from "../src/app/evidence";
import { TracesPage } from "../src/features/workspace/traces";
afterEach(cleanup);
function setup(count?: number) {
  let rows = [
    {
      id: "trace/one",
      model: "model-a",
      messages: [
        { role: "system", content: "Preserve units" },
        { role: "user", content: "Shared first question" },
      ],
      response: "First response",
      score: null,
    },
    {
      id: "trace-two",
      model: "model-b",
      messages: [{ role: "user", content: "Other question" }],
      response: "Second response",
      score: null,
    },
    {
      id: "trace-three",
      model: "model-c",
      messages: [{ role: "user", content: "Shared last question" }],
      response: "Third response",
      score: null,
    },
  ];
  if (count !== undefined)
    rows = Array.from({ length: count }, (_, index) => ({
      id: `trace-${index}`,
      model: `model-${index}`,
      messages: [{ role: "user", content: `Question ${index}` }],
      response: `Response ${index}`,
      score: null,
    }));
  const api = {
    get: vi.fn(async (path: string) => {
      if (!path.startsWith("/api/traces")) return [{ name: "training" }];
      const params = new URL(path, "http://fixture").searchParams;
      const offset = Number(params.get("offset")),
        limit = Number(params.get("limit"));
      return rows.slice(offset, offset + limit);
    }),
    post: vi.fn(async () => ({})),
    del: vi.fn(async (path: string) => {
      rows = rows.filter(
        (trace) => `/api/traces/${encodeURIComponent(trace.id)}` !== path,
      );
      return {};
    }),
  };
  const studio = {
    api,
    project: "fixture",
    user: { id: "u", role: "admin" },
    scopeKey: "u:fixture",
    route: "/traces",
    evidence: new EvidenceRegistry(),
    confirm: vi.fn(async () => true),
    toast: vi.fn(),
    refreshOverview: vi.fn(async () => {}),
  } as any;
  const view = render(
    <StudioContext.Provider value={studio}>
      <MemoryRouter>
        <TracesPage />
      </MemoryRouter>
    </StudioContext.Provider>,
  );
  return {
    api,
    studio,
    changeScope(scopeKey: string) {
      view.rerender(
        <StudioContext.Provider value={{ ...studio, scopeKey }}>
          <MemoryRouter>
            <TracesPage />
          </MemoryRouter>
        </StudioContext.Provider>,
      );
    },
  };
}
async function inspectFirst() {
  const triggers = await screen.findAllByRole("button", {
    name: "Inspect trace",
  });
  fireEvent.click(triggers[0]);
  return {
    inspector: screen.getByRole("complementary", { name: "Trace inspector" }),
    trigger: triggers[0],
  };
}
it("navigates only the searched window and preserves selected identity through filtering", async () => {
  setup();
  await screen.findByText("Shared first question");
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value: "Shared" },
  });
  const { inspector, trigger } = await inspectFirst();
  expect(within(inspector).getByText("Preserve units")).toBeVisible();
  expect(
    within(inspector).getByRole("button", { name: "Previous trace" }),
  ).toBeDisabled();
  expect(within(inspector).getByText("1 of 2")).toBeVisible();
  fireEvent.click(
    within(inspector).getByRole("button", { name: "Next trace" }),
  );
  expect(within(inspector).getByText("Third response")).toBeVisible();
  expect(
    within(inspector).getByRole("button", { name: "Next trace" }),
  ).toBeDisabled();
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value: "first question" },
  });
  expect(within(inspector).getByText("Third response")).toBeVisible();
  expect(within(inspector).getByText("Outside current search")).toBeVisible();
  expect(
    within(inspector).getByRole("button", { name: "Previous trace" }),
  ).toBeDisabled();
  expect(
    within(inspector).getByRole("button", { name: "Next trace" }),
  ).toBeDisabled();
  fireEvent.click(
    within(inspector).getByRole("button", { name: "Close inspector" }),
  );
  expect(trigger).toHaveFocus();
});
it("preserves scoring, corrections and confirmed deletion in the selected review footer", async () => {
  const { api, studio } = setup();
  await screen.findByText("Shared first question");
  expect(
    screen.queryByRole("button", { name: "Delete" }),
  ).not.toBeInTheDocument();
  const { inspector } = await inspectFirst();
  fireEvent.click(
    within(inspector).getByRole("button", {
      name: "Mark response incorrect: trace/one",
    }),
  );
  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith("/api/traces/trace%2Fone/score", {
      score: 0,
    }),
  );
  await waitFor(() =>
    expect(
      within(inspector).getByRole("button", { name: "Correct response" }),
    ).toBeEnabled(),
  );
  fireEvent.click(
    within(inspector).getByRole("button", {
      name: "Mark response correct: trace/one",
    }),
  );
  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith("/api/traces/trace%2Fone/score", {
      score: 1,
    }),
  );
  await waitFor(() =>
    expect(
      within(inspector).getByRole("button", { name: "Correct response" }),
    ).toBeEnabled(),
  );
  fireEvent.click(
    within(inspector).getByRole("button", { name: "Correct response" }),
  );
  fireEvent.change(screen.getByLabelText("Corrected answer"), {
    target: { value: "Checked response" },
  });
  fireEvent.change(screen.getByLabelText("Feedback (optional)"), {
    target: { value: "Units verified" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save as correct" }));
  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith(
      "/api/traces/trace%2Fone/score",
      { score: 1, corrected: "Checked response", note: "Units verified" },
      expect.any(AbortSignal),
    ),
  );
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  studio.confirm.mockResolvedValueOnce(false);
  fireEvent.click(within(inspector).getByRole("button", { name: "Delete" }));
  await waitFor(() =>
    expect(studio.confirm).toHaveBeenCalledWith(
      "Delete this inference trace?",
      "Delete trace",
    ),
  );
  await waitFor(() =>
    expect(
      within(inspector).getByRole("button", { name: "Delete" }),
    ).toBeEnabled(),
  );
  expect(api.del).not.toHaveBeenCalled();
  fireEvent.click(within(inspector).getByRole("button", { name: "Delete" }));
  await waitFor(() =>
    expect(api.del).toHaveBeenCalledWith("/api/traces/trace%2Fone"),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("complementary", { name: "Trace inspector" }),
    ).not.toBeInTheDocument(),
  );
  await waitFor(() =>
    expect(screen.queryByText("Shared first question")).not.toBeInTheDocument(),
  );
  expect(document.activeElement).toHaveClass("trace-table-host");
});
it("restores collection focus when the original trigger is disconnected", async () => {
  setup();
  const { inspector, trigger } = await inspectFirst();
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value: "last question" },
  });
  expect(trigger.isConnected).toBe(false);
  fireEvent.click(
    within(inspector).getByRole("button", { name: "Close inspector" }),
  );
  expect(document.activeElement).toHaveClass("trace-table-host");
});

it("blocks review navigation while scoring and recovers after a failed score", async () => {
  const { api } = setup();
  const { inspector } = await inspectFirst();
  let rejectScore!: (error: Error) => void;
  api.post.mockImplementationOnce(
    () =>
      new Promise((_resolve, reject) => {
        rejectScore = reject;
      }),
  );
  fireEvent.click(
    within(inspector).getByRole("button", {
      name: "Mark response correct: trace/one",
    }),
  );
  expect(
    within(inspector).getByRole("button", { name: "Next trace" }),
  ).toBeDisabled();
  expect(
    within(inspector).getByRole("button", { name: "Delete" }),
  ).toBeDisabled();
  expect(
    within(inspector).getByRole("button", { name: "Correct response" }),
  ).toBeDisabled();
  rejectScore(new Error("Scoring unavailable"));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Scoring unavailable",
  );
  await waitFor(() =>
    expect(
      within(inspector).getByRole("button", { name: "Next trace" }),
    ).toBeEnabled(),
  );
  fireEvent.click(
    within(inspector).getByRole("button", { name: "Next trace" }),
  );
  expect(within(inspector).getByText("Second response")).toBeVisible();
});

it("follows table sorting when moving between selected traces", async () => {
  setup();
  const { inspector } = await inspectFirst();
  const sort = screen.getByRole("button", { name: /When · model/ });
  fireEvent.click(sort);
  fireEvent.click(sort);
  expect(within(inspector).getByText("3 of 3")).toBeVisible();
  expect(
    within(inspector).getByRole("button", { name: "Next trace" }),
  ).toBeDisabled();
  fireEvent.click(
    within(inspector).getByRole("button", { name: "Previous trace" }),
  );
  expect(within(inspector).getByText("Second response")).toBeVisible();
});

it("restores focus only after the inspector closes in the committed DOM", async () => {
  setup();
  const { inspector, trigger } = await inspectFirst();
  const focus = trigger.focus.bind(trigger);
  const restore = vi.spyOn(trigger, "focus").mockImplementation(() => {
    expect(trigger.closest(".inspection-workspace")).not.toHaveClass(
      "has-inspector",
    );
    expect(inspector.isConnected).toBe(false);
    focus();
  });
  fireEvent.click(
    within(inspector).getByRole("button", { name: "Close inspector" }),
  );
  expect(restore).toHaveBeenCalledTimes(1);
  expect(trigger).toHaveFocus();
});
it("starts each selected trace detail at the top", async () => {
  setup();
  const { inspector } = await inspectFirst();
  const firstDetail = within(inspector).getByRole("region", {
    name: "Trace detail",
  });
  firstDetail.scrollTop = 240;
  fireEvent.scroll(firstDetail);
  fireEvent.click(
    within(inspector).getByRole("button", { name: "Next trace" }),
  );
  const nextDetail = within(inspector).getByRole("region", {
    name: "Trace detail",
  });
  expect(nextDetail).not.toBe(firstDetail);
  expect(nextDetail.scrollTop).toBe(0);
});

it("uses actual server offsets and excludes the next-page sentinel from review", async () => {
  const { api } = setup(21);
  await screen.findByText("Question 0");
  fireEvent.change(
    screen.getByLabelText("Recent inference traces rows per page"),
    { target: { value: "10" } },
  );
  await waitFor(() =>
    expect(api.get).toHaveBeenCalledWith(
      "/api/traces?limit=11&offset=0",
      expect.any(AbortSignal),
    ),
  );
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent("Rows 1–10"),
  );
  expect(screen.queryByText("Question 10")).not.toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Inspect trace" })).toHaveLength(
    10,
  );
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("Question 10");
  expect(api.get).toHaveBeenCalledWith(
    "/api/traces?limit=11&offset=10",
    expect.any(AbortSignal),
  );
  expect(screen.getByRole("status")).toHaveTextContent("Rows 11–20");
  expect(screen.queryByText("Question 20")).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value: "no matches" },
  });
  expect(screen.getByRole("status")).toHaveTextContent(
    "Rows 11–20 · this page · 0 matches",
  );
  expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("Question 20");
  expect(api.get).toHaveBeenCalledWith(
    "/api/traces?limit=11&offset=20",
    expect.any(AbortSignal),
  );
  expect(screen.getByRole("status")).toHaveTextContent("Rows 21–21");
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  expect(screen.queryByText(/Page \d+ of/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Previous" }));
  await screen.findByText("Question 10");
  expect(
    screen.getByLabelText("Recent inference traces rows per page"),
  ).toHaveValue("10");
});

it("disables Next for an exact multiple and recovers after deleting a sole final record", async () => {
  const { api } = setup(11);
  await screen.findByText("Question 0");
  fireEvent.change(
    screen.getByLabelText("Recent inference traces rows per page"),
    { target: { value: "10" } },
  );
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent("Rows 1–10"),
  );
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("Question 10");
  const { inspector } = await inspectFirst();
  fireEvent.click(within(inspector).getByRole("button", { name: "Delete" }));
  await waitFor(() =>
    expect(api.del).toHaveBeenCalledWith("/api/traces/trace-10"),
  );
  await screen.findByText("Question 0");
  expect(screen.getByRole("status")).toHaveTextContent("Rows 1–10");
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
});

it("retains explicit inspected context while changing pages and reports refresh errors honestly", async () => {
  const { api } = setup(11);
  await screen.findByText("Question 0");
  fireEvent.change(
    screen.getByLabelText("Recent inference traces rows per page"),
    { target: { value: "10" } },
  );
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent("Rows 1–10"),
  );
  const { inspector } = await inspectFirst();
  let resolvePage!: (value: any) => void;
  api.get.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolvePage = resolve;
      }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(inspector).toBeVisible();
  expect(within(inspector).getByText("Response 0")).toBeVisible();
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent("Loading page");
  resolvePage([
    {
      id: "trace-10",
      model: "model-10",
      messages: [{ role: "user", content: "Question 10" }],
      response: "Response 10",
      score: null,
    },
  ]);
  await screen.findByText("Question 10");
  expect(within(inspector).getByText("Outside current page")).toBeVisible();
  fireEvent.click(
    within(inspector).getByRole("button", {
      name: "Mark response correct: trace-0",
    }),
  );
  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith("/api/traces/trace-0/score", {
      score: 1,
    }),
  );
  await waitFor(() =>
    expect(within(inspector).getByText("100.0%")).toBeVisible(),
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled(),
  );
  api.get.mockRejectedValueOnce(new Error("Trace service unavailable"));
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await screen.findByRole("alert");
  expect(
    screen
      .getAllByRole("status")
      .some((status) => status.textContent?.includes("Page unavailable")),
  ).toBe(true);
  expect(screen.queryByText("Rows 0–0")).not.toBeInTheDocument();
  expect(inspector).toBeVisible();
});

it("ignores obsolete page responses and resets page size/offset synchronously for a new scope", async () => {
  const { api, changeScope } = setup(30);
  await screen.findByText("Question 0");
  fireEvent.change(
    screen.getByLabelText("Recent inference traces rows per page"),
    { target: { value: "10" } },
  );
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent("Rows 1–10"),
  );
  let resolveOld!: (value: any) => void;
  api.get.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveOld = resolve;
      }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  fireEvent.change(
    screen.getByLabelText("Recent inference traces rows per page"),
    { target: { value: "25" } },
  );
  await screen.findByText("Question 0");
  resolveOld([
    {
      id: "obsolete",
      model: "old",
      messages: [],
      response: "Obsolete page response",
    },
  ]);
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent("Rows 1–25"),
  );
  expect(screen.queryByText("Obsolete page response")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("Question 25");
  await inspectFirst();
  let resolveNew!: (value: any) => void;
  api.get.mockImplementation((path: string) =>
    path.startsWith("/api/traces")
      ? new Promise((resolve) => {
          resolveNew = resolve;
        })
      : Promise.resolve([{ name: "training" }]),
  );
  changeScope("u:other-project");
  expect(
    screen.queryByRole("complementary", { name: "Trace inspector" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("Question 25")).not.toBeInTheDocument();
  expect(api.get).toHaveBeenLastCalledWith(
    "/api/datasets",
    expect.any(AbortSignal),
  );
  expect(api.get).toHaveBeenCalledWith(
    "/api/traces?limit=26&offset=0",
    expect.any(AbortSignal),
  );
  expect(
    api.get.mock.calls
      .slice(-2)
      .some(([path]) => path === "/api/traces?limit=26&offset=25"),
  ).toBe(false);
  resolveNew([]);
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent("Rows 0–0"),
  );
});

it("fits five rows into the viewport and requests the eleven-record oracle at offsets 0, 5 and 10", async () => {
  const height = vi
    .spyOn(HTMLElement.prototype, "clientHeight", "get")
    .mockImplementation(function (this: HTMLElement) {
      return this.classList.contains("data-table__scroll") ? 261 : 0;
    });
  const width = vi
    .spyOn(HTMLElement.prototype, "clientWidth", "get")
    .mockImplementation(function (this: HTMLElement) {
      return this.classList.contains("data-table__scroll") ? 900 : 0;
    });
  const rect = vi
    .spyOn(HTMLElement.prototype, "getBoundingClientRect")
    .mockImplementation(function (this: HTMLElement) {
      const block =
        this.tagName === "THEAD" ? 40 : this.tagName === "TR" ? 44 : 0;
      return {
        x: 0,
        y: 0,
        left: 0,
        right: 900,
        top: 0,
        bottom: block,
        width: 900,
        height: block,
        toJSON() {},
      };
    });
  try {
    const { api } = setup(11);
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Rows 1–5"),
    );
    expect(api.get).toHaveBeenCalledWith(
      "/api/traces?limit=6&offset=0",
      expect.any(AbortSignal),
    );
    expect(
      screen.getAllByRole("button", { name: "Inspect trace" }),
    ).toHaveLength(5);
    expect(screen.queryByText("Question 5")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Rows 6–10"),
    );
    expect(api.get).toHaveBeenCalledWith(
      "/api/traces?limit=6&offset=5",
      expect.any(AbortSignal),
    );
    expect(screen.queryByText("Question 10")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Rows 11–11"),
    );
    expect(api.get).toHaveBeenCalledWith(
      "/api/traces?limit=6&offset=10",
      expect.any(AbortSignal),
    );
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(
      screen.getByLabelText("Recent inference traces rows per page"),
    ).toHaveValue("fit");
  } finally {
    cleanup();
    height.mockRestore();
    width.mockRestore();
    rect.mockRestore();
  }
});

it("keeps inspector geometry stable when Fit changes a late server page from seven to four rows", async () => {
  const height = vi
    .spyOn(HTMLElement.prototype, "clientHeight", "get")
    .mockImplementation(function (this: HTMLElement) {
      if (!this.classList.contains("data-table__scroll")) return 0;
      if (!this.closest(".has-inspector")) return 760;
      return this.closest(".data-table")
        ?.querySelector('[role="status"]')
        ?.textContent?.includes("Loading")
        ? 560
        : 522;
    });
  const width = vi
    .spyOn(HTMLElement.prototype, "clientWidth", "get")
    .mockImplementation(function (this: HTMLElement) {
      return this.classList.contains("data-table__scroll")
        ? this.closest(".has-inspector")
          ? 533
          : 1000
        : 0;
    });
  const rect = vi
    .spyOn(HTMLElement.prototype, "getBoundingClientRect")
    .mockImplementation(function (this: HTMLElement) {
      const block =
        this.tagName === "THEAD"
          ? 50.5
          : this.tagName === "TR"
            ? this.closest(".has-inspector")
              ? 101
              : 100
            : 0;
      return {
        x: 0,
        y: 0,
        left: 0,
        right: 1000,
        top: 0,
        bottom: block,
        width: 1000,
        height: block,
        toJSON() {},
      };
    });
  try {
    const { api } = setup(63);
    await waitFor(() =>
      expect(
        document.querySelector('.data-table__footer [role="status"]'),
      ).toHaveTextContent("Rows 1–7"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(
        document.querySelector('.data-table__footer [role="status"]'),
      ).toHaveTextContent("Rows 8–14"),
    );
    fireEvent.change(
      screen.getByLabelText("Recent inference traces rows per page"),
      { target: { value: "10" } },
    );
    await waitFor(() =>
      expect(
        document.querySelector('.data-table__footer [role="status"]'),
      ).toHaveTextContent("Rows 1–10"),
    );
    for (let i = 0; i < 6; i++) {
      fireEvent.click(screen.getByRole("button", { name: "Next" }));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled(),
      );
    }
    fireEvent.change(screen.getByRole("searchbox"), {
      target: { value: "no match" },
    });
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
    fireEvent.change(
      screen.getByLabelText("Recent inference traces rows per page"),
      { target: { value: "fit" } },
    );
    await waitFor(() =>
      expect(
        document.querySelector('.data-table__footer [role="status"]'),
      ).toHaveTextContent("Rows 57–63"),
    );
    const { inspector } = await inspectFirst();
    await waitFor(() =>
      expect(
        document.querySelector('.data-table__footer [role="status"]'),
      ).toHaveTextContent("Rows 57–60"),
    );
    expect(api.get).toHaveBeenCalledWith(
      "/api/traces?limit=5&offset=56",
      expect.any(AbortSignal),
    );
    expect(inspector).toBeVisible();
    expect(within(inspector).getByText("Response 56")).toBeVisible();
    fireEvent.change(
      screen.getByLabelText("Recent inference traces rows per page"),
      { target: { value: "10" } },
    );
    await waitFor(() =>
      expect(
        document.querySelector('.data-table__footer [role="status"]'),
      ).toHaveTextContent("Rows 51–60"),
    );
    expect(inspector).toBeVisible();
  } finally {
    cleanup();
    height.mockRestore();
    width.mockRestore();
    rect.mockRestore();
  }
});
