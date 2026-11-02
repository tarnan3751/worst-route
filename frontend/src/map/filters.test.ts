// A layer's filter in expression syntax (map/filters.ts): each legacy form converted as the style
// spec's migration converter (feature_filter/convert.ts) converts it, an expression left as it is,
// and no legacy node left in any of Protomaps' flavors' filters once converted, so a clause can be
// ANDed onto each.
import { layers, namedFlavor } from "@protomaps/basemaps";
import type { FilterSpecification } from "maplibre-gl";
import { describe, expect, it } from "vitest";
import { asExpression } from "./filters";

const convert = (filter: unknown): unknown => asExpression(filter as FilterSpecification);

/** Whether `node` or a node in it is in the legacy syntax only, which MapLibre would misread
 * inside an expression. */
function hasLegacy(node: unknown): boolean {
  if (!Array.isArray(node) || node.length === 0) return false;
  const [op, a, b] = node as unknown[];
  if (op === "!in" || op === "!has" || op === "none") return true;
  if (["==", "!=", "<", "<=", ">", ">="].includes(op as string)) {
    if (node.length === 3 && !Array.isArray(a) && !Array.isArray(b)) return true;
  }
  if (op === "in" && !(node.length >= 3 && (typeof a !== "string" || Array.isArray(b)))) {
    return true;
  }
  return node.slice(1).some(hasLegacy);
}

describe("asExpression", () => {
  it("converts legacy comparisons on a property, the geometry's type and the id", () => {
    expect(convert(["==", "kind", "rail"])).toEqual(["==", ["get", "kind"], "rail"]);
    expect(convert(["!=", "kind_detail", "service"])).toEqual([
      "!=",
      ["get", "kind_detail"],
      "service",
    ]);
    expect(convert(["<=", "kind_detail", 2])).toEqual(["<=", ["get", "kind_detail"], 2]);
    expect(convert([">", "kind_detail", 2])).toEqual([">", ["get", "kind_detail"], 2]);
    expect(convert(["==", "$type", "Polygon"])).toEqual(["==", ["geometry-type"], "Polygon"]);
    expect(convert(["!=", "$id", 3])).toEqual(["!=", ["id"], 3]);
    expect(convert(["==", "name", null])).toEqual([
      "all",
      ["has", "name"],
      ["==", ["get", "name"], null],
    ]);
    expect(convert(["!=", "name", null])).toEqual([
      "any",
      ["!", ["has", "name"]],
      ["!=", ["get", "name"], null],
    ]);
  });

  it("converts in and !in to a match over the values, sorted, each once", () => {
    expect(convert(["in", "kind", "river", "stream"])).toEqual([
      "match",
      ["get", "kind"],
      ["river", "stream"],
      true,
      false,
    ]);
    expect(convert(["in", "kind", "sea", "bay", "sea"])).toEqual([
      "match",
      ["get", "kind"],
      ["bay", "sea"],
      true,
      false,
    ]);
    expect(convert(["!in", "kind", "pier"])).toEqual([
      "match",
      ["get", "kind"],
      ["pier"],
      false,
      true,
    ]);
    expect(convert(["in", "x", 1, "a"])).toEqual([
      "any",
      ["==", ["get", "x"], 1],
      ["==", ["get", "x"], "a"],
    ]);
    expect(convert(["!in", "x", 1, "a"])).toEqual([
      "all",
      ["!=", ["get", "x"], 1],
      ["!=", ["get", "x"], "a"],
    ]);
    expect(convert(["in", "x"])).toBe(false);
    expect(convert(["!in", "x"])).toBe(true);
  });

  it("converts has, !has, all, any (each term type checked first) and none", () => {
    expect(convert(["!has", "is_bridge"])).toEqual(["!", ["has", "is_bridge"]]);
    expect(convert(["!has", "$id"])).toEqual(["!", ["!=", ["id"], null]]);
    expect(convert(["all", ["has", "is_tunnel"], ["in", "kind", "other", "path"]])).toEqual([
      "all",
      ["has", "is_tunnel"],
      ["match", ["get", "kind"], ["other", "path"], true, false],
    ]);
    expect(convert(["all", ["!has", "x"]])).toEqual(["!", ["has", "x"]]);
    expect(convert(["any", [">", "y", 0], ["has", "z"]])).toEqual([
      "any",
      ["case", ["==", ["typeof", ["get", "y"]], "number"], [">", ["get", "y"], 0], false],
      ["has", "z"],
    ]);
    expect(convert(["none", ["==", "a", "b"]])).toEqual([
      "!",
      [
        "any",
        ["case", ["==", ["typeof", ["get", "a"]], "string"], ["==", ["get", "a"], "b"], false],
      ],
    ]);
  });

  it("leaves a filter in expression syntax, or one that reads the same in both, as it is", () => {
    for (const filter of [
      ["==", ["get", "oneway"], "yes"],
      ["all", ["has", "is_tunnel"], ["==", ["get", "kind"], "highway"], ["!", ["has", "is_link"]]],
      ["in", ["get", "kind"], ["literal", ["highway", "major_road"]]],
      ["has", "is_link"],
      true,
    ]) {
      expect(convert(filter)).toBe(filter);
    }
  });

  it("leaves no legacy node in any of Protomaps' flavors' filters", () => {
    for (const flavor of ["grayscale", "light", "dark"]) {
      const filters = layers("protomaps", namedFlavor(flavor), { lang: "en" }).map(
        (layer) => [layer.id, (layer as { filter?: unknown }).filter] as const,
      );
      const legacy = filters.filter(([, filter]) => hasLegacy(filter));
      expect(legacy.length, flavor).toBeGreaterThan(30); // what the conversion is for
      for (const [id, filter] of legacy) expect(hasLegacy(convert(filter)), id).toBe(false);
    }
  });
});
