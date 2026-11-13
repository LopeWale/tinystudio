import { expect, it } from "vitest";
import { scientificJson } from "./scientific-json";

it("preserves signed zero and large finite floats without emitting unsafe integer tokens", () => {
  const source = {
    negative_zero: -0,
    large: 1e20,
    nested: [1e16, -1e100, 1.25e-30],
    text: 'a "quoted" numeric string: 9007199254740993',
  };
  const encoded = scientificJson.stringify(source, 2);
  expect(encoded).toContain('"negative_zero": -0.0');
  expect(encoded).toContain('"large": 1e+20');
  expect(encoded).toContain("1e+16");
  const decoded = scientificJson.parse(encoded) as typeof source;
  expect(Object.is(decoded.negative_zero, -0)).toBe(true);
  expect(decoded).toEqual(source);
});
it("admits source float tokens across the finite range and preserves negative zero", () => {
  const value = scientificJson.parse(
    '{"large":1e20,"decimal":100000000000000000000.0,"negative_zero":-0.0,"max":1.7976931348623157e308}',
  ) as Record<string, number>;
  expect(value.large).toBe(1e20);
  expect(value.decimal).toBe(1e20);
  expect(Object.is(value.negative_zero, -0)).toBe(true);
  expect(value.max).toBe(Number.MAX_VALUE);
});
it.each([
  "9007199254740992",
  "-9007199254740993",
  '{"count":100000000000000000000}',
])(
  "rejects an unsafe source integer token %s instead of silently rounding it",
  (source) => {
    expect(() => scientificJson.parse(source)).toThrow(/decimal string|float/i);
  },
);
it("allows safe integers and ignores number-like text inside escaped JSON strings", () => {
  const source = {
    count: Number.MAX_SAFE_INTEGER,
    text: 'escaped quote: " then 9007199254740993, slash \\ and newline\n',
  };
  expect(scientificJson.parse(JSON.stringify(source))).toEqual(source);
});
it.each(["1e999", '{"value":-1e999}'])(
  "rejects nonfinite source numbers %s",
  (source) => {
    expect(() => scientificJson.parse(source)).toThrow(/finite/i);
  },
);
it.each([NaN, Infinity, -Infinity])(
  "refuses to serialize nonfinite values %s",
  (value) => {
    expect(() => scientificJson.stringify({ value })).toThrow(/finite/i);
  },
);
it("formats ordinary finite JSON with the standard indentation and escaping", () => {
  const value = {
    plain: [1, true, null, { nested: "value\nnext" }],
    omitted: undefined,
    empty: {},
    nil: [],
  };
  expect(scientificJson.stringify(value, 2)).toBe(
    JSON.stringify(value, null, 2),
  );
  const cyclic: any = {};
  cyclic.self = cyclic;
  expect(() => scientificJson.stringify(cyclic)).toThrow();
});
