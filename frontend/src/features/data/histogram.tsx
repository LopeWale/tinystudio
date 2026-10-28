import { useEffect, useId, useState } from "react";
import { Evidence } from "../../components/studio/primitives";
import "./histogram.css";

type HistogramProps = {
  label: string;
  counts: number[];
  bins: number[];
  /** Quality aggregates count integers in half-open [lower, upper) bins. */
  integerValues?: boolean;
  unit?: string;
  onSelect?: (index: number) => void;
  selected?: number | null;
  data?: unknown;
};
const number = (value: number) =>
  value.toLocaleString("en-US", { maximumFractionDigits: 6 });
const observations = (count: number) =>
  `${number(count)} observation${count === 1 ? "" : "s"}`;

export function Histogram({
  label,
  counts,
  bins,
  integerValues = false,
  unit = "value",
  onSelect,
  selected,
  data,
}: HistogramProps) {
  const descriptionId = useId();
  const [active, setActive] = useState<number | null>(null);
  const [plot, setPlot] = useState<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(420);
  useEffect(() => {
    if (!plot) return;
    const measure = (next: number) => {
      if (next > 0) setWidth(Math.max(200, next));
    };
    measure(plot.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) =>
      measure(entry.contentRect.width),
    );
    observer.observe(plot);
    return () => observer.disconnect();
  }, [plot]);
  if (!counts?.length && !bins?.length)
    return <p className="histogram-empty">No observations available.</p>;
  if (
    !Array.isArray(counts) ||
    !Array.isArray(bins) ||
    bins.length !== counts.length + 1 ||
    !counts.length ||
    counts.some((n) => !Number.isSafeInteger(n) || n < 0) ||
    bins.some(
      (n, i) =>
        !Number.isFinite(n) ||
        (i > 0 && n <= bins[i - 1]) ||
        (integerValues && !Number.isSafeInteger(n)),
    )
  ) {
    return (
      <p className="histogram-empty">
        Histogram unavailable: bin ranges and counts do not match.
      </p>
    );
  }
  const total = counts.reduce((sum, count) => sum + count, 0);
  const maximum = Math.max(1, ...counts);
  const top = maximum <= 4 ? maximum : Math.ceil(maximum / 4) * 4;
  const ticks = Array.from(
    { length: Math.min(4, top) + 1 },
    (_, i) => (i * top) / Math.min(4, top),
  );
  const interval = (index: number) => {
    const lower = bins[index],
      upper = bins[index + 1];
    return integerValues
      ? lower === upper - 1
        ? number(lower)
        : `${number(lower)}–${number(upper - 1)}`
      : `${number(lower)}–${number(upper)}`;
  };
  const membership = (index: number) =>
    integerValues
      ? interval(index)
      : `${number(bins[index])} ≤ value ${index === counts.length - 1 ? "≤" : "<"} ${number(bins[index + 1])}`;
  const description = (index: number) =>
    `${membership(index)}: ${observations(counts[index])} (${total ? ((counts[index] / total) * 100).toFixed(1) : "0.0"}%)`;
  const left = 38,
    right = width - 10,
    baseline = 148,
    plotHeight = 112;
  const x = (value: number) =>
    left +
    ((value - bins[0]) / (bins[bins.length - 1] - bins[0])) * (right - left);
  const labelSpacing = Math.max(
    ...counts.map((_, i) => interval(i).length * 5.5 + 10),
  );
  const displayed =
    active !== null && active < counts.length
      ? active
      : selected != null && selected < counts.length && selected >= 0
        ? selected
        : null;
  const center = (index: number) => (x(bins[index]) + x(bins[index + 1])) / 2;
  // Label the evidence first. Empty intervals fill remaining space, so a sparse
  // axis never spends a label on an empty neighbor instead of its visible bar.
  const populated = counts
    .map((_, i) => i)
    .filter((i) => counts[i] > 0)
    .sort((a, b) => counts[b] - counts[a] || a - b);
  const candidates = [
    ...(displayed === null ? [] : [displayed]),
    ...populated,
    0,
    counts.length - 1,
    ...counts.map((_, i) => i),
  ];
  const visibleLabels = new Set<number>();
  for (const index of candidates) {
    if (
      [...visibleLabels].every(
        (other) => Math.abs(center(index) - center(other)) >= labelSpacing,
      )
    ) {
      visibleLabels.add(index);
    }
  }
  return (
    <Evidence
      as="figure"
      label={label}
      data={data ?? { counts, bins }}
      className="histogram-figure"
    >
      <div className="histogram-axis-heading">
        <span>Observations</span>
        <span>{observations(total)}</span>
      </div>
      <div ref={setPlot} className="histogram-scroll">
        <svg
          viewBox={`0 0 ${width} 181`}
          role={onSelect ? "group" : "img"}
          aria-label={label}
          aria-description={`${observations(total)} by ${unit}`}
          aria-describedby={descriptionId}
          className="histogram-chart"
        >
          {ticks.map((tick) => (
            <g key={tick} className="histogram-grid">
              <line
                x1={left}
                x2={right}
                y1={baseline - (tick / top) * plotHeight}
                y2={baseline - (tick / top) * plotHeight}
              />
              <text
                x={left - 8}
                y={baseline - (tick / top) * plotHeight + 4}
                textAnchor="end"
              >
                {number(tick)}
              </text>
            </g>
          ))}
          {counts.map((count, i) => {
            const start = x(bins[i]),
              end = x(bins[i + 1]),
              center = (start + end) / 2;
            const barWidth = Math.min(64, Math.max(1, end - start - 5));
            const height = (count / top) * plotHeight;
            return (
              <g
                key={`${bins[i]}:${bins[i + 1]}`}
                role={onSelect ? "button" : undefined}
                tabIndex={0}
                aria-label={description(i)}
                aria-pressed={onSelect ? selected === i : undefined}
                className={`histogram-bin${selected === i ? " is-selected" : ""}`}
                onMouseEnter={() => setActive(i)}
                onMouseLeave={() => setActive(null)}
                onFocus={() => setActive(i)}
                onBlur={() => setActive(null)}
                onClick={() => onSelect?.(i)}
                onKeyDown={(event) => {
                  if (
                    onSelect &&
                    (event.key === "Enter" || event.key === " ")
                  ) {
                    event.preventDefault();
                    onSelect(i);
                  }
                }}
              >
                <title>{description(i)}</title>
                <rect
                  className="histogram-hit-area"
                  x={start}
                  y="18"
                  width={end - start}
                  height="154"
                />
                {count > 0 && (
                  <rect
                    className="histogram-bar"
                    x={center - barWidth / 2}
                    y={baseline - height}
                    width={barWidth}
                    height={height}
                    rx="2"
                  />
                )}
                {count > 0 && (
                  <text
                    className="histogram-bar-count"
                    x={center}
                    y={baseline - height - 7}
                    textAnchor="middle"
                  >
                    {number(count)}
                  </text>
                )}
                {visibleLabels.has(i) && (
                  <text
                    className="histogram-bin-label"
                    x={center}
                    y="168"
                    textAnchor="middle"
                  >
                    {interval(i)}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </div>
      <figcaption id={descriptionId} className="histogram-caption">
        <span className="histogram-unit">{unit}</span>
        <span role="status" className="histogram-readout">
          {displayed === null
            ? "Hover or focus a bin for its count and share."
            : description(displayed)}
        </span>
        <span className="histogram-note">
          Bin ranges include empty intervals; endpoints are not observed minima
          or maxima.
          {!integerValues &&
            " Upper bounds are excluded except in the final bin."}
        </span>
      </figcaption>
      <details className="histogram-data">
        <summary>View bin counts</summary>
        <div className="histogram-data-scroll">
          <table aria-label={`${label} bin counts`}>
            <thead>
              <tr>
                <th scope="col">{unit} interval</th>
                <th scope="col">Observations</th>
              </tr>
            </thead>
            <tbody>
              {counts.map((count, i) => (
                <tr key={i}>
                  <th scope="row">
                    {integerValues ? interval(i) : membership(i)}
                  </th>
                  <td>{number(count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </Evidence>
  );
}
