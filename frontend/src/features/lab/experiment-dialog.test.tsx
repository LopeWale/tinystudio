import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { LabPage } from "./index";
import { makeDefinition } from "./definition";
afterEach(cleanup);
function mount() {
  const definition = makeDefinition();
  const row = { id: "saved", name: definition.name, revision: 1, definition };
  const api = {
    get: vi.fn(async (path: string) =>
      path.includes("/events?")
        ? { events: [], next_after: 0 }
        : path.includes("/records?")
          ? { records: [] }
          : path === "/api/lab/runs/run"
            ? { id: "run", state: "succeeded" }
            : path.endsWith("catalog")
              ? { adapters: [], methods: [] }
              : path.endsWith("targets")
                ? [{ id: "local", name: "Local" }]
                : [],
    ),
    post: vi.fn(async () => row),
    put: vi.fn(),
  };
  const studio = {
    api,
    scopeKey: "alpha",
    project: "alpha",
    user: { id: "user" },
    route: "/lab",
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
  } as any;
  const view = render(
    <StudioContext.Provider value={studio}>
      <LabPage />
    </StudioContext.Provider>,
  );
  return { api, row, view, studio };
}
const open = () =>
  fireEvent.click(
    screen.getByRole("button", {
      name: /^(Open experiment|Resume draft|Edit experiment)$/,
    }),
  );
it("defaults to Runs with an explicit experiment dialog and retains invalid JSON through Escape", async () => {
  mount();
  expect(screen.getByRole("tab", { name: "Runs" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.queryByRole("tab", { name: "Experiment" })).toBeNull();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Experiment name" })).toBeNull();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "New experiment" }));
  fireEvent.click(screen.getByRole("tab", { name: "Definition JSON" }));
  const raw = screen.getByRole("textbox", {
    name: "Experiment definition JSON",
  });
  fireEvent.change(raw, { target: { value: "{ invalid draft" } });
  expect(
    screen.getByRole("button", { name: "Save experiment" }),
  ).toBeDisabled();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(
    screen.queryByRole("textbox", { name: "Experiment definition JSON" }),
  ).toBeNull();
  expect(screen.getByRole("button", { name: "New experiment" })).toHaveFocus();
  open();
  expect(
    screen.getByRole("textbox", { name: "Experiment definition JSON" }),
  ).toBe(raw);
  expect(raw).toHaveValue("{ invalid draft");
  fireEvent.click(screen.getByRole("button", { name: "New experiment" }));
  expect(screen.getByRole("textbox", { name: "Experiment name" })).toHaveValue(
    "Synthetic signal",
  );
  expect(screen.getByRole("button", { name: "Save experiment" })).toBeEnabled();
});
it("preserves selected File identity after close and resume", () => {
  mount();
  open();
  fireEvent.click(screen.getByRole("tab", { name: "Inputs" }));
  const input = screen.getByLabelText("Import laboratory input", {
    selector: "input",
  }) as HTMLInputElement;
  const file = new File(["data"], "input.jsonl");
  fireEvent.change(input, { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
  open();
  expect(
    screen.getByLabelText("Import laboratory input", { selector: "input" }),
  ).toBe(input);
  expect(input.files?.[0]).toBe(file);
});
it.each(["save", "preflight", "submit"])(
  "does not reopen a dismissed dialog after pending %s completes",
  async (kind) => {
    const { api, row } = mount();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "New experiment" }));
    if (kind === "submit") {
      fireEvent.click(screen.getByRole("button", { name: "Save experiment" }));
      await screen.findByRole("button", { name: "Save revision" });
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "Run saved revision" }),
        ).toBeEnabled(),
      );
    }
    let finish!: (value: any) => void;
    api.post.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    await user.click(
      screen.getByRole("button", {
        name:
          kind === "save"
            ? "Save experiment"
            : kind === "preflight"
              ? "Check compatibility"
              : "Run saved revision",
      }),
    );
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("heading", { name: "Laboratory" })).toHaveFocus();
    await act(async () =>
      finish(
        kind === "save"
          ? row
          : kind === "submit"
            ? { id: "run", state: "queued" }
            : { compatible: true },
      ),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("heading", { name: "Laboratory" })).toHaveFocus();
    expect(screen.getByRole("tab", { name: "Runs" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    open();
    expect(screen.getByRole("dialog")).toBeVisible();
  },
);
it("shows a failed save inside the dialog and preserves the draft", async () => {
  const { api } = mount();
  open();
  fireEvent.change(screen.getByLabelText("Experiment name"), {
    target: { value: "Keep me" },
  });
  api.post.mockRejectedValueOnce(new Error("Save unavailable"));
  fireEvent.click(screen.getByRole("button", { name: "Save experiment" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Save unavailable",
  );
  expect(screen.getByLabelText("Experiment name")).toHaveValue("Keep me");
});
