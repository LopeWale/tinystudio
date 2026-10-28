import { useEffect, useRef, useState, type KeyboardEventHandler } from "react";
import { Button } from "../../components/studio/primitives";
import { spatialFigure } from "./spatial-figure";
import { spatialPickedPoint } from "./spatial-picking";
import {
  SpatialScene,
  loadSpatialRuntime,
  requireWebGL,
  type SceneEvent,
} from "./spatial-runtime";
import type { TrendController } from "./trend-controller";
export function SpatialChart({
  controller: c,
  onKeyDown,
}: {
  controller: TrendController;
  onKeyDown: KeyboardEventHandler<HTMLDivElement>;
}) {
  const host = useRef<HTMLDivElement>(null);
  const latest = useRef(c);
  latest.current = c;
  const instance = useRef<SpatialScene | null>(null);
  const draw = useRef<(() => void) | undefined>(undefined);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    let appearance: MutationObserver | undefined;
    const selectedHost = host.current;
    if (!selectedHost) return;
    setError("");
    setReady(false);
    async function mount() {
      requireWebGL();
      const runtime = await loadSpatialRuntime();
      if (!active || !selectedHost) return;
      const point = (event: SceneEvent, pin: boolean) => {
        const found = spatialPickedPoint(
          event,
          latest.current.spatialGroup?.points ?? [],
        );
        latest.current.pointEvent(found ?? null, pin);
      };
      const scene = new SpatialScene(selectedHost, runtime, {
        point,
        camera: (camera) => latest.current.changeCamera(camera),
        ready: () => {
          if (active) setReady(true);
        },
        error: (message) => {
          if (active) {
            setError(message);
            setReady(false);
          }
        },
      });
      instance.current = scene;
      draw.current = () => {
        if (!active || !latest.current.spatialGroup) return;
        const current = latest.current;
        const style = getComputedStyle(selectedHost);
        // Canvas libraries need resolved colors; tokens remain the single source.
        const probe = document.createElement("span");
        selectedHost.append(probe);
        const token = (name: string) => {
          probe.style.color = `var(--color-${name})`;
          return getComputedStyle(probe).color;
        };
        const theme = {
          text: token("text-primary"),
          secondary: token("text-secondary"),
          border: token("separator-border"),
          surface: token("background-primary-default"),
          colors: [5, 4, 3, 2, 1].map((index) => token(`chart-${index}`)),
          font: style.fontFamily,
        };
        probe.remove();
        try {
          scene.update(
            spatialFigure(current.spatialGroup!, {
              geometry: current.geometry,
              opacity: current.opacity,
              selectedKey: current.selected?.identity.key,
              camera: current.camera,
              revision: JSON.stringify([
                current.spatialGroup!.key,
                current.mapping,
                current.cameraRevision,
              ]),
              theme,
            }),
          );
        } catch (cause) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      };
      draw.current();
      appearance = new MutationObserver(() => draw.current?.());
      appearance.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["class", "style"],
      });
    }
    mount().catch((cause) => {
      if (active)
        setError(cause instanceof Error ? cause.message : String(cause));
    });
    return () => {
      active = false;
      appearance?.disconnect();
      draw.current = undefined;
      instance.current?.dispose();
      instance.current = null;
    };
  }, [attempt]);
  useEffect(() => {
    draw.current?.();
  }, [
    c.spatialGroup,
    c.mapping,
    c.geometry,
    c.opacity,
    c.camera,
    c.cameraRevision,
    c.selected?.identity.key,
  ]);
  return (
    <div className="lab-spatial-frame">
      {!ready && !error && (
        <p role="status" className="lab-spatial-status">
          Loading 3D graph…
        </p>
      )}
      {error && (
        <div role="alert" className="lab-spatial-status">
          <strong>3D graph unavailable</strong>
          <p>{error}</p>
          <div className="lab-actions">
            <Button size="sm" onClick={() => setAttempt((value) => value + 1)}>
              Retry 3D
            </Button>
            <Button size="sm" onClick={() => c.changeView("2d")}>
              Use 2D trends
            </Button>
          </div>
        </div>
      )}
      <div
        ref={host}
        role="group"
        aria-label="Interactive 3D graph; drag to rotate, scroll to zoom, arrow keys to select records"
        tabIndex={0}
        onKeyDown={onKeyDown}
        className="lab-spatial-canvas"
        data-render-state={error ? "error" : ready ? "ready" : "loading"}
      />
    </div>
  );
}
