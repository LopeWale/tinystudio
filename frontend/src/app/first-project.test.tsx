import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "./App";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
let projects: any[];
let failCreate: boolean;
let failRefresh: boolean;
let onlyProviderRefresh: boolean;
let refreshReads: number;
let posts: number;
let userRole: string;
let createStatus: number;
let pendingCreation: Promise<Response> | null;
let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => {
  localStorage.clear();
  window.location.hash = "#/datasets";
  projects = [];
  failCreate = false;
  failRefresh = false;
  onlyProviderRefresh = false;
  refreshReads = 0;
  posts = 0;
  userRole = "admin";
  createStatus = 503;
  pendingCreation = null;
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  fetcher = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/auth/status")
      return json({
        authenticated: true,
        user: { id: "owner", role: userRole, name: "Owner" },
      });
    if (path === "/api/projects") {
      if (init?.method === "POST") {
        posts++;
        if (pendingCreation) return pendingCreation;
        if (failCreate)
          return json({ detail: "Creation refused" }, createStatus);
        projects = [
          { slug: "other", name: "Other project" },
          { slug: "first", name: "First project" },
        ];
        return json(projects[1]);
      }
      if (
        posts &&
        failRefresh &&
        (!onlyProviderRefresh || ++refreshReads === 1)
      )
        return json({ detail: "Refresh refused" }, 503);
      return json(projects);
    }
    if (path === "/api/settings") return json({ settings: [], values: {} });
    if (path === "/api/config" || path.includes("overview")) return json({});
    return json([]);
  });
  vi.stubGlobal("fetch", fetcher);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = "";
  localStorage.clear();
});

it("opens the shared creation dialog over the real shell and supports Cancel, close, and reopening", async () => {
  render(<App />);
  expect(
    await screen.findByRole("dialog", { name: "New project" }),
  ).toBeInTheDocument();
  expect(document.querySelector(".studio-shell")).toBeInTheDocument();
  expect(
    document.querySelector('[aria-label="Main navigation"]'),
  ).toBeInTheDocument();
  expect(document.querySelector(".auth-layout")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "New project" })).toHaveFocus();
  expect(screen.getByRole("heading", { name: "Projects" })).toBeVisible();
  expect(
    screen.getByRole("button", { name: /Current project/ }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "New project" }));
  fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
  const emptyStateTrigger = screen.getByRole("button", {
    name: "Create a project",
  });
  emptyStateTrigger.focus();
  fireEvent.click(emptyStateTrigger);
  expect(screen.getByRole("dialog", { name: "New project" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(emptyStateTrigger).toHaveFocus();
  expect(posts).toBe(0);
  expect(
    fetcher.mock.calls.every(
      ([path]) => path === "/api/projects" || path === "/api/auth/status",
    ),
  ).toBe(true);
});

it("retains a refused draft, then creates once and enters datasets across the first scope change", async () => {
  failCreate = true;
  render(<App />);
  await screen.findByRole("dialog", { name: "New project" });
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "First project" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create project" }));
  expect(await screen.findByText("Creation refused")).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(
    "First project",
  );
  failCreate = false;
  fireEvent.click(screen.getByRole("button", { name: "Create project" }));
  await waitFor(() => expect(window.location.hash).toBe("#/datasets"));
  expect(
    screen.queryByRole("dialog", { name: "New project" }),
  ).not.toBeInTheDocument();
  expect(localStorage.getItem("tinystudio.project")).toBe("first");
  expect(posts).toBe(2);
  expect(
    fetcher.mock.calls.some(([path]) => path.startsWith("/api/p/first/")),
  ).toBe(true);
});

it.each([false, true])(
  "reconciles both project lists after refresh failure (provider only: %s) without repeating creation",
  async (providerOnly) => {
    failRefresh = true;
    onlyProviderRefresh = providerOnly;
    render(<App />);
    await screen.findByRole("dialog", { name: "New project" });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "First project" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    expect(
      await screen.findByText(
        /Project created, but the directory could not refresh/,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts).toBe(1);
    failRefresh = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry projects" }));
    expect(
      await screen.findByRole("button", { name: "First project" }),
    ).toBeInTheDocument();
    expect(posts).toBe(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /Current project/ }),
      ).toBeEnabled(),
    );
    expect(window.location.hash).toBe("#/projects");
    fireEvent.click(screen.getByRole("button", { name: "First project" }));
    fireEvent.click(screen.getByRole("button", { name: "Open project" }));
    await waitFor(() => expect(window.location.hash).toBe("#/datasets"));
    expect(
      screen.getByRole("button", { name: /Current project/ }),
    ).toHaveTextContent("First project");
    expect(posts).toBe(1);
  },
);

