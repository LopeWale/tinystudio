import { useState, type KeyboardEvent } from "react";
import { Evidence } from "../../components/studio/primitives";
import { number, percent } from "./selection";

interface Point {
  neuron: number;
  x: number | null;
  y: number | null;
  [key: string]: any;
}
export function NeuronMap({
  rows,
  selected,
  onSelect,
  xLabel,
  yLabel,
  xFormat = number,
  yFormat = number,
  label,
}: {
  rows: Point[];
  selected: number | null;
  onSelect: (neuron: number) => void;
  xLabel: string;
  yLabel: string;
  xFormat?: (v: number) => string;
  yFormat?: (v: number) => string;
  label: string;
}) {
  // Retain only interaction identity. Measurements always come from this render's
  // finite rows, so layer/loading/filter changes cannot publish an older row.
  const [inspection, setInspection] = useState<{
    neuron: number;
    selection: number | null;
  } | null>(null);
  const points = rows.filter(
    (row) =>
      row.x != null &&
      Number.isFinite(row.x) &&
      row.y != null &&
      Number.isFinite(row.y),
  );
  const xs = points.map((p) => p.x as number),
    ys = points.map((p) => p.y as number);
  const minX = Math.min(0, ...xs),
    maxX = Math.max(1e-6, ...xs),
    minY = Math.min(0, ...ys),
    maxY = Math.max(1e-6, ...ys);
  const sx = (x: number) => 54 + ((x - minX) / (maxX - minX)) * 450,
    sy = (y: number) => 200 - ((y - minY) / (maxY - minY)) * 170;
  const active = points.find((p) => p.neuron === selected) ?? points[0];
  const keyboard = (event: KeyboardEvent<Element>, index: number) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(points[index].neuron);
      return;
    }
    const step = ["ArrowRight", "ArrowDown"].includes(event.key)
      ? 1
      : ["ArrowLeft", "ArrowUp"].includes(event.key)
        ? -1
        : 0;
    if (!step && !["Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? points.length - 1
          : (index + step + points.length) % points.length;
    const sibling = (
      event.currentTarget as SVGCircleElement
    ).ownerSVGElement?.querySelector<SVGCircleElement>(
      `[data-neuron="${points[next].neuron}"]`,
    );
    sibling?.focus();
    setInspection({ neuron: points[next].neuron, selection: selected });
  };
  const inspect =
    (inspection && inspection.selection === selected
      ? points.find((point) => point.neuron === inspection.neuron)
      : undefined) ?? active;
  return (
    <Evidence
      label={label}
      data={{ xLabel, yLabel, rows, omitted: rows.length - points.length }}
      className="neuron-map"
    >
      <svg
        viewBox="0 0 540 255"
        role="group"
        aria-label={`${label}. Arrow keys move between neurons; Enter selects.`}
      >
        {[0, 0.25, 0.5, 0.75, 1].map((r) => (
          <g key={r}>
            <line
              x1="54"
              x2="504"
              y1={30 + r * 170}
              y2={30 + r * 170}
              className="plot-grid"
            />
            <text x="44" y={34 + r * 170} textAnchor="end">
              {yFormat(maxY - r * (maxY - minY))}
            </text>
            <text x={54 + r * 450} y="220" textAnchor="middle">
              {xFormat(minX + r * (maxX - minX))}
            </text>
          </g>
        ))}
        <text x="279" y="247" textAnchor="middle">
          {xLabel}
        </text>
        <text x="54" y="17">
          {yLabel}
        </text>
        {points.map((point, index) => (
          <Evidence
            as="circle"
            label="Neuron map point"
            data={point}
            key={point.neuron}
            data-neuron={point.neuron}
            cx={sx(point.x as number)}
            cy={sy(point.y as number)}
            r={point.neuron === selected ? 5.5 : 3.2}
            className={`neuron-dot ${point.neuron === selected ? "selected" : ""}`}
            role="button"
            tabIndex={point.neuron === active?.neuron ? 0 : -1}
            aria-label={`Explore neuron ${point.neuron}: ${xLabel} ${xFormat(point.x as number)}, ${yLabel} ${yFormat(point.y as number)}`}
            onMouseEnter={() =>
              setInspection({ neuron: point.neuron, selection: selected })
            }
            onFocus={() =>
              setInspection({ neuron: point.neuron, selection: selected })
            }
            onMouseLeave={() => setInspection(null)}
            onClick={() => onSelect(point.neuron)}
            onKeyDown={(e) => keyboard(e, index)}
          />
        ))}
      </svg>
      <div className="plot-caption">
        <span>
          {points.length.toLocaleString()} plotted
          {rows.length > points.length
            ? ` · ${rows.length - points.length} undefined coordinates omitted`
            : ""}
        </span>
        <span aria-live="polite">
          {inspect
            ? `N${inspect.neuron} · ${xFormat(inspect.x as number)} · ${yFormat(inspect.y as number)}`
            : "No finite values"}
        </span>
      </div>
    </Evidence>
  );
}

export function LabelBars({
  pairs,
  denominator,
  onSelect,
  selected = "",
}: {
  pairs: [string, number][];
  denominator: number;
  onSelect?: (label: string) => void;
  selected?: string;
}) {
  if (!pairs.length)
    return <p className="muted">No labels in this retained pole.</p>;
  return (
    <div className="label-bars">
      <p className="muted">
        Share of {denominator} distinct retained datapoint
        {denominator === 1 ? "" : "s"}. A datapoint can carry several labels.
      </p>
      {pairs.map(([label, share]) => (
        <Evidence
          as="div"
          key={label}
          label="Neuron label share"
          data={{ label, share, denominator }}
        >
          <button
            type="button"
            onClick={() => onSelect?.(selected === label ? "" : label)}
            aria-pressed={onSelect ? selected === label : undefined}
            disabled={!onSelect}
            className="label-bar"
          >
            <span>{label}</span>
            <span className="bar-track">
              <i style={{ width: percent(Math.max(0, Math.min(1, share))) }} />
            </span>
            <b>{percent(share)}</b>
          </button>
        </Evidence>
      ))}
    </div>
  );
}
