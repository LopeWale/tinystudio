import { StrictMode, useState } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import {
  StudioContext,
  useResource,
  type StudioRuntime,
} from "../src/app/StudioContext";
import { EvidenceRegistry } from "../src/app/evidence";
import { Evidence, Modal } from "../src/components/studio/primitives";
afterEach(cleanup);
const runtime = (extra: Partial<StudioRuntime> = {}) =>
  ({
    api: { get: vi.fn() },
    project: "alpha",
    user: { id: 1 },
    scopeKey: "1:alpha",
    route: "/datasets",
    evidence: new EvidenceRegistry(),
    ...extra,
  }) as unknown as StudioRuntime;
describe("React lifecycle acceptance", () => {
  it("rejects delayed earlier resource on project change and aborts StrictMode cleanup", async () => {
    const requests: { resolve: (v: unknown) => void; signal: AbortSignal }[] =
      [];
    const api = {
      get: vi.fn(
        (_path: string, signal: AbortSignal) =>
          new Promise((resolve) => requests.push({ resolve, signal })),
      ),
    } as any;
    function Result() {
      const { data, loading } = useResource<{ name: string }>("/api/datasets");
      return <p>{loading ? "Loading" : data?.name}</p>;
    }
    const context = runtime({ api });
    const view = render(
      <StrictMode>
        <StudioContext.Provider value={context}>
          <Result />
        </StudioContext.Provider>
      </StrictMode>,
    );
    expect(requests).toHaveLength(2);
    expect(requests[0].signal.aborted).toBe(true);
    view.rerender(
      <StrictMode>
        <StudioContext.Provider
          value={{ ...context, project: "beta", scopeKey: "1:beta" }}
        >
          <Result />
        </StudioContext.Provider>
      </StrictMode>,
    );
    expect(requests[1].signal.aborted).toBe(true);
    await act(async () => requests[2].resolve({ name: "Beta current" }));
    expect(screen.getByText("Beta current")).toBeInTheDocument();
    await act(async () => requests[1].resolve({ name: "Alpha stale" }));
    expect(screen.queryByText("Alpha stale")).not.toBeInTheDocument();
    view.unmount();
    expect(requests[2].signal.aborted).toBe(true);
  });
  it("focuses dialog, traps tab, closes with Escape and returns focus in StrictMode", async () => {
    function Demo() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button onClick={() => setOpen(true)}>Open</button>
          <Modal
            open={open}
            title="Delete dataset"
            onClose={() => setOpen(false)}
          >
            <button>First action</button>
            <button>Last action</button>
          </Modal>
        </>
      );
    }
    render(
      <StrictMode>
        <Demo />
      </StrictMode>,
    );
    const opener = screen.getByText("Open");
    opener.focus();
    fireEvent.click(opener);
    expect(screen.getByRole("button", { name: "Close dialog" })).toHaveFocus();
    screen.getByText("Last action").focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(screen.getByRole("button", { name: "Close dialog" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(opener).toHaveFocus();
    expect(document.body.style.overflow).toBe("");
  });
  it("registers only committed sanitized evidence and removes it on unmount", () => {
    const context = runtime();
    const view = render(
      <StrictMode>
        <StudioContext.Provider value={context}>
          <Evidence
            label="Result"
            data={{
              accuracy: 0.75,
              password: "never export",
              api_key: "hidden",
            }}
          >
            <span>Result row</span>
          </Evidence>
        </StudioContext.Provider>
      </StrictMode>,
    );
    expect(context.evidence.snapshot()).toHaveLength(1);
    expect(context.evidence.pick(screen.getByText("Result row"))?.data).toEqual(
      { accuracy: 0.75, password: "[redacted]", api_key: "[redacted]" },
    );
    view.unmount();
    expect(context.evidence.snapshot()).toEqual([]);
  });
});

describe("session recovery", () => {
  it("renders sign in after an expired API session and aborts active task polling on unmount", async () => {
    const { MemoryRouter } = await import("react-router-dom");
    const { StudioProvider, useStudio } =
      await import("../src/app/StudioContext");
    const signals: AbortSignal[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init: RequestInit) => {
        signals.push(init.signal as AbortSignal);
        const value =
          path === "/api/auth/status"
            ? { authenticated: true, user: { id: 1, email: "a@example.test" } }
            : path === "/api/projects"
              ? [{ slug: "a", name: "Project A" }]
              : path.includes("overview")
                ? {}
                : path.includes("/tasks")
                  ? []
                  : { detail: "expired" };
        return new Response(JSON.stringify(value), {
          status: path.includes("expire") ? 401 : 200,
          headers: { "content-type": "application/json" },
        });
      }),
    );
    function Consumer() {
      const { api } = useStudio();
      return (
        <button onClick={() => void api.get("/api/expire").catch(() => {})}>
          Expire session
        </button>
      );
    }
    const view = render(
      <StrictMode>
        <MemoryRouter>
          <StudioProvider
            authView={({ phase }) => <p>{phase}</p>}
            confirmation={() => null}
            taskView={() => null}
          >
            <Consumer />
          </StudioProvider>
        </MemoryRouter>
      </StrictMode>,
    );
    await waitFor(() =>
      expect(screen.getByText("Expire session")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText("Expire session"));
    await waitFor(() => expect(screen.getByText("login")).toBeInTheDocument());
    view.unmount();
    expect(signals[0].aborted).toBe(true);
    vi.unstubAllGlobals();
  });
});

