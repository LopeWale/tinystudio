import { StrictMode } from "react";
import { MemoryRouter } from "react-router-dom";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  cleanup,
} from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { Evidence } from "../../components/studio/primitives";
import { StudioContext, useResource } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { AssistantProvider, AssistantWidget } from "./index";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
describe("shared React assistant", () => {
  it("keeps the same focused omnipresent composer and draft through remote sync on a research route", async () => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
    let revision = 1;
    const api = {
      get: vi.fn(async (path: string) =>
        path.endsWith("/models")
          ? {
              anthropic: {
                available: true,
                models: [{ id: "claude-opus-5-5" }],
              },
            }
          : path.endsWith("/config")
            ? { max_bytes: 1024, accept: ".txt" }
            : {
                revision: revision++,
                history: [],
                provider: "anthropic",
                model: "claude-opus-5-5",
              },
      ),
    };
    const studio = {
      api,
      route: "/evaluations/review",
      scopeKey: "alice:home",
      project: "home",
      user: { id: "alice" },
      evidence: new EvidenceRegistry(),
      toast: vi.fn(),
      confirm: vi.fn(),
      navigate: vi.fn(),
    } as any;
    const view = render(
      <StrictMode>
        <MemoryRouter initialEntries={["/evaluations/review"]}>
          <StudioContext.Provider value={studio}>
            <AssistantProvider>
              <AssistantWidget />
            </AssistantProvider>
          </StudioContext.Provider>
        </MemoryRouter>
      </StrictMode>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open assistant" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument(),
    );
    const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
    textarea.focus();
    fireEvent.change(textarea, { target: { value: "Keep this draft intact" } });
    textarea.setSelectionRange(5, 9);
    window.dispatchEvent(new Event("focus"));
    await waitFor(() =>
      expect(
        api.get.mock.calls.filter(([path]) => path.endsWith("/conversation"))
          .length,
      ).toBeGreaterThan(1),
    );
    expect(screen.getByLabelText("Message")).toBe(textarea);
    expect(textarea.value).toBe("Keep this draft intact");
    expect(document.activeElement).toBe(textarea);
    expect(textarea.selectionStart).toBe(5);
    expect(screen.queryByRole("button", { name: /^Assistant$/ })).toBeNull();
    view.unmount();
  });
  it.each([false, true])(
    "attaches committed evaluation GET context on a real composer send; picked=%s",
    async (picked) => {
      vi.stubGlobal(
        "ResizeObserver",
        class {
          observe() {}
          disconnect() {}
        },
      );
      vi.stubGlobal("matchMedia", () => ({
        matches: true,
        addEventListener() {},
        removeEventListener() {},
      }));
      const evaluation = {
          name: "review",
          metrics: { accuracy: 0.875 },
          api_key: "never-send",
        },
        selected = { datapoint: 42, score: 0.875 };
      const api = {
        get: vi.fn(async (path: string) =>
          path === "/api/evals/review"
            ? evaluation
            : path.endsWith("/models")
              ? {
                  anthropic: {
                    available: true,
                    models: [{ id: "claude-opus-5-5" }],
                  },
                }
              : path.endsWith("/config")
                ? { max_bytes: 1024, accept: ".txt" }
                : {
                    revision: 1,
                    history: [],
                    provider: "anthropic",
                    model: "claude-opus-5-5",
                  },
        ),
        put: vi.fn(async (_path: any, body: any) => ({ ...body, revision: 2 })),
      };
      const fetcher = vi.fn(
        async (_url: any, _options: any) =>
          new Response('data: {"type":"conversation_saved","revision":3}\n\n'),
      );
      vi.stubGlobal("fetch", fetcher);
      const evidence = new EvidenceRegistry();
      evidence.register({
        id: "stale",
        label: "GET /api/evals/private",
        route: "/evaluations/review",
        project: "other",
        user: "bob",
        data: { name: "other-scope-private" },
      });
      const studio = {
        api,
        route: "/evaluations/review",
        project: "home",
        scopeKey: "alice:home",
        user: { id: "alice" },
        evidence,
        toast: vi.fn(),
        confirm: vi.fn(),
        navigate: vi.fn(),
      } as any;
      function Evaluation() {
        const resource = useResource("/api/evals/review");
        return (
          <main>
            <h1>Evaluation review</h1>
            {resource.data && <p>Accuracy 87.5%</p>}
            <Evidence label="Evaluation row" data={selected}>
              <button type="button">Evaluation result 42</button>
            </Evidence>
            <input
              type="password"
              aria-label="Private input"
              value="dom-password-must-not-appear"
              readOnly
            />
          </main>
        );
      }
      render(
        <StrictMode>
          <MemoryRouter initialEntries={["/evaluations/review"]}>
            <StudioContext.Provider value={studio}>
              <AssistantProvider>
                <Evaluation />
                <AssistantWidget />
              </AssistantProvider>
            </StudioContext.Provider>
          </MemoryRouter>
        </StrictMode>,
      );
      await screen.findByText("Accuracy 87.5%");
      await waitFor(() =>
        expect(
          evidence
            .snapshot()
            .some((record) => record.label === "GET /api/evals/review"),
        ).toBe(true),
      );
      fireEvent.click(screen.getByRole("button", { name: "Open assistant" }));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Send" })).toBeDisabled(),
      );
      if (picked) {
        fireEvent.click(screen.getByRole("button", { name: "Pick page data" }));
        fireEvent.click(
          screen.getByRole("button", { name: "Evaluation result 42" }),
        );
      }
      fireEvent.change(screen.getByLabelText("Message"), {
        target: { value: "Review this open evaluation" },
      });
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Send" })).toBeEnabled(),
      );
      fireEvent.click(screen.getByRole("button", { name: "Send" }));
      await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
      const body = JSON.parse(fetcher.mock.calls[0][1]!.body as string);
      expect(body.context).toMatchObject({
        route: "/evaluations/review",
        project: "home",
        user: "alice",
        api: [
          {
            path: "/api/evals/review",
            data: {
              name: "review",
              metrics: { accuracy: 0.875 },
              api_key: "[redacted]",
            },
          },
        ],
      });
      expect(JSON.stringify(body.context)).not.toMatch(
        /never-send|other-scope-private|dom-password-must-not-appear/,
      );
      if (picked)
        expect(body.context.selected_data).toMatchObject({
          label: "Evaluation row",
          project: "home",
          user: "alice",
          route: "/evaluations/review",
          data: selected,
        });
      else expect(body.context.selected_data).toBeUndefined();
    },
  );
});
