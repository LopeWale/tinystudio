import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { App } from "../src/app/App";
import { createIllustrativeCatalogRuntime } from "../src/app/ComponentCatalogRoute";
const initialHref = window.location.href;
afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", initialHref);
  vi.unstubAllGlobals();
});
describe("unauthenticated development catalog entry", () => {
  it("opens actual local examples without mounting authentication or requesting HTTP/model work", async () => {
    expect(import.meta.env.DEV).toBe(true);
    const request = vi.fn(() => {
        throw new Error("Catalog must not access fetch");
      }),
      xhr = vi.fn(() => {
        throw new Error("Catalog must not access XHR");
      });
    vi.stubGlobal("fetch", request);
    vi.stubGlobal("XMLHttpRequest", xhr);
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    window.history.replaceState(null, "", "#/__components");
    render(<App />);
    expect(
      await screen.findByText(/No account or project is connected/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /All records, work states, and counters here are illustrative/,
      ),
    ).toBeInTheDocument();
    fireEvent.change(
      screen.getByRole("textbox", { name: "Illustrative name" }),
      { target: { value: "Unauthenticated local edit" } },
    );
    expect(
      screen.getByText("Local input: Unauthenticated local edit"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Sign in")).not.toBeInTheDocument();
    expect(request).not.toHaveBeenCalled();
    expect(xhr).not.toHaveBeenCalled();
  });
  it("rejects every API transport operation and carries no authenticated user", async () => {
    const request = vi.fn();
    vi.stubGlobal("fetch", request);
    const runtime = createIllustrativeCatalogRuntime();
    expect(runtime.user).toBeNull();
    expect(runtime.project).toBe("ILLUSTRATIVE_LOCAL_ONLY");
    for (const operation of [
      () => runtime.api.get("/api/auth/status"),
      () => runtime.api.post("/api/playground/chat", {}),
      () => runtime.api.put("/api/settings", {}),
      () => runtime.api.patch("/api/projects", {}),
      () => runtime.api.del("/api/datasets"),
      () => runtime.api.upload("/api/upload", new FormData()),
    ])
      await expect(operation()).rejects.toThrow(
        "network and workspace operations are disabled",
      );
    expect(request).not.toHaveBeenCalled();
  });
});
