import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { StudioProvider, useStudio, type StudioRuntime } from "./StudioContext";
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
function deferred() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve: (response: Response) => resolve(response) };
}
let previous: string | null;
beforeEach(() => {
  previous = localStorage.getItem("tinystudio.project");
  localStorage.setItem("tinystudio.project", "b");
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  if (previous === null) localStorage.removeItem("tinystudio.project");
  else localStorage.setItem("tinystudio.project", previous);
});
const auth = (id = "user-a") => json({ authenticated: true, user: { id } });
function transport(
  projects: (init: RequestInit) => Promise<Response>,
  status = async () => auth(),
) {
  const fetcher = vi.fn((path: string, init: RequestInit) =>
    path === "/api/auth/status"
      ? status()
      : path === "/api/projects"
        ? projects(init)
        : Promise.resolve(json(path.includes("/tasks") ? [] : {})),
  );
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
function mount(strict = false) {
  const phases: string[] = [];
  let runtime!: StudioRuntime;
  function Auth({ phase, error, onRetry, onSubmit }: any) {
    const studio = useStudio();
    return (
      <>
        <p>{phase}</p>
        {error && <p role="alert">{error}</p>}
        <button onClick={onRetry}>Retry</button>
        <button onClick={studio.onSessionExpired}>Expire</button>
        <button onClick={() => void onSubmit({})}>Authenticate</button>
      </>
    );
  }
  function Consumer() {
    runtime = useStudio();
    return (
      <>
        <output data-testid="project">{runtime.project}</output>
        <output data-testid="projects">
          {runtime.projects.map((p) => p.slug).join(",")}
        </output>
        <output data-testid="route">{runtime.route}</output>
      </>
    );
  }
  function Navigator() {
    const go = useNavigate();
    return <button onClick={() => go("/other")}>Navigate</button>;
  }
  const content = (
    <MemoryRouter>
      <Navigator />
      <StudioProvider
        authView={(props) => {
          phases.push(props.phase);
          return <Auth {...props} />;
        }}
        confirmation={() => null}
        taskView={() => null}
      >
        <Consumer />
      </StudioProvider>
    </MemoryRouter>
  );
  return {
    ...render(strict ? <StrictMode>{content}</StrictMode> : content),
    phases,
    runtime: () => runtime,
  };
}
describe("project lookup during authenticated startup", () => {
  it("retains loading until projects arrive and restores the saved project without onboarding", async () => {
    const pending = deferred();
    const fetcher = transport(() => pending.promise);
    const view = mount();
    await waitFor(() =>
      expect(
        fetcher.mock.calls.some(([path]) => path === "/api/projects"),
      ).toBe(true),
    );
    expect(screen.getByText("loading")).toBeInTheDocument();
    expect(view.phases).not.toContain("project");
    await act(async () =>
      pending.resolve(json([{ slug: "a" }, { slug: "b" }])),
    );
    expect(await screen.findByTestId("project")).toHaveTextContent(/^b$/);
    expect(view.phases).not.toContain("project");
  });
  it("renders the authenticated workspace only after a successful empty list", async () => {
    const pending = deferred();
    const fetcher = transport(() => pending.promise);
    const view = mount();
    await waitFor(() =>
      expect(
        fetcher.mock.calls.some(([path]) => path === "/api/projects"),
      ).toBe(true),
    );
    expect(view.phases).not.toContain("project");
    await act(async () => pending.resolve(json([])));
    expect(await screen.findByTestId("project")).toBeEmptyDOMElement();
    expect(view.runtime().firstProjectPrompt).toBe(true);
    expect(view.phases).not.toContain("project");
  });
  it("reports lookup failure and retries projects while keeping loading until retry succeeds", async () => {
    const pending = deferred();
    let reads = 0;
    const fetcher = transport(() =>
      ++reads === 1
        ? Promise.resolve(json({ detail: "Projects unavailable" }, 503))
        : pending.promise,
    );
    const view = mount();
    expect(await screen.findByText("unavailable")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Projects unavailable");
    expect(view.phases).not.toContain("project");
    fireEvent.click(screen.getByText("Retry"));
    await waitFor(() => expect(reads).toBe(2));
    expect(screen.getByText("loading")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    await act(async () => pending.resolve(json([{ slug: "a" }])));
    expect(await screen.findByTestId("project")).toHaveTextContent(/^a$/);
    expect(
      fetcher.mock.calls.filter(([path]) => path === "/api/auth/status"),
    ).toHaveLength(1);
    expect(view.phases).not.toContain("project");
  });
  it("keeps login visible after a project lookup expires the session", async () => {
    transport(async () => json({ detail: "Expired" }, 401));
    const view = mount();
    expect(await screen.findByText("login")).toBeInTheDocument();
    expect(view.phases).not.toContain("unavailable");
    expect(view.phases).not.toContain("project");
  });
  it.each([200, 401])(
    "discards an old HTTP %s response after a new account opens its workspace",
    async (lateStatus) => {
      const pending = deferred();
      let reads = 0,
        statuses = 0;
      let oldSignal: AbortSignal | null | undefined;
      transport(
        (init) => {
          if (++reads === 1) {
            oldSignal = init.signal;
            return pending.promise;
          }
          return Promise.resolve(json([{ slug: "b" }]));
        },
        async () => auth(++statuses === 1 ? "user-a" : "user-b"),
      );
      const view = mount();
      await waitFor(() => expect(reads).toBe(1));
      fireEvent.click(screen.getByText("Expire"));
      expect(screen.getByText("login")).toBeInTheDocument();
      expect(oldSignal?.aborted).toBe(true);
      fireEvent.click(screen.getByText("Authenticate"));
      expect(await screen.findByTestId("project")).toHaveTextContent(/^b$/);
      await act(async () =>
        pending.resolve(
          json(
            lateStatus === 200 ? [{ slug: "a" }] : { detail: "Old expiry" },
            lateStatus,
          ),
        ),
      );
      expect(screen.getByTestId("project")).toHaveTextContent(/^b$/);
      expect(screen.getByTestId("projects")).toHaveTextContent(/^b$/);
      expect(view.runtime().user.id).toBe("user-b");
      expect(screen.queryByText("login")).toBeNull();
    },
  );
  it("finishes project lookup after route navigation in StrictMode", async () => {
    const pending = deferred();
    const signals: (AbortSignal | null | undefined)[] = [];
    transport((init) => {
      signals.push(init.signal);
      return pending.promise;
    });
    const view = mount(true);
    await waitFor(() => expect(signals.length).toBeGreaterThan(0));
    fireEvent.click(screen.getByText("Navigate"));
    expect(signals.at(-1)?.aborted).toBe(false);
    await act(async () => pending.resolve(json([{ slug: "b" }])));
    expect(await screen.findByTestId("route")).toHaveTextContent("/other");
    view.unmount();
  });
  it("aborts a pending project lookup on unmount and discards its late response in StrictMode", async () => {
    const pending = deferred();
    const signals: (AbortSignal | null | undefined)[] = [];
    transport((init) => {
      signals.push(init.signal);
      return pending.promise;
    });
    const view = mount(true);
    await waitFor(() => expect(signals.length).toBeGreaterThan(0));
    expect(signals.at(-1)?.aborted).toBe(false);
    view.unmount();
    expect(signals.every((signal) => signal?.aborted)).toBe(true);
    const phasesBeforeResponse = [...view.phases];
    await act(async () => pending.resolve(json([{ slug: "late-project" }])));
    expect(view.phases).toEqual(phasesBeforeResponse);
    expect(localStorage.getItem("tinystudio.project")).toBe("b");
  });
  it("rejects an older overlapping refresh without replacing the current project list", async () => {
    const older = deferred(),
      newer = deferred();
    let reads = 0;
    transport(() =>
      ++reads === 1
        ? Promise.resolve(json([{ slug: "b" }]))
        : reads === 2
          ? older.promise
          : newer.promise,
    );
    const view = mount();
    await screen.findByTestId("project");
    let first!: Promise<unknown>, second!: Promise<void>;
    act(() => {
      first = view
        .runtime()
        .refreshProjects()
        .catch((error) => error);
      second = view.runtime().refreshProjects();
    });
    await act(async () => {
      newer.resolve(json([{ slug: "b", name: "Current" }]));
      await second;
    });
    await act(async () => {
      older.resolve(json([{ slug: "a", name: "Stale" }]));
      await first;
    });
    expect(await first).toMatchObject({ name: "AbortError" });
    expect(screen.getByTestId("project")).toHaveTextContent(/^b$/);
    expect(view.runtime().projects[0].name).toBe("Current");
  });
});
