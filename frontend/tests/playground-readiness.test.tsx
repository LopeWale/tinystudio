import { Profiler } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { StudioContext } from "../src/app/StudioContext";
import { EvidenceRegistry } from "../src/app/evidence";
import {
  PlaygroundProvider,
  usePlaygroundController,
} from "../src/app/PlaygroundProvider";
import { PlaygroundPage } from "../src/features/workspace/playground";
import type { PlaygroundController } from "../src/controllers/playground";
afterEach(cleanup);
function pendingInventory(
  onCommit?: (controller: PlaygroundController) => void,
) {
  let resolve!: (value: any[]) => void, controller!: PlaygroundController;
  const inventory = new Promise<any[]>((r) => (resolve = r));
  const api = {
    get: vi.fn((path: string) =>
      path === "/api/models" ? inventory : Promise.resolve([]),
    ),
  };
  const runtime = {
    api,
    project: "fixture",
    user: { id: "u" },
    scopeKey: "u:fixture",
    route: "/playground",
    evidence: new EvidenceRegistry(),
    onSessionExpired: vi.fn(),
  } as any;
  function Probe() {
    controller = usePlaygroundController();
    return (
      <Profiler id="playground" onRender={() => onCommit?.(controller)}>
        <PlaygroundPage />
      </Profiler>
    );
  }
  const view = render(
    <StudioContext.Provider value={runtime}>
      <MemoryRouter>
        <PlaygroundProvider>
          <Probe />
        </PlaygroundProvider>
      </MemoryRouter>
    </StudioContext.Provider>,
  );
  return { ...view, resolve, controller: () => controller };
}
const rows = [
  { id: "job/1", alias: "Tuned", kind: "fine-tuned", status: "down" },
];
describe("Playground inventory initialization is an owned state transition", () => {
  it("never presents a selected model value that differs from the controller used by Send", async () => {
    const commits: { displayed: string; owned: string }[] = [];
    const view = pendingInventory((controller) => {
      const selector = document.querySelector(
        "select",
      ) as HTMLSelectElement | null;
      if (selector?.value)
        commits.push({
          displayed: selector.value,
          owned: controller.getSnapshot().model,
        });
    });
    await act(async () => view.resolve(rows));
    expect(commits.length).toBeGreaterThan(0);
    expect(commits.every((commit) => commit.displayed === commit.owned)).toBe(
      true,
    );
  });
  it("preserves a prompt typed while the initial model inventory is pending", async () => {
    const view = pendingInventory();
    fireEvent.change(screen.getByLabelText("Message your model"), {
      target: { value: "Prepared while inventory is loading" },
    });
    expect(view.controller().getSnapshot().draft).toBe(
      "Prepared while inventory is loading",
    );
    await act(async () => view.resolve(rows));
    expect(screen.getByLabelText("Model")).toHaveValue("job/1");
    expect(screen.getByLabelText("Message your model")).toHaveValue(
      "Prepared while inventory is loading",
    );
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
  });
});
