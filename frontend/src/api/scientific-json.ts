export interface JsonCodec {
  parse(text: string): unknown;
  stringify(value: unknown, space?: number): string;
}
/** Study JSON admits finite floats, while plain integer tokens must be exact. */
function parse(text: string): unknown {
  const result: unknown = JSON.parse(text);
  const token = /-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/y;
  for (let index = 0; index < text.length; index++) {
    if (text[index] === '"') {
      for (index++; index < text.length; index++) {
        if (text[index] === "\\") index++;
        else if (text[index] === '"') break;
      }
    } else if (text[index] === "-" || /\d/.test(text[index])) {
      token.lastIndex = index;
      const source = token.exec(text)![0];
      const value = Number(source);
      if (!Number.isFinite(value))
        throw new Error("Scientific JSON requires finite numbers.");
      if (!/[.eE]/.test(source) && !Number.isSafeInteger(value))
        throw new Error(
          "Integer exceeds exact JSON representation; use a decimal string for exact integers, or a float token for approximate values.",
        );
      index += source.length - 1;
    }
  }
  return result;
}

/** Native JSON loses signed zero and spells some supported floats as unsafe integers. */
function stringify(input: unknown, space = 0): string {
  const indent = " ".repeat(Math.min(10, Math.max(0, Math.floor(space))));
  const ancestors = new Set<object>();
  const encode = (value: unknown, depth: number): string | undefined => {
    if (value === null) return "null";
    if (typeof value === "string" || typeof value === "boolean")
      return JSON.stringify(value);
    if (typeof value === "number") {
      if (!Number.isFinite(value))
        throw new Error("Scientific JSON requires finite numbers.");
      if (Object.is(value, -0)) return "-0.0";
      return Number.isInteger(value) && !Number.isSafeInteger(value)
        ? value.toExponential()
        : String(value);
    }
    if (typeof value === "bigint")
      throw new TypeError(
        "Use a decimal string for exact integers outside JSON's safe range.",
      );
    if (typeof value !== "object") return undefined;
    if ("toJSON" in value && typeof value.toJSON === "function") {
      const converted: unknown = value.toJSON();
      if (converted !== value) return encode(converted, depth);
    }
    if (ancestors.has(value))
      throw new TypeError("Scientific JSON cannot contain cyclic objects.");
    ancestors.add(value);
    try {
      const array = Array.isArray(value);
      const parts = array
        ? Array.from(
            { length: value.length },
            (_, index) => encode(value[index], depth + 1) ?? "null",
          )
        : Object.keys(value).flatMap((key) => {
            const encoded = encode(
              (value as Record<string, unknown>)[key],
              depth + 1,
            );
            return encoded === undefined
              ? []
              : [`${JSON.stringify(key)}:${indent ? " " : ""}${encoded}`];
          });
      const [start, end] = array ? ["[", "]"] : ["{", "}"];
      return !parts.length
        ? start + end
        : indent
          ? `${start}\n${indent.repeat(depth + 1)}${parts.join(`,\n${indent.repeat(depth + 1)}`)}\n${indent.repeat(depth)}${end}`
          : `${start}${parts.join(",")}${end}`;
    } finally {
      ancestors.delete(value);
    }
  };
  const result = encode(input, 0);
  if (result === undefined)
    throw new TypeError("Scientific JSON requires a JSON value.");
  return result;
}
export const scientificJson: JsonCodec = { parse, stringify };
