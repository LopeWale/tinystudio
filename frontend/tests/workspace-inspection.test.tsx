import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { StudioContext } from "../src/app/StudioContext";
import { EvidenceRegistry } from "../src/app/evidence";
import { TracesPage } from "../src/features/workspace/traces";
import { WorkspaceDisclosure } from "../src/components/studio/workspace";
import { Button } from "../src/components/studio/primitives";
afterEach(cleanup);
it("opens the selected trace with complete source messages while keeping review actions and the table", async () => {
  const trace = {
    id: "trace-1",
    model: "model-a",
    messages: [
      { role: "system", content: "Keep units" },
      { role: "user", content: "Question" },
    ],
    response: "Original answer",
    corrected: "Reviewed answer",
    score: 1,
    note: "Checked units",
  };
  const runtime = {
    api: {
      get: vi.fn(async (path: string) =>
        path.startsWith("/api/traces") ? [trace] : [],
      ),
    },
    project: "fixture",
    user: { id: "u" },
    scopeKey: "u:fixture",
    route: "/traces",
    evidence: new EvidenceRegistry(),
  } as any;
  render(
    <StudioContext.Provider value={runtime}>
      <MemoryRouter>
        <TracesPage />
      </MemoryRouter>
    </StudioContext.Provider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Inspect trace" }));
  const inspector = screen.getByRole("complementary", {
    name: "Trace inspector",
  });
  expect(within(inspector).getByText("Keep units")).toBeVisible();
  expect(within(inspector).getByText("Original answer")).toBeVisible();
  expect(within(inspector).getByText("Reviewed answer")).toBeVisible();
  expect(
    screen.getByRole("table", { name: "Recent inference traces" }),
  ).toBeVisible();
  fireEvent.click(
    within(inspector).getByRole("button", { name: "Correct response" }),
  );
  expect(
    await screen.findByRole("dialog", { name: "Correct this response" }),
  ).toBeVisible();
  expect(screen.getByLabelText("Corrected answer")).toHaveValue(
    "Reviewed answer",
  );
});
it("secondary tools start collapsed, retain their input, and do not submit their surrounding form", () => {
  const submit = vi.fn();
  render(
    <form onSubmit={submit}>
      <WorkspaceDisclosure title="Advanced tools">
        <input aria-label="Draft configuration" defaultValue="Keep me" />
        <Button>Refresh</Button>
      </WorkspaceDisclosure>
    </form>,
  );
  const details = screen.getByText("Advanced tools").closest("details")!;
  expect(details.open).toBe(false);
  details.open = true;
  fireEvent.change(screen.getByLabelText("Draft configuration"), {
    target: { value: "Still here" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  expect(submit).not.toHaveBeenCalled();
  details.open = false;
  details.open = true;
  expect(screen.getByLabelText("Draft configuration")).toHaveValue(
    "Still here",
  );
});
