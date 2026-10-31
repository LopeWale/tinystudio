import { useEffect, useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import { Chart } from "./chart";
import { LabChoice } from "./inputs";

type Row = { id: string; record: Record<string, unknown> };
type Arm = "policy" | "baseline";
type Point = {
  id: string;
  step: number;
  arm: Arm;
  position: number;
  target: number;
};
type Group = {
  key: string;
  episode: number;
  seed: number;
  points: Point[];
  rejected: number;
};
const arms: Arm[] = ["policy", "baseline"];
const identity = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0;

// Identities are checked before observations so a corrupt duplicate cannot make
// another row at the same transition appear unambiguous.
export function controlTrajectoryGroups(rows: readonly Row[]) {
  const groups = new Map<string, Group>();
  const buckets = new Map<string, Row[]>();
  let rejected = 0;
  for (const row of rows) {
    const { episode, seed, step, arm, source } = row.record;
    if (
      !identity(episode) ||
      !identity(seed) ||
      !identity(step) ||
      step === Number.MAX_SAFE_INTEGER ||
      !arms.includes(arm as Arm) ||
      source !== "heldout_environment"
    ) {
      rejected++;
      continue;
    }
    const key = JSON.stringify([episode, seed]);
    if (!groups.has(key))
      groups.set(key, { key, episode, seed, points: [], rejected: 0 });
    const bucketKey = JSON.stringify([episode, seed, arm, step]);
    const bucket = buckets.get(bucketKey);
    if (bucket) bucket.push(row);
    else buckets.set(bucketKey, [row]);
  }
  for (const bucket of buckets.values()) {
    const row = bucket[0];
    const {
      episode,
      seed,
      step,
      arm,
      next_observation: observation,
    } = row.record;
    const group = groups.get(JSON.stringify([episode, seed]))!;
    if (
      bucket.length !== 1 ||
      !Array.isArray(observation) ||
      observation.length !== 3 ||
      !observation.every(
        (value) => typeof value === "number" && Number.isFinite(value),
      ) ||
      !Number.isFinite(Math.abs(observation[0] - observation[2]))
    ) {
      group.rejected += bucket.length;
      rejected += bucket.length;
      continue;
    }
    group.points.push({
      id: row.id,
      step: step as number,
      arm: arm as Arm,
      position: observation[0],
      target: observation[2],
    });
  }
  const ordered = [...groups.values()].sort(
    (a, b) => a.episode - b.episode || a.seed - b.seed,
  );
  for (const group of ordered)
    group.points.sort(
      (a, b) => a.step - b.step || arms.indexOf(a.arm) - arms.indexOf(b.arm),
    );
  return { groups: ordered, rejected };
}

export function ControlTrajectories({
  rows,
  selectedId,
  onSelect,
}: {
  rows: readonly Row[];
  selectedId?: string;
  onSelect: (id: string | undefined) => void;
}) {
  const model = useMemo(() => controlTrajectoryGroups(rows), [rows]);
  const [groupKey, setGroupKey] = useState("");
  const [metric, setMetric] = useState("position");
  const selectedGroup = model.groups.find((group) =>
    group.points.some((point) => point.id === selectedId),
  );
  const group =
    model.groups.find((entry) => entry.key === groupKey) ??
    selectedGroup ??
    model.groups[0];
  useEffect(() => {
    if (selectedGroup) setGroupKey(selectedGroup.key);
  }, [selectedId, selectedGroup?.key]);
  const points = group?.points ?? [];
  const paired = new Map<number, Set<Arm>>();
  for (const point of points) {
    if (!paired.has(point.step)) paired.set(point.step, new Set());
    paired.get(point.step)!.add(point.arm);
  }
  const incomplete = [...paired.values()].filter(
    (present) => present.size !== 2,
  ).length;
  const option = useMemo<EChartsOption>(() => {
    const series = arms.flatMap((arm) => {
      const armPoints = points.filter((point) => point.arm === arm);
      const data = (target: boolean) =>
        armPoints.flatMap((point, index) => {
          const value = target
            ? point.target
            : metric === "position"
              ? point.position
              : Math.abs(point.position - point.target);
          const current = {
            value: [point.step + 1, value],
            rowId: point.id,
            symbolSize: point.id === selectedId ? 11 : 6,
          };
          const previous = armPoints[index - 1];
          return previous && point.step - previous.step > 1
            ? [{ value: [previous.step + 2, null] }, current]
            : [current];
        });
      return [
        {
          name: arm === "policy" ? "Policy" : "Baseline",
          type: "line" as const,
          connectNulls: false,
          showSymbol: true,
          data: data(false),
        },
        ...(metric === "position"
          ? [
              {
                name: `${arm === "policy" ? "Policy" : "Baseline"} target`,
                type: "line" as const,
                connectNulls: false,
                showSymbol: true,
                lineStyle: { type: "dashed" as const },
                data: data(true),
              },
            ]
          : []),
      ];
    });
    return {
      animation: false,
      grid: { left: 64, right: 24, top: 48, bottom: 90 },
      legend: { top: 0 },
      tooltip: { trigger: "axis", renderMode: "richText" },
      xAxis: {
        type: "value",
        name: "Completed transition (step + 1)",
        nameLocation: "middle",
        nameGap: 28,
        minInterval: 1,
      },
      yAxis: {
        type: "value",
        name:
          metric === "position"
            ? "Position · unit not recorded"
            : "Absolute tracking error · unit not recorded",
        nameLocation: "middle",
        nameGap: 45,
      },
      dataZoom: [
        { id: "control-trajectory-inside", type: "inside", filterMode: "none" },
        {
          id: "control-trajectory-slider",
          type: "slider",
          bottom: 0,
          filterMode: "none",
        },
      ],
      series,
    };
  }, [points, metric, selectedId]);
  return (
    <section aria-label="Control trajectories" className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-3">
        <LabChoice
          label="Episode and seed"
          value={group?.key ?? ""}
          disabled={!model.groups.length}
          options={model.groups.map((entry) => ({
            id: entry.key,
            name: `Episode ${entry.episode} · seed ${entry.seed}`,
          }))}
          onChange={(key) => {
            setGroupKey(key);
            const next = model.groups.find((entry) => entry.key === key)
              ?.points[0];
            onSelect(next?.id);
          }}
        />
        <LabChoice
          label="Trajectory metric"
          value={metric}
          onChange={setMetric}
          options={[
            { id: "position", name: "Position and target" },
            { id: "error", name: "Absolute tracking error" },
          ]}
        />
      </div>
      <p className="text-caption-1-medium text-text-secondary">
        Shared plant state after each recorded transition. Lines break at
        missing steps; no interpolation. Arrow keys, Home and End select a raw
        record.
      </p>
      <p role="status" className="text-caption-1-medium text-text-secondary">
        {points.length
          ? `${paired.size} recorded transitions · ${incomplete} incomplete policy/baseline pairs`
          : "No valid trajectory points in this episode and seed."}
        {model.rejected > 0 &&
          ` · ${model.rejected} invalid or ambiguous records excluded`}
      </p>
      {points.length > 0 && (
        <Chart
          key={`${group?.key}:${metric}`}
          label="Control trajectory chart"
          height={360}
          option={option}
          onPoint={(data) => {
            if (
              data &&
              typeof data === "object" &&
              "rowId" in data &&
              points.some((point) => point.id === data.rowId)
            )
              onSelect(data.rowId as string);
          }}
          onKeyDown={(event) => {
            if (
              ![
                "ArrowLeft",
                "ArrowRight",
                "ArrowUp",
                "ArrowDown",
                "Home",
                "End",
              ].includes(event.key)
            )
              return;
            event.preventDefault();
            const current = points.findIndex(
              (point) => point.id === selectedId,
            );
            const index =
              event.key === "Home"
                ? 0
                : event.key === "End"
                  ? points.length - 1
                  : current < 0
                    ? 0
                    : Math.max(
                        0,
                        Math.min(
                          points.length - 1,
                          current +
                            (["ArrowLeft", "ArrowUp"].includes(event.key)
                              ? -1
                              : 1),
                        ),
                      );
            onSelect(points[index].id);
          }}
        />
      )}
    </section>
  );
}
