import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  sceneCamera,
  SpatialScene,
  type PlotlyRuntime,
  type SceneFigure,
} from "./spatial-runtime";
function deferred() {
  let resolve!: (value?: unknown) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<unknown>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const settle = async () => {
  for (let index = 0; index < 8; index++) await Promise.resolve();
};
const figure = (id: string): SceneFigure => ({
  data: [{ id }],
  layout: { title: id },
});
let resizeCallback: ResizeObserverCallback;
let disconnect: ReturnType<typeof vi.fn>;
beforeEach(() => {
  disconnect = vi.fn();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: ResizeObserverCallback) {
        resizeCallback = callback;
      }
      observe = vi.fn();
      disconnect = disconnect;
    },
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
});
function harness() {
  const listeners = new Map<string, (event: any) => void>();
  const container = document.createElement("div");
  const emitter = {
    on: vi.fn((name: string, callback: (event: any) => void) =>
      listeners.set(name, callback),
    ),
    removeListener: vi.fn((name: string) => listeners.delete(name)),
  };
  const runtime = {
    newPlot: vi.fn().mockResolvedValue(undefined),
    react: vi.fn().mockResolvedValue(undefined),
    purge: vi.fn(),
    Plots: { resize: vi.fn() },
  } satisfies PlotlyRuntime;
  const events = {
    point: vi.fn(),
    camera: vi.fn(),
    ready: vi.fn(),
    error: vi.fn(),
  };
  const scene = new SpatialScene(container, runtime, events);
  // Plotly equips its rendering target with these event methods during init.
  const host = Object.assign(container.firstElementChild!, emitter);
  return { scene, runtime, events, host, container, listeners, emitter };
}
describe("owned serialized 3D lifecycle", () => {
  it.each(["dispose", "context loss"])(
    "cleans a successful update that completes after %s",
    async (ending) => {
      const { scene, runtime, host, listeners, events } = harness();
      const live = new Set<HTMLElement>();
      runtime.newPlot.mockImplementationOnce(async (target) => {
        live.add(target);
      });
      runtime.purge.mockImplementation((target) => {
        live.delete(target);
      });
      scene.update(figure("initial"));
      await settle();
      const pending = deferred();
      runtime.react.mockImplementationOnce(async (target) => {
        await pending.promise;
        live.add(target);
      });
      scene.update(figure("delayed"));
      const oldClick = listeners.get("plotly_click")!;
      if (ending === "dispose") scene.dispose();
      else
        host.dispatchEvent(new Event("webglcontextlost", { cancelable: true }));
      expect(live.size).toBe(0);
      pending.resolve();
      await settle();
      expect(live.size).toBe(0);
      oldClick({ points: [{ customdata: "stale" }] });
      expect(events.point).not.toHaveBeenCalled();
      scene.dispose();
    },
  );
  it("keeps late cleanup away from a replacement renderer in the same container", async () => {
    const { scene, runtime, host, container, events, emitter } = harness();
    scene.update(figure("initial"));
    await settle();
    const oldTarget = runtime.newPlot.mock.calls[0][0];
    const pending = deferred();
    runtime.react.mockReturnValueOnce(pending.promise);
    scene.update(figure("delayed"));
    scene.dispose();
    const replacement = new SpatialScene(
      container as HTMLDivElement,
      runtime,
      events,
    );
    Object.assign(container.firstElementChild!, emitter);
    replacement.update(figure("replacement"));
    await settle();
    const newTarget = runtime.newPlot.mock.calls[1][0];
    expect(newTarget).not.toBe(oldTarget);
    runtime.purge.mockClear();
    pending.resolve();
    await settle();
    expect(
      runtime.purge.mock.calls.some(([target]) => target === oldTarget),
    ).toBe(true);
    expect(
      runtime.purge.mock.calls.some(([target]) => target === newTarget),
    ).toBe(false);
    replacement.dispose();
  });
  it("keeps a lost initializing context terminal when newPlot later succeeds", async () => {
    const { scene, runtime, host, container, events } = harness();
    const initial = deferred();
    const live = new Set<HTMLElement>();
    runtime.newPlot.mockImplementationOnce(async (target) => {
      await initial.promise;
      live.add(target);
    });
    runtime.purge.mockImplementation((target) => {
      live.delete(target);
    });
    scene.update(figure("initial"));
    host.dispatchEvent(new Event("webglcontextlost", { cancelable: true }));
    expect(events.error).toHaveBeenCalledOnce();
    expect(container.childElementCount).toBe(0);
    initial.resolve();
    await settle();
    expect(live.size).toBe(0);
    expect(events.ready).not.toHaveBeenCalled();
    expect(host.on).not.toHaveBeenCalled();
    scene.dispose();
  });
  it("coalesces queued updates and serializes react after pending newPlot and react", async () => {
    const { scene, runtime, events } = harness();
    const initial = deferred(),
      update = deferred();
    runtime.newPlot.mockReturnValueOnce(initial.promise);
    runtime.react.mockReturnValueOnce(update.promise);
    scene.update(figure("initial"));
    scene.update(figure("superseded"));
    scene.update(figure("latest"));
    expect(runtime.newPlot).toHaveBeenCalledTimes(1);
    expect(runtime.react).not.toHaveBeenCalled();
    initial.resolve();
    await settle();
    expect(runtime.react).toHaveBeenCalledTimes(1);
    expect(runtime.react.mock.calls[0][1]).toEqual([{ id: "latest" }]);
    scene.update(figure("later-superseded"));
    scene.update(figure("last"));
    expect(runtime.react).toHaveBeenCalledTimes(1);
    update.resolve();
    await settle();
    expect(runtime.react).toHaveBeenCalledTimes(2);
    expect(runtime.react.mock.calls[1][1]).toEqual([{ id: "last" }]);
    expect(events.ready).toHaveBeenCalledTimes(1);
    scene.dispose();
  });
  it("disposes an initialization that completes after unmount without delivering callbacks", async () => {
    const { scene, runtime, events, host } = harness();
    const initial = deferred();
    runtime.newPlot.mockReturnValueOnce(initial.promise);
    scene.update(figure("initial"));
    scene.update(figure("queued"));
    scene.dispose();
    expect(runtime.purge).toHaveBeenCalledOnce();
    expect(host.parentElement).toBeNull();
    initial.resolve();
    await settle();
    expect(runtime.purge).toHaveBeenCalledTimes(2);
    expect(host.on).not.toHaveBeenCalled();
    expect(runtime.react).not.toHaveBeenCalled();
    expect(events.ready).not.toHaveBeenCalled();
    expect(events.error).not.toHaveBeenCalled();
    scene.update(figure("ignored"));
    expect(runtime.newPlot).toHaveBeenCalledOnce();
  });
  it("cleans a pending initialization rejected after unmount without surfacing stale errors", async () => {
    const { scene, runtime, events } = harness();
    const initial = deferred();
    runtime.newPlot.mockReturnValueOnce(initial.promise);
    scene.update(figure("initial"));
    scene.dispose();
    initial.reject(new Error("initialization failed after unmount"));
    await settle();
    expect(runtime.purge).toHaveBeenCalledTimes(2);
    expect(events.ready).not.toHaveBeenCalled();
    expect(events.error).not.toHaveBeenCalled();
  });
  it("reports context loss, purges and suppresses retained old click callbacks", async () => {
    const { scene, runtime, events, listeners, host } = harness();
    scene.update(figure("initial"));
    await settle();
    const click = listeners.get("plotly_click")!;
    click({ points: [{ customdata: "actual-row" }] });
    expect(events.point).toHaveBeenCalledOnce();
    const lost = new Event("webglcontextlost", { cancelable: true });
    host.dispatchEvent(lost);
    expect(lost.defaultPrevented).toBe(true);
    expect(events.error).toHaveBeenCalledWith(
      expect.stringContaining("context was lost"),
    );
    expect(runtime.purge).toHaveBeenCalledOnce();
    expect(disconnect).toHaveBeenCalledOnce();
    expect(listeners.size).toBe(0);
    click({ points: [{ customdata: "stale" }] });
    expect(events.point).toHaveBeenCalledOnce();
    scene.update(figure("ignored"));
    scene.dispose();
    expect(runtime.react).not.toHaveBeenCalled();
    expect(runtime.purge).toHaveBeenCalledOnce();
  });
  it("cleans resources and reports a rejected initialization", async () => {
    const { scene, runtime, events, host } = harness();
    runtime.newPlot.mockRejectedValueOnce(new Error("allocation failed"));
    scene.update(figure("initial"));
    await settle();
    expect(events.error).toHaveBeenCalledWith("allocation failed");
    expect(events.ready).not.toHaveBeenCalled();
    expect(runtime.purge).toHaveBeenCalledOnce();
    expect(host.on).not.toHaveBeenCalled();
    scene.dispose();
    expect(runtime.purge).toHaveBeenCalledOnce();
  });
  it("cleans initialized resources and ignores stale callbacks after rejected react", async () => {
    const { scene, runtime, events, listeners } = harness();
    scene.update(figure("initial"));
    await settle();
    const camera = listeners.get("plotly_relayout")!;
    runtime.react.mockRejectedValueOnce(new Error("update failed"));
    scene.update(figure("bad"));
    await settle();
    expect(events.error).toHaveBeenCalledWith("update failed");
    expect(runtime.purge).toHaveBeenCalledOnce();
    expect(disconnect).toHaveBeenCalledOnce();
    camera({ "scene.camera": { eye: { x: 1, y: 2, z: 3 } } });
    expect(events.camera).not.toHaveBeenCalled();
    scene.dispose();
  });
  it("contains a synchronous resize failure and removes its observer", async () => {
    const { scene, runtime, events } = harness();
    scene.update(figure("initial"));
    await settle();
    runtime.Plots.resize.mockImplementationOnce(() => {
      throw new Error("resize failed");
    });
    expect(() => resizeCallback([], {} as ResizeObserver)).not.toThrow();
    expect(events.error).toHaveBeenCalledWith(
      expect.stringContaining("resize failed"),
    );
    expect(runtime.purge).toHaveBeenCalledOnce();
    expect(disconnect).toHaveBeenCalledOnce();
    scene.dispose();
  });
});
describe("finite camera state", () => {
  it.each([Infinity, -Infinity, NaN, "1", null])(
    "rejects nonnumeric or nonfinite vector component %s",
    (x) => {
      expect(sceneCamera({ eye: { x, y: 1, z: 2 } })).toBeUndefined();
    },
  );
  it("retains valid camera components without copying invalid vectors or unknown keys", () => {
    expect(
      sceneCamera({
        eye: { x: NaN, y: 1, z: 2 },
        center: { x: 0, y: 0, z: 0 },
        projection: { type: "orthographic" },
        untrusted: "ignored",
      }),
    ).toEqual({
      center: { x: 0, y: 0, z: 0 },
      projection: { type: "orthographic" },
    });
  });
});
