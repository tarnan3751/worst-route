// The outline the land outside the contiguous US is drawn flat around: public/conus.json, as
// tools/desktop_data.py conus writes it, a GeoJSON MultiPolygon within the contiguous US's box,
// small enough to fetch at every start.
import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const root = fileURLToPath(new URL("../..", import.meta.url));
const OUTLINE = join(root, "public/conus.json");
const [WEST, SOUTH, EAST, NORTH] = [-125, 24.4, -66.8, 49.5];

describe("the contiguous US's outline", () => {
  it("is a GeoJSON MultiPolygon within the contiguous US's box, under 100 kB", () => {
    expect(statSync(OUTLINE).size).toBeLessThan(100_000);
    const outline = JSON.parse(readFileSync(OUTLINE, "utf8")) as {
      type: string;
      coordinates: unknown;
    };
    expect(outline.type).toBe("MultiPolygon");
    const polygons = outline.coordinates as number[][][][];
    expect(polygons.length).toBeGreaterThan(0);
    for (const polygon of polygons) {
      expect(polygon.length).toBeGreaterThan(0);
      for (const ring of polygon) {
        expect(ring.length).toBeGreaterThanOrEqual(4);
        expect(ring[ring.length - 1]).toEqual(ring[0]); // closed
        for (const position of ring) {
          expect(position).toHaveLength(2);
          const [lon, lat] = position;
          expect(lon).toBeGreaterThanOrEqual(WEST);
          expect(lon).toBeLessThanOrEqual(EAST);
          expect(lat).toBeGreaterThanOrEqual(SOUTH);
          expect(lat).toBeLessThanOrEqual(NORTH);
        }
      }
    }
  });
});
