import { describe, expect, it } from "vitest";
import fixture from "./clock-contract.v1.json";
import { clockReference, eventAvailableForDecision } from "./clock";

describe("shared version-one clock contract", () => {
  for (const example of fixture.cases) {
    it(example.id, () => {
      if ("invalid" in example && example.invalid) {
        expect(() => clockReference(example.row, example.scope)).toThrow(
          "conflicting clock",
        );
      } else {
        const reference = clockReference(example.row, example.scope);
        expect(reference.qualified).toBe(
          "qualified" in example ? example.qualified : false,
        );
      }
    });
  }
  // Resolve independently of test ordering, including shuffled runs.
  const key = (id: string) => {
    const example = fixture.cases.find((item) => item.id === id)!;
    return clockReference(example.row, example.scope).key;
  };
  it.each(fixture.compatible_pairs)("matches %s and %s", (left, right) =>
    expect(key(left)).toBe(key(right)),
  );
  it.each(fixture.incompatible_pairs)("separates %s and %s", (left, right) =>
    expect(key(left)).not.toBe(key(right)),
  );
  it("isolates incomplete episodes and missing domains by device", () => {
    expect(clockReference({ episode_id: "A" }, "capture").key).not.toBe(
      clockReference({ episode_id: "B" }, "capture").key,
    );
    expect(clockReference({ device_id: "A" }, "capture").key).not.toBe(
      clockReference({ device_id: "B" }, "capture").key,
    );
  });
  it.each([false, "", Infinity, NaN, {}, []])(
    "rejects invalid origin %s",
    (clock_origin) => {
      expect(() => clockReference({ clock_origin })).toThrow();
    },
  );
  it("rejects conflicting session types and unqualified reset flags", () => {
    expect(() => clockReference({ clock_session: 0, session_id: "0" })).toThrow(
      "conflicting clock",
    );
    expect(() => clockReference({ clock_reset: true })).toThrow(
      "clock_reset_id",
    );
  });
});

const point = {
  episode_id: "E",
  clock_domain: "reference",
  clock_units: "s",
  clock_origin: 0,
  clock_session: "S",
  clock_reset_id: 0,
  decision_at: 3,
};
const event = {
  episode_id: "E",
  clock_domain: "reference",
  clock_units: "s",
  time_origin: 0,
  session_id: "S",
  clock_reset_id: 0,
  time: 1,
  available_at: 2,
};
describe("decision events", () => {
  it("compares aliases and numeric zero using the canonical coordinate", () => {
    expect(eventAvailableForDecision(event, point, "E")).toBe(true);
  });
  it.each([
    { clock_reset_id: 1 },
    { session_id: "other" },
    { clock_epoch: 1 },
    { clock_units: "ms" },
    { episode_id: "other" },
    { available_at: 4 },
    { available_at: NaN },
    { time: Infinity },
    { session_id: undefined },
  ])(
    "rejects unrelated, contradictory, incomplete or unavailable events %s",
    (change) => {
      expect(
        eventAvailableForDecision({ ...event, ...change }, point, "E"),
      ).toBe(false);
    },
  );
  it("does not qualify two equally incomplete clocks", () => {
    expect(
      eventAvailableForDecision(
        { ...event, session_id: undefined },
        { ...point, clock_session: undefined },
        "E",
      ),
    ).toBe(false);
  });
});