it("does not reopen first creation after visiting global Settings and returning", async () => {
  render(<App />);
  await screen.findByRole("dialog", { name: "New project" });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  fireEvent.click(screen.getByRole("link", { name: "Settings" }));
  await screen.findByRole("heading", { name: "Settings" });
  fireEvent.click(screen.getByRole("link", { name: "Projects" }));
  await screen.findByRole("heading", { name: "Projects" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(
    fetcher.mock.calls.some(
      ([path]) => path.includes("/api/p//") || path.includes("/assistant"),
    ),
  ).toBe(false);
});

it.each([
  "/lab",
  "/playground",
  "/assistant",
  "/datasets/missing",
  "/models",
  "/stats",
])("guards %s before any project-dependent request", async (path) => {
  window.location.hash = `#${path}`;
  render(<App />);
  await screen.findByRole("dialog", { name: "New project" });
  expect(window.location.hash).toBe("#/projects");
  expect(
    fetcher.mock.calls.every(
      ([url]) => url === "/api/projects" || url === "/api/auth/status",
    ),
  ).toBe(true);
});

it("preserves non-admin permission checks for direct Users navigation without a project", async () => {
  userRole = "member";
  render(<App />);
  await screen.findByRole("dialog", { name: "New project" });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await act(async () => {
    window.location.hash = "#/users";
  });
  await waitFor(() => expect(window.location.hash).toBe("#/users"));
  expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
  await screen.findByText(/admin/i);
  expect(fetcher.mock.calls.some(([path]) => path === "/api/users")).toBe(
    false,
  );
  expect(fetcher.mock.calls.some(([path]) => path.includes("/assistant"))).toBe(
    false,
  );
});

it("returns to sign-in if first creation expires the session", async () => {
  failCreate = true;
  createStatus = 401;
  render(<App />);
  await screen.findByRole("dialog", { name: "New project" });
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "First project" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create project" }));
  expect(
    await screen.findByRole("heading", { name: "Sign in" }),
  ).toBeInTheDocument();
  expect(document.querySelector(".studio-shell")).not.toBeInTheDocument();
  expect(localStorage.getItem("tinystudio.project")).not.toBe("first");
  expect(posts).toBe(1);
});

it.each(["dismissed", "edited"])(
  "does not select a late first creation over a %s draft",
  async (mode) => {
    let finish!: (response: Response) => void;
    pendingCreation = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    render(<App />);
    await screen.findByRole("dialog", { name: "New project" });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Submitted draft" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    if (mode === "dismissed") {
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      fireEvent.click(screen.getByRole("button", { name: "New project" }));
    }
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Newer draft" },
    });
    projects = [
      { slug: "other", name: "Other project" },
      { slug: "first", name: "First project" },
    ];
    await act(async () => {
      finish(json(projects[1]));
    });
    await screen.findByText("Project created");
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(
      "Newer draft",
    );
    expect(window.location.hash).toBe("#/projects");
    expect(localStorage.getItem("tinystudio.project")).not.toBe("first");
    expect(localStorage.getItem("tinystudio.project")).not.toBe("other");
    expect(posts).toBe(1);
  },
);

it("returns initial creation focus to New project after Escape", async () => {
  render(<App />);
  await screen.findByRole("dialog", { name: "New project" });
  fireEvent.keyDown(screen.getByRole("dialog", { name: "New project" }), {
    key: "Escape",
  });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "New project" })).toHaveFocus();
});
