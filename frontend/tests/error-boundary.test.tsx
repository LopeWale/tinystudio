import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RouteErrorBoundary } from "../src/app/RouteErrorBoundary";
afterEach(cleanup);
describe("route rendering recovery", () => {
  it("contains a render exception below navigation and retries the real child after correction", () => {
    const consoleError = vi
        .spyOn(console, "error")
        .mockImplementation(() => {}),
      onError = vi.fn();
    let broken = true;
    function Page() {
      if (broken) throw new Error("fixture render failure");
      return <p>Actual page content</p>;
    }
    render(
      <>
        <nav>Workspace navigation</nav>
        <RouteErrorBoundary resetKey="a:/models" onError={onError}>
          <Page />
        </RouteErrorBoundary>
      </>,
    );
    expect(screen.getByText("Workspace navigation")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "unexpected interface error",
    );
    expect(onError).toHaveBeenCalledOnce();
    broken = false;
    fireEvent.click(screen.getByText("Try this page again"));
    expect(screen.getByText("Actual page content")).toBeInTheDocument();
    consoleError.mockRestore();
  });
  it("resets the failed page on route/account/project key changes", () => {
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => {});
    function Broken(): never {
      throw new Error("fixture");
    }
    const view = render(
      <RouteErrorBoundary resetKey="u:a:/models">
        <Broken />
      </RouteErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    view.rerender(
      <RouteErrorBoundary resetKey="u:b:/datasets">
        <p>Another scoped page</p>
      </RouteErrorBoundary>,
    );
    expect(screen.getByText("Another scoped page")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    consoleError.mockRestore();
  });
});

describe("route module download recovery", () => {
  it("shows loading while downloading and renders the actual resolved route", async () => {
    const { Suspense } = await import("react");
    const { act } = await import("@testing-library/react");
    const { lazyRoute } = await import("../src/app/lazyRoute");
    let resolve!: (component: () => React.ReactElement) => void;
    const loader = vi.fn(
        () => new Promise<() => React.ReactElement>((r) => (resolve = r)),
      ),
      Route = lazyRoute(loader);
    render(
      <RouteErrorBoundary resetKey="p:/route">
        <Suspense fallback={<p role="status">Loading route code</p>}>
          <Route />
        </Suspense>
      </RouteErrorBoundary>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading route code");
    await act(async () => resolve(() => <p>Downloaded route content</p>));
    expect(screen.getByText("Downloaded route content")).toBeInTheDocument();
    expect(loader).toHaveBeenCalledOnce();
  });
  it("offers a full reload for a cached lazy rejection instead of a false local retry", async () => {
    const consoleError = vi
        .spyOn(console, "error")
        .mockImplementation(() => {}),
      { Suspense } = await import("react"),
      { lazyRoute } = await import("../src/app/lazyRoute");
    const loader = vi.fn(async () => {
        throw new Error("controlled chunk download failure");
      }),
      Route = lazyRoute(loader),
      reload = vi.fn();
    const view = render(
      <RouteErrorBoundary resetKey="p:/route" onReload={reload}>
        <Suspense fallback={<p>Loading route</p>}>
          <Route />
        </Suspense>
      </RouteErrorBoundary>,
    );
    const recovery = await screen.findByRole("link", {
      name: "Reload this page",
    });
    expect(recovery).toHaveAttribute("href", window.location.href);
    expect(screen.queryByText("Try this page again")).not.toBeInTheDocument();
    fireEvent.click(recovery);
    expect(reload).toHaveBeenCalledOnce();
    expect(loader).toHaveBeenCalledOnce();
    view.rerender(
      <RouteErrorBoundary resetKey="p:/another" onReload={reload}>
        <p>Another working route</p>
      </RouteErrorBoundary>,
    );
    expect(screen.getByText("Another working route")).toBeInTheDocument();
    consoleError.mockRestore();
  });
});
