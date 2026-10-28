/** Version-one coordinates shared with tinystudio/lab/clocks.py. */
type ClockRow = Record<string, any>;
type Identifier = string | number | null;
export type ClockReference = {
  key: string;
  domain: string | null;
  units: string | null;
  origin: Identifier;
  session: Identifier;
  reset: Identifier;
  source_scope: unknown[] | null;
  qualified: boolean;
};
function scalar(value: unknown, field: string): Identifier {
  if (value == null) return null;
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return value;
  throw new Error(
    `${field} requires a nonempty string or finite numeric clock identifier`,
  );
}
function alias(row: ClockRow, fields: string[]): Identifier {
  const values = fields
    .filter((field) => row[field] != null)
    .map((field) => scalar(row[field], field));
  if (values.some((value) => value !== values[0]))
    throw new Error(`conflicting clock aliases: ${fields.join(", ")}`);
  return values[0] ?? null;
}
function label(value: unknown, field: string): string | null {
  if (value == null) return null;
  if (typeof value !== "string" || !value.trim())
    throw new Error(`${field} must be a nonempty clock label`);
  return value.trim();
}
export function clockReference(
  row: ClockRow,
  sourceScope: unknown = null,
): ClockReference {
  const domain = label(row.clock_domain, "clock_domain");
  const recordedUnits = label(row.clock_units, "clock_units");
  const units = recordedUnits === "unspecified" ? null : recordedUnits;
  const origin = alias(row, ["clock_origin", "clock_epoch", "time_origin"]);
  const session = alias(row, ["clock_session", "session_id"]);
  const reset = scalar(row.clock_reset_id, "clock_reset_id");
  if (row.clock_reset === true && reset === null)
    throw new Error(
      "clock_reset requires an explicit clock_reset_id for the new coordinate segment",
    );
  const qualified =
    domain !== null && units !== null && origin !== null && session !== null;
  const capture =
    row.capture_id ??
    row.acquisition_id ??
    row.source_artifact_id ??
    sourceScope;
  const scope = qualified
    ? null
    : [
        capture,
        row.episode_id ?? null,
        domain === null ? (row.device_id ?? null) : null,
      ];
  return {
    key: JSON.stringify([domain, units, origin, session, reset, scope]),
    domain,
    units,
    origin,
    session,
    reset,
    source_scope: scope,
    qualified,
  };
}
/** Events have no artifact scope; only complete coordinates establish comparability. */
export function eventAvailableForDecision(
  payload: ClockRow | null | undefined,
  point: ClockRow | undefined,
  episode: unknown,
): boolean {
  if (!payload || !point || payload.episode_id !== episode) return false;
  try {
    const eventClock = clockReference(payload);
    const pointClock = clockReference(point);
    const decision = point.decision_at ?? point.available_at;
    return (
      eventClock.qualified &&
      pointClock.qualified &&
      eventClock.key === pointClock.key &&
      typeof payload.time === "number" &&
      Number.isFinite(payload.time) &&
      typeof payload.available_at === "number" &&
      Number.isFinite(payload.available_at) &&
      typeof decision === "number" &&
      Number.isFinite(decision) &&
      payload.available_at <= decision
    );
  } catch {
    return false;
  }
}
