/** The only Plotly boundary: owns one WebGL scene and serializes its lifecycle. */
export type SceneCamera = {
  eye?: { x: number; y: number; z: number };
  center?: { x: number; y: number; z: number };
  up?: { x: number; y: number; z: number };
  projection?: { type: "perspective" | "orthographic" };
};
export type SceneFigure = {
  data: Record<string, unknown>[];
  layout: Record<string, unknown>;
};
export type SceneEvent = {
  points?: {
    customdata?: unknown;
    pointNumber?: unknown;
    data?: Record<string, any>;
  }[];
};
type EventHost = HTMLDivElement & {
  on: (event: string, listener: (event: any) => void) => void;
  removeListener: (event: string, listener: (event: any) => void) => void;
};
export type PlotlyRuntime = {
  newPlot: (
    host: HTMLElement,
    data: unknown[],
    layout: object,
    config: object,
  ) => Promise<unknown>;
  react: (
    host: HTMLElement,
    data: unknown[],
    layout: object,
    config: object,
  ) => Promise<unknown>;
  purge: (host: HTMLElement) => void;
  Plots: { resize: (host: HTMLElement) => Promise<unknown> | void };
};
export function sceneCamera(value: unknown): SceneCamera | undefined {
  if (!value || typeof value !== "object") return undefined;
  const input = value as SceneCamera;
  const output: SceneCamera = {};
  for (const key of ["eye", "center", "up"] as const) {
    const vector = input[key];
    if (
      vector &&
      [vector.x, vector.y, vector.z].every(
        (v) => typeof v === "number" && Number.isFinite(v),
      )
    )
      output[key] = { x: vector.x, y: vector.y, z: vector.z };
  }
  if (
    input.projection?.type === "perspective" ||
    input.projection?.type === "orthographic"
  )
    output.projection = { type: input.projection.type };
  return Object.keys(output).length ? output : undefined;
}
const config = {
  responsive: false,
  displaylogo: false,
  displayModeBar: false,
  scrollZoom: true,
};
export class SpatialScene {
  private readonly host: HTMLDivElement;
  private active = true;
  private initialized = false;
  private failed = false;
  private pending?: SceneFigure;
  private draining = false;
  private listeners: [string, (event: any) => void][] = [];
  private observer?: ResizeObserver;
  private listenersAttached = false;
  constructor(
    container: HTMLDivElement,
    private plotly: PlotlyRuntime,
    private events: {
      point: (event: SceneEvent, pin: boolean) => void;
      camera: (camera: SceneCamera) => void;
      error: (message: string) => void;
      ready: () => void;
    },
  ) {
    // Each attempt owns a distinct node. A late Plotly promise may still mutate
    // its node after disposal; it must never mutate or purge a retry's node.
    this.host = document.createElement("div");
    this.host.className = "lab-spatial-surface";
    this.host.style.inlineSize = "100%";
    this.host.style.blockSize = "100%";
    container.append(this.host);
    this.host.addEventListener("webglcontextlost", this.contextLost, true);
    this.listenersAttached = true;
  }
  update(figure: SceneFigure) {
    if (!this.active || this.failed) return;
    this.pending = figure;
    if (!this.draining) void this.drain();
  }
  private async drain() {
    this.draining = true;
    try {
      while (this.active && this.pending) {
        const figure = this.pending;
        this.pending = undefined;
        if (!this.initialized) {
          await this.plotly.newPlot(
            this.host,
            figure.data,
            figure.layout,
            config,
          );
          if (!this.active || this.failed) {
            this.plotly.purge(this.host);
            return;
          }
          this.initialized = true;
          this.attach();
          this.events.ready();
        } else {
          await this.plotly.react(
            this.host,
            figure.data,
            figure.layout,
            config,
          );
          if (!this.active || this.failed) {
            this.plotly.purge(this.host);
            return;
          }
        }
      }
    } catch (cause) {
      // newPlot can allocate before its promise rejects, even after unmount.
      if (!this.active || this.failed) this.plotly.purge(this.host);
      else this.fail(cause instanceof Error ? cause.message : String(cause));
    } finally {
      this.draining = false;
    }
  }
  private attach() {
    const host = this.host as EventHost;
    const bind = (name: string, callback: (event: any) => void) => {
      const guarded = (event: unknown) => {
        if (this.active && !this.failed) callback(event);
      };
      host.on(name, guarded);
      this.listeners.push([name, guarded]);
    };
    bind("plotly_click", (event) => this.events.point(event, true));
    bind("plotly_hover", (event) => this.events.point(event, false));
    bind("plotly_unhover", () => this.events.point({}, false));
    bind("plotly_relayout", (event) => {
      const camera = sceneCamera(event?.["scene.camera"]);
      if (camera) this.events.camera(camera);
    });
    bind("plotly_webglcontextlost", () =>
      this.fail(
        "The 3D graphics context was lost. Retry the graph or switch to 2D.",
      ),
    );
    this.observer = new ResizeObserver(() => {
      if (this.active && !this.failed) {
        try {
          Promise.resolve(this.plotly.Plots.resize(this.host))
            .then(() => {
              if (!this.active || this.failed) this.plotly.purge(this.host);
            })
            .catch((cause) => {
              if (!this.active || this.failed) this.plotly.purge(this.host);
              else this.fail(String(cause));
            });
        } catch (cause) {
          this.fail(String(cause));
        }
      }
    });
    this.observer.observe(this.host);
  }
  private contextLost = (event: Event) => {
    event.preventDefault();
    this.fail(
      "The 3D graphics context was lost. Retry the graph or switch to 2D.",
    );
  };
  private fail(message: string) {
    if (!this.active || this.failed) return;
    this.failed = true;
    this.pending = undefined;
    this.detach();
    this.plotly.purge(this.host);
    this.host.remove();
    this.events.error(message);
  }
  private detach() {
    this.observer?.disconnect();
    this.observer = undefined;
    const host = this.host as EventHost;
    for (const [name, listener] of this.listeners)
      host.removeListener?.(name, listener);
    this.listeners = [];
    if (this.listenersAttached)
      this.host.removeEventListener("webglcontextlost", this.contextLost, true);
    this.listenersAttached = false;
  }
  dispose() {
    if (!this.active) return;
    this.active = false;
    this.pending = undefined;
    this.detach();
    if (!this.failed && (this.initialized || this.draining))
      this.plotly.purge(this.host);
    this.host.remove();
  }
}
export async function loadSpatialRuntime(): Promise<PlotlyRuntime> {
  const module = await import("plotly.js-gl3d-dist-min");
  const runtime = module.default as PlotlyRuntime;
  if (
    ![
      runtime?.newPlot,
      runtime?.react,
      runtime?.purge,
      runtime?.Plots?.resize,
    ].every((fn) => typeof fn === "function")
  )
    throw new Error("The 3D renderer could not be loaded.");
  return runtime;
}
export function requireWebGL() {
  const canvas = document.createElement("canvas");
  const gl = canvas.getContext("webgl2") ?? canvas.getContext("webgl");
  if (!gl)
    throw new Error(
      "3D requires WebGL in this browser. Enable graphics acceleration, retry, or switch to 2D.",
    );
  gl.getExtension("WEBGL_lose_context")?.loseContext();
}
