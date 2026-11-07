import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
async function setup() {
  let controller!: PlaygroundController;
  const fetcher = vi.fn<typeof fetch>(() => new Promise<Response>(() => {}));
  const runtime = {
    api: {
      get: vi.fn((path: string) =>
        Promise.resolve(
          path === "/api/models"
            ? [
                { id: "model-a", alias: "Model A", status: "up" },
                { id: "model-b", alias: "Model B", status: "down" },
              ]
            : [
                { name: "Handbook", chunk_count: 12 },
                { name: "Notes", chunk_count: 4 },
              ],
        ),
      ),
    },
    project: "fixture",
    user: { id: "u" },
    scopeKey: "u:fixture",
    route: "/playground",
    evidence: new EvidenceRegistry(),
    onSessionExpired: vi.fn(),
  } as any;
  function Probe() {
    controller = usePlaygroundController();
    return <PlaygroundPage />;
  }
  render(
    <StudioContext.Provider value={runtime}>
      <MemoryRouter>
        <PlaygroundProvider fetcher={fetcher}>
          <Probe />
        </PlaygroundProvider>
      </MemoryRouter>
    </StudioContext.Provider>,
  );
  await screen.findByRole("button", { name: /Model A/ });
  return { controller, fetcher, user: userEvent.setup() };
}

describe("Playground composer composition", () => {
  it("keeps the draft while settings are opened and closed and selects repositories without modifier keys", async () => {
    const { user, controller } = await setup();
    const draft = screen.getByLabelText("Message your model");
    await user.type(draft, "Use both references");
    await user.click(screen.getByRole("button", { name: "Settings" }));
    await user.click(screen.getByRole("checkbox", { name: /Handbook/ }));
    await user.click(screen.getByRole("checkbox", { name: /Notes/ }));
    expect(controller.getSnapshot().repositories).toEqual([
      "Handbook",
      "Notes",
    ]);
    await user.click(screen.getByRole("checkbox", { name: /Handbook/ }));
    expect(controller.getSnapshot().repositories).toEqual(["Notes"]);
    await user.click(
      screen.getByRole("button", { name: "Close configuration" }),
    );
    expect(draft).toHaveValue("Use both references");
    expect(screen.getByRole("button", { name: /^Settings$/ })).toHaveFocus();
    const context = screen.getByLabelText("Active Playground context");
    expect(within(context).getByText("Notes")).toBeInTheDocument();
    expect(within(context).queryByText("Handbook")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("textbox", { name: "System prompt" }),
    ).not.toBeInTheDocument();
  });

  it("changes the controller-owned model through the composer picker and preserves its explicit reset semantics", async () => {
    const { user, controller } = await setup();
    await user.type(
      screen.getByLabelText("Message your model"),
      "Draft to reset",
    );
    await user.click(screen.getByRole("button", { name: /Model A/ }));
    await user.click(await screen.findByRole("option", { name: "Model B" }));
    expect(controller.getSnapshot().model).toBe("model-b");
    expect(screen.getByLabelText("Message your model")).toHaveValue("");
    expect(screen.getByRole("button", { name: /Model B/ })).toBeInTheDocument();
  });

  it("submits Enter, preserves Shift+Enter and IME input, locks configuration while busy, and cancels", async () => {
    const { user, controller, fetcher } = await setup();
    const draft = screen.getByLabelText("Message your model");
    await user.type(draft, "Question");
    fireEvent.keyDown(draft, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(draft, { key: "Enter", isComposing: true });
    expect(fetcher).not.toHaveBeenCalled();
    fireEvent.keyDown(draft, { key: "Enter" });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(
      JSON.parse(fetcher.mock.calls[0][1]!.body as string).messages,
    ).toEqual([{ role: "user", content: "Question" }]);
    expect(controller.getSnapshot().busy).toBe(true);
    expect(screen.getByRole("button", { name: /Model A/ })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Settings" }));
    expect(screen.getByRole("checkbox", { name: /Handbook/ })).toBeDisabled();
    expect(screen.getByLabelText("System prompt")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Cancel response" }));
    expect(controller.getSnapshot().busy).toBe(false);
    expect(controller.getSnapshot().history).toEqual([]);
    expect(screen.getByRole("button", { name: /Model A/ })).toBeEnabled();
  });

  it("keeps retrieved passages attached to the rendered model response", async () => {
    const { user, fetcher } = await setup();
    fetcher.mockResolvedValueOnce(
      new Response(
        new ReadableStream({
          start(stream) {
            stream.enqueue(
              new TextEncoder().encode(
                'data: {"delta":"Answer","document_chunks":[{"document":"Guide","similarity_score":0.75,"content":"Retained source passage"}],"done":true}\n\n',
              ),
            );
            stream.close();
          },
        }),
      ),
    );
    await user.type(screen.getByLabelText("Message your model"), "Question");
    await user.click(screen.getByRole("button", { name: "Send" }));
    const disclosure = await screen.findByText("Retrieved passages (1)");
    await user.click(disclosure);
    expect(screen.getByText("Retained source passage")).toBeInTheDocument();
    expect(screen.getByText("Guide")).toBeInTheDocument();
  });
});
