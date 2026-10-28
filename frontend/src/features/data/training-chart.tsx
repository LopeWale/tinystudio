import { SimpleTable } from "../../components/studio/data-table";
import { useState } from "react";
import {
  Button,
  Evidence,
  Panel,
  EmptyState,
  Badge,
} from "../../components/studio/primitives";
import { Check, Select, Input } from "./common";
import "./analytical-workspace.css";

type Sample = { step: number; loss?: number; eval_loss?: number };
type Run = { number: number; loss?: Sample[]; checkpoints?: number[] };
const valid = (v: unknown): v is number =>
  typeof v === "number" && Number.isFinite(v);
export function lossSamples(run: Run, kind: "loss" | "eval_loss") {
  return (run.loss || [])
    .filter((p) => valid(p.step) && valid(p[kind]))
    .sort((a, b) => a.step - b.step);
}
export function TrainingChart({
  experiments,
  onInspect,
}: {
  experiments: Run[];
  onInspect: (n: number) => void;
}) {
  const [hidden, setHidden] = useState<number[]>([]),
    [evaluation, setEvaluation] = useState(true),
    [pinned, pin] = useState<number | null>(null),
    [hover, preview] = useState<number | null>(null);
  const [start, setStart] = useState(""),
    [end, setEnd] = useState(""),
    [range, setRange] = useState<[number, number] | null>(null);
  const visible = experiments.filter((e) => !hidden.includes(e.number));
  const samples = visible.flatMap((e) => [
    ...lossSamples(e, "loss").map((p) => ({ step: p.step, value: p.loss! })),
    ...(evaluation
      ? lossSamples(e, "eval_loss").map((p) => ({
          step: p.step,
          value: p.eval_loss!,
        }))
      : []),
  ]);
  const steps = [...new Set(samples.map((p) => p.step))].sort((a, b) => a - b);
  const minX = range?.[0] ?? Math.min(0, ...steps),
    maxX = range?.[1] ?? Math.max(1, ...steps);
  const maxY = Math.max(1, ...samples.map((p) => p.value)),
    minY = Math.min(0, ...samples.map((p) => p.value));
  const x = (n: number) => 56 + ((n - minX) / (maxX - minX || 1)) * 640,
    y = (n: number) => 248 - ((n - minY) / (maxY - minY || 1)) * 212;
  const step = pinned ?? hover;
  return (
    <div className="analytical-evidence-grid">
      <Panel
        className="analytical-chart-panel"
        title="Training loss comparison"
        actions={<Badge>{visible.length} runs visible</Badge>}
      >
        <div className="analytical-chart-tools">
          <Check
            label="Show evaluation loss"
            checked={evaluation}
            onChange={setEvaluation}
          />
          <span className="data-muted">
            Loss · shared axes · lower is better
          </span>
        </div>
        <div className="analytical-legend">
          {experiments.map((e, i) => (
            <Button
              key={e.number}
              size="sm"
              aria-pressed={!hidden.includes(e.number)}
              onClick={() =>
                setHidden(
                  hidden.includes(e.number)
                    ? hidden.filter((n) => n !== e.number)
                    : [...hidden, e.number],
                )
              }
            >
              <span
                className="analytical-swatch"
                style={{ background: `var(--color-chart-${(i % 5) + 1})` }}
              />
              Experiment {e.number}
            </Button>
          ))}
        </div>
        {samples.length ? (
          <Evidence label="Training loss series" data={samples}>
            <svg
              viewBox="0 0 740 290"
              className="analytical-loss-chart"
              aria-label="Training loss by step"
              onMouseLeave={() => preview(null)}
            >
              <text x="12" y="18">
                Loss
              </text>
              <text x="650" y="283">
                Training step
              </text>
              {[0, 0.25, 0.5, 0.75, 1].map((t) => (
                <g key={t}>
                  <line
                    x1="56"
                    x2="696"
                    y1={36 + t * 212}
                    y2={36 + t * 212}
                    className="analytical-gridline"
                  />
                  <text x="8" y={40 + t * 212}>
                    {(maxY - t * (maxY - minY)).toFixed(2)}
                  </text>
                  <text x={56 + t * 640} y="266" textAnchor="middle">
                    {Math.round(minX + t * (maxX - minX))}
                  </text>
                </g>
              ))}
              {visible.flatMap((e) =>
                (evaluation
                  ? (["loss", "eval_loss"] as const)
                  : (["loss"] as const)
                ).map((kind) => {
                  const points = lossSamples(e, kind).filter(
                    (p) => p.step >= minX && p.step <= maxX,
                  );
                  const color = `var(--color-chart-${(experiments.indexOf(e) % 5) + 1})`;
                  // An absent metric interrupts the path; it is never interpolated as zero.
                  const logged = (e.loss || [])
                    .filter((p) => valid(p.step))
                    .sort((a, b) => a.step - b.step);
                  let path = "",
                    connected = false;
                  for (const p of logged) {
                    if (!valid(p[kind]) || p.step < minX || p.step > maxX) {
                      connected = false;
                      continue;
                    }
                    path += `${connected ? "L" : "M"}${x(p.step)},${y(p[kind]!)} `;
                    connected = true;
                  }
                  return (
                    <g key={`${e.number}-${kind}`}>
                      <path
                        d={path}
                        fill="none"
                        stroke={color}
                        strokeWidth="2"
                        strokeDasharray={
                          kind === "eval_loss" ? "5 4" : undefined
                        }
                      />
                      {points.map((p, i) => (
                        <circle
                          key={`${p.step}-${i}`}
                          cx={x(p.step)}
                          cy={y(p[kind]!)}
                          r={step === p.step ? 6 : 4}
                          fill={color}
                          stroke="var(--color-background-primary-default)"
                          strokeWidth="2"
                          tabIndex={0}
                          role="button"
                          aria-label={`Experiment ${e.number}, step ${p.step}, ${kind === "loss" ? "train" : "eval"} loss ${p[kind]}`}
                          aria-pressed={pinned === p.step}
                          onMouseEnter={() => preview(p.step)}
                          onFocus={() => preview(p.step)}
                          onClick={() => pin(p.step)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              pin(p.step);
                            }
                          }}
                        >
                          <title>{`Experiment ${e.number} · ${kind} ${p[kind]}`}</title>
                        </circle>
                      ))}
                    </g>
                  );
                }),
              )}
              {step !== null && step >= minX && step <= maxX && (
                <line
                  x1={x(step)}
                  x2={x(step)}
                  y1="28"
                  y2="248"
                  className="analytical-pin-line"
                />
              )}
            </svg>
          </Evidence>
        ) : (
          <EmptyState>No loss logged yet.</EmptyState>
        )}
        <div className="analytical-range">
          <Input
            label="From step"
            type="number"
            value={start}
            onChange={setStart}
          />
          <Input label="To step" type="number" value={end} onChange={setEnd} />
          <Button
            size="sm"
            disabled={
              start === "" ||
              end === "" ||
              !Number.isFinite(Number(start)) ||
              !Number.isFinite(Number(end)) ||
              Number(start) >= Number(end)
            }
            onClick={() => setRange([Number(start), Number(end)])}
          >
            Apply zoom
          </Button>
          <Button
            size="sm"
            disabled={!range}
            onClick={() => {
              setRange(null);
              setStart("");
              setEnd("");
            }}
          >
            Reset zoom
          </Button>
        </div>
        <p className="data-muted">
          {range ? `Local view: steps ${minX}–${maxX}` : "All logged steps"} ·
          Solid: training · Dashed: evaluation · Missing values remain gaps.
        </p>
      </Panel>
      <Panel
        className="analytical-step-inspector"
        title={
          step === null ? "Step evidence" : `Step ${step} / matching evidence`
        }
        actions={
          pinned !== null ? (
            <Button
              size="sm"
              onClick={() => {
                pin(null);
                preview(null);
              }}
            >
              Clear pin
            </Button>
          ) : undefined
        }
      >
        {step === null ? (
          <EmptyState>
            Select a point to pin its loss and checkpoint evidence.
          </EmptyState>
        ) : (
          <>
            <Badge>{pinned !== null ? "Pinned" : "Preview"}</Badge>
            <Evidence
              label="Selected loss step"
              data={{
                step,
                runs: visible.map((e) => ({
                  experiment: e.number,
                  sample: e.loss?.find((p) => p.step === step),
                })),
              }}
            >
              <SimpleTable
                label="Loss at selected step"
                rows={visible}
                getRowId={(e) => String(e.number)}
                columns={[
                  {
                    id: "run",
                    label: "Run",
                    value: (e) => e.number,
                    render: (e) => (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => onInspect(e.number)}
                      >
                        Experiment {e.number}
                      </Button>
                    ),
                  },
                  {
                    id: "train",
                    label: "Train",
                    value: (e) => e.loss?.find((p) => p.step === step)?.loss,
                    render: (e) => {
                      const sample = e.loss?.find((p) => p.step === step);
                      return valid(sample?.loss)
                        ? sample.loss.toFixed(4)
                        : "No sample";
                    },
                  },
                  {
                    id: "evaluation",
                    label: "Evaluation",
                    value: (e) =>
                      e.loss?.find((p) => p.step === step)?.eval_loss,
                    render: (e) => {
                      const sample = e.loss?.find((p) => p.step === step);
                      return valid(sample?.eval_loss)
                        ? sample.eval_loss.toFixed(4)
                        : "No sample";
                    },
                  },
                  {
                    id: "checkpoint",
                    label: "Checkpoint",
                    value: (e) => e.checkpoints?.includes(step) || false,
                    render: (e) =>
                      e.checkpoints?.includes(step)
                        ? `Step ${step}`
                        : "Not saved",
                  },
                ]}
              />
            </Evidence>
            <p className="data-muted">
              Exact logged steps only. Select an experiment to inspect its
              configuration and available checkpoint actions.
            </p>
          </>
        )}
        {!!steps.length && (
          <Select
            label="Inspect logged step"
            value={step === null ? "" : String(step)}
            onChange={(v: string) => pin(v === "" ? null : Number(v))}
            options={[
              { value: "", label: "Choose a step" },
              ...steps.map((n) => ({ value: String(n), label: `Step ${n}` })),
            ]}
          />
        )}
      </Panel>
    </div>
  );
}