describe("field accessibility", () => {
  it("associates its label and both descriptions without erasing caller accessibility", async () => {
    const { Field } = await import("../src/components/studio/primitives");
    render(
      <>
        <span id="existing-help">Existing help.</span>
        <Field
          label="Repository ID"
          hint="Use owner/name."
          error="Repository is required."
        >
          <input id="repository-id" aria-describedby="existing-help" />
        </Field>
      </>,
    );
    const input = screen.getByLabelText("Repository ID");
    expect(input).toHaveAttribute("id", "repository-id");
    expect(input).toHaveAccessibleDescription(
      "Existing help. Use owner/name. Repository is required.",
    );
    expect(input).toHaveAttribute("aria-invalid", "true");
  });
  it("labels a generated input ID and a multi-control group", async () => {
    const { Field } = await import("../src/components/studio/primitives");
    render(
      <>
        <Field label="Email">
          <input />
        </Field>
        <Field label="Coordinates">
          <input aria-label="X" />
          <input aria-label="Y" />
        </Field>
      </>,
    );
    expect(screen.getByLabelText("Email")).toHaveAttribute("id");
    expect(
      screen.getByRole("group", { name: "Coordinates" }),
    ).toBeInTheDocument();
  });
});

describe("route navigation keeps session work alive", () => {
  it("finishes delayed auth startup after navigating, then preserves a pending durable save on another navigation", async () => {
    const { MemoryRouter, useNavigate } = await import("react-router-dom");
    const { StudioProvider, useStudio } =
      await import("../src/app/StudioContext");
    let authResolve!: (response: Response) => void,
      saveResolve!: (response: Response) => void;
    let saveSignal: AbortSignal | undefined;
    const response = (body: any) =>
      new Response(JSON.stringify(body), {
        headers: { "content-type": "application/json" },
      });
    vi.stubGlobal(
      "fetch",
      vi.fn((path: string, options: any) => {
        if (path === "/api/auth/status")
          return new Promise<Response>((resolve) => {
            authResolve = resolve;
          });
        if (path.includes("/assistant/conversation")) {
          saveSignal = options.signal;
          return new Promise<Response>((resolve) => {
            saveResolve = resolve;
          });
        }
        return Promise.resolve(
          response(
            path === "/api/projects"
              ? [{ slug: "fixture", name: "Fixture" }]
              : path.includes("/tasks")
                ? []
                : {},
          ),
        );
      }),
    );
    function Navigator() {
      const navigate = useNavigate();
      return (
        <button onClick={() => navigate("/other")}>
          Navigate during startup
        </button>
      );
    }
    function Consumer() {
      const studio = useStudio(),
        [saved, setSaved] = useState(false);
      return (
        <>
          <span>{studio.route}</span>
          <button
            onClick={() =>
              void studio.api
                .put("/api/p/fixture/assistant/conversation", { revision: 1 })
                .then(() => setSaved(true))
            }
          >
            Save durable reply
          </button>
          <button onClick={() => studio.navigate("#/datasets")}>
            Navigate during save
          </button>
          {saved && <p>Reply saved</p>}
        </>
      );
    }
    const view = render(
      <MemoryRouter>
        <Navigator />
        <StudioProvider
          authView={({ phase }) => <p>{phase}</p>}
          confirmation={() => null}
          taskView={() => null}
        >
          <Consumer />
        </StudioProvider>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText("Navigate during startup"));
    await act(async () =>
      authResolve(response({ authenticated: true, user: { id: "u" } })),
    );
    await screen.findByText("Save durable reply");
    expect(screen.getByText("/other")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Save durable reply"));
    fireEvent.click(screen.getByText("Navigate during save"));
    expect(saveSignal?.aborted).toBe(false);
    await act(async () => saveResolve(response({ revision: 2 })));
    expect(screen.getByText("Reply saved")).toBeInTheDocument();
    view.unmount();
    vi.unstubAllGlobals();
  });
});

describe("stacked modal ownership", () => {
  it("isolates background and lower dialog, traps only the top dialog and restores each focus/scroll lease", async () => {
    const { within } = await import("@testing-library/react");
    const alreadyInert = document.createElement("section");
    alreadyInert.setAttribute("inert", "");
    alreadyInert.setAttribute("aria-hidden", "false");
    document.body.append(alreadyInert);
    document.body.style.setProperty("overflow", "scroll", "important");
    const outerClosed = vi.fn(),
      innerClosed = vi.fn();
    function Demo() {
      const [outer, setOuter] = useState(false),
        [inner, setInner] = useState(false);
      return (
        <>
          <button onClick={() => setOuter(true)}>Launch outer</button>
          <Modal
            open={outer}
            title="Outer"
            onClose={() => {
              outerClosed();
              setOuter(false);
            }}
          >
            <button onClick={() => setInner(true)}>Launch inner</button>
            <button>Outer last</button>
          </Modal>
          <Modal
            open={inner}
            title="Inner"
            onClose={() => {
              innerClosed();
              setInner(false);
            }}
          >
            <button>Inner first</button>
            <button>Inner last</button>
          </Modal>
        </>
      );
    }
    const view = render(
      <StrictMode>
        <Demo />
      </StrictMode>,
    );
    const launcher = screen.getByText("Launch outer");
    launcher.focus();
    fireEvent.click(launcher);
    expect(view.container).toHaveAttribute("inert");
    expect(document.body.style.overflow).toBe("hidden");
    const innerLauncher = screen.getByText("Launch inner");
    innerLauncher.focus();
    fireEvent.click(innerLauncher);
    const outer = screen.getByText("Outer").closest('[role="dialog"]')!;
    expect(outer.closest(".modal-overlay")).toHaveAttribute("inert");
    expect(outer.closest(".modal-overlay")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    const inner = screen.getByRole("dialog", { name: "Inner" });
    const close = within(inner).getByRole("button", { name: "Close dialog" });
    expect(close).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(screen.getByText("Inner last")).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(close).toHaveFocus();
    launcher.focus();
    expect(close).toHaveFocus();
    fireEvent.mouseDown(outer.closest(".modal-overlay")!);
    expect(outerClosed).not.toHaveBeenCalled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(innerClosed).toHaveBeenCalledOnce();
    expect(outerClosed).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Inner" }),
      ).not.toBeInTheDocument(),
    );
    expect(innerLauncher).toHaveFocus();
    expect(outer.closest(".modal-overlay")).not.toHaveAttribute("inert");
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(outerClosed).toHaveBeenCalledOnce();
    expect(launcher).toHaveFocus();
    expect(view.container).not.toHaveAttribute("inert");
    expect(view.container).not.toHaveAttribute("aria-hidden");
    expect(document.body.style.overflow).toBe("scroll");
    expect(document.body.style.getPropertyPriority("overflow")).toBe(
      "important",
    );
    expect(alreadyInert).toHaveAttribute("inert");
    expect(alreadyInert).toHaveAttribute("aria-hidden", "false");
    view.unmount();
    alreadyInert.remove();
    document.body.style.removeProperty("overflow");
  });
  it("isolates body regions added while open and restores them on teardown", async () => {
    const view = render(
      <Modal open title="Active" onClose={() => {}}>
        <button>Action</button>
      </Modal>,
    );
    const added = document.createElement("aside");
    document.body.append(added);
    await waitFor(() => expect(added).toHaveAttribute("inert"));
    expect(added).toHaveAttribute("aria-hidden", "true");
    view.unmount();
    expect(added).not.toHaveAttribute("inert");
    expect(added).not.toHaveAttribute("aria-hidden");
    added.remove();
  });
});

