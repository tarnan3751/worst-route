// A layer's filter in MapLibre's expression syntax, so a clause can be added to it (outside.ts adds
// `within`). Protomaps' layers come with filters in the deprecated legacy syntax
// (`["in", "kind", "river"]`), and MapLibre reads a filter with any expression-only node as an
// expression throughout, so a legacy filter ANDed with an expression would be misread.
// `asExpression` converts a legacy filter as the style spec's migration converter does
// (feature_filter/convert.ts, with the syntax test of feature_filter/index.ts; ported), so the
// layer keeps the features it had; a filter in expression syntax already is returned as it is. The
// loader converts a legacy filter with a private converter of its own, which differs from this one
// only for `!=` inside `any` or `none` on a feature without the property (true there, false here,
// after the type check), which none of the basemaps' filters has.
import type { ExpressionFilterSpecification, FilterSpecification } from "maplibre-gl";

type Syntax = "expression" | "legacy" | "neutral"; // neutral: reads the same either way
type Types = Record<string, string>; // the type a legacy comparison expects of each property
const COMPARISONS = new Set(["==", "!=", "<", "<=", ">", ">="]);

/** Which syntax MapLibre reads `filter` in (its style spec's classifyFilter). */
function syntaxOf(filter: unknown): Syntax {
  if (typeof filter === "boolean") return "neutral";
  if (!Array.isArray(filter) || filter.length === 0) return "legacy";
  const [op, a, b] = filter as unknown[];
  switch (op) {
    case "has":
      if (filter.length < 2 || a === "$id" || a === "$type") return "legacy";
      return filter.length === 2 ? "neutral" : "expression";
    case "in":
      return filter.length >= 3 && (typeof a !== "string" || Array.isArray(b))
        ? "expression"
        : "legacy";
    case "!in":
    case "!has":
    case "none":
      return "legacy";
    case "any":
    case "all": {
      let legacy = false;
      for (const child of filter.slice(1)) {
        const syntax = syntaxOf(child);
        if (syntax === "expression") return syntax;
        legacy ||= syntax === "legacy";
      }
      return legacy ? "legacy" : "neutral";
    }
    default:
      if (!COMPARISONS.has(op as string)) return "expression";
      return filter.length !== 3 || Array.isArray(a) || Array.isArray(b) ? "expression" : "legacy";
  }
}

function getter(property: unknown): unknown[] {
  if (property === "$type") return ["geometry-type"];
  return property === "$id" ? ["id"] : ["get", property];
}

/** The checks that each property in `types` is of its type, or true for none. */
function typeChecks(types: Types): unknown {
  const checks = Object.entries(types).map(([property, type]) => [
    "==",
    ["typeof", getter(property)],
    type,
  ]);
  if (checks.length === 0) return true;
  return checks.length === 1 ? checks[0] : ["all", ...checks];
}

function converted(filter: unknown, types: Types): unknown {
  if (syntaxOf(filter) !== "legacy") return filter;
  if (!filter) return true;
  const [op, ...args] = filter as unknown[];
  if (args.length === 0) return op !== "any";
  switch (op) {
    case "all": {
      const children = args.map((child) => converted(child, types));
      return children.length > 1 ? ["all", ...children] : children[0];
    }
    case "any":
      return [
        "any",
        ...args.map((child) => {
          const own: Types = {};
          const expression = converted(child, own);
          const checks = typeChecks(own);
          return checks === true ? expression : ["case", checks, expression, false];
        }),
      ];
    case "none":
      return ["!", converted(["any", ...args], {})];
    case "in":
    case "!in": {
      const [property, ...values] = args;
      const negate = op === "!in";
      if (values.length === 0) return negate;
      const get = getter(property);
      const type = typeof values[0];
      if ((type === "string" || type === "number") && values.every((v) => typeof v === type)) {
        const unique = [...values].sort().filter((v, k, sorted) => k === 0 || sorted[k - 1] !== v);
        return ["match", get, unique, !negate, negate];
      }
      const tests = values.map((v) => [negate ? "!=" : "==", get, v]);
      return [negate ? "all" : "any", ...tests];
    }
    case "has":
    case "!has": {
      const [property] = args;
      const has =
        property === "$type" ? true : property === "$id" ? ["!=", ["id"], null] : ["has", property];
      return op === "has" ? has : ["!", has];
    }
    default: {
      if (!COMPARISONS.has(op as string)) return true;
      const [property, value] = args;
      if (property === "$type") return [op, ["geometry-type"], value];
      if (value !== null) types[property as string] = typeof value;
      if (property !== "$id" && value === null && (op === "==" || op === "!=")) {
        return op === "=="
          ? ["all", ["has", property], ["==", getter(property), null]]
          : ["any", ["!", ["has", property]], ["!=", getter(property), null]];
      }
      return [op, getter(property), value];
    }
  }
}

/** `filter` in expression syntax: a legacy one converted as MapLibre converts it, an expression as
 * it is. */
export function asExpression(filter: FilterSpecification): ExpressionFilterSpecification {
  return converted(filter, {}) as ExpressionFilterSpecification;
}