describe("confirmation lifecycle", () => {
  it.each(["navigation", "project", "account", "unmount"])(
    "settles a pending confirmation false on %s",
    async (reason) => {
      const { MemoryRouter } = await import("react-router-dom");
      const { StudioProvider, useStudio } =
        await import("../src/app/StudioContext");
      const resolve = vi.fn();
      vi.stubGlobal(
        "fetch",
        vi.fn(
          async (path: string) =>
            new Response(
              JSON.stringify(
                path === "/api/auth/status"
                  ? { authenticated: true, user: { id: "u" } }
                  : path === "/api/projects"
                    ? [
                        { slug: "a", name: "A" },
                        { slug: "b", name: "B" },
                      ]
                    : path.includes("/tasks")
                      ? []
                      : {},
              ),
              { headers: { "content-type": "application/json" } },
            ),
        ),
      );
      function Consumer() {
        const studio = useStudio();
        return (
          <>
            <button
              onClick={() =>
                void studio.confirm("Delete fixture?").then(resolve)
              }
            >
              Ask
            </button>
            <button
              onClick={() => {
                if (reason === "navigation") studio.navigate("#/datasets");
                if (reason === "project") studio.selectProject("b");
                if (reason === "account") studio.onSessionExpired();
              }}
            >
              Change context
            </button>
          </>
        );
      }
      const view = render(
        <MemoryRouter>
          <StudioProvider
            authView={({ phase }) => <p>{phase}</p>}
            confirmation={({ message }) => <p role="alertdialog">{message}</p>}
            taskView={() => null}
          >
            <Consumer />
          </StudioProvider>
        </MemoryRouter>,
      );
      await screen.findByText("Ask");
      fireEvent.click(screen.getByText("Ask"));
      expect(screen.getByRole("alertdialog")).toHaveTextContent(
        "Delete fixture?",
      );
      if (reason === "unmount") view.unmount();
      else fireEvent.click(screen.getByText("Change context"));
      await waitFor(() =>
        expect(resolve).toHaveBeenCalledExactlyOnceWith(false),
      );
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
      view.unmount();
      vi.unstubAllGlobals();
    },
  );
});

it("keeps a nested React-tree child topmost when child effects register before their parent", () => {
  const closeOuter = vi.fn(),
    closeInner = vi.fn();
  function Nested() {
    const [inner, setInner] = useState(true);
    return (
      <Modal open title="Parent modal" onClose={closeOuter}>
        <button>Parent action</button>
        <Modal
          open={inner}
          title="Nested child"
          onClose={() => {
            closeInner();
            setInner(false);
          }}
        >
          <button>Child action</button>
        </Modal>
      </Modal>
    );
  }
  render(
    <StrictMode>
      <Nested />
    </StrictMode>,
  );
  expect(screen.getByRole("dialog")).toHaveAccessibleName("Nested child");
  fireEvent.keyDown(document, { key: "Escape" });
  expect(closeInner).toHaveBeenCalledOnce();
  expect(closeOuter).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog")).toHaveAccessibleName("Parent modal");
});
