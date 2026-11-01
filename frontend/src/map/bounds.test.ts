// The box the map is held to: it holds every build served, the national one and Portland's; and
// holdToUs, the camera held to it.
import { describe, expect, it } from "vitest";
import type { Bbox, LonLat } from "../api/types";
import { US_BOUNDS, fitZoom, holdToUs } from "./bounds";

// West, south, east, north: the national build's nodes (us-t3) and Portland's bbox.
const NATIONAL: Bbox = [-124.72, 24.55, -66.97, 49.22];
const PORTLAND: Bbox = [-123.05, 45.25, -122.35, 45.72];

describe("US_BOUNDS", () => {
  const [[west, south], [east, north]] = US_BOUNDS;

  it("runs west to east and south to north", () => {
    expect(west).toBeLessThan(east);
    expect(south).toBeLessThan(north);
  });

  it.each([
    ["the national build", NATIONAL],
    ["Portland", PORTLAND],
  ])("holds %s", (_name, [w, s, e, n]) => {
    expect(west).toBeLessThan(w);
    expect(south).toBeLessThan(s);
    expect(east).toBeGreaterThan(e);
    expect(north).toBeGreaterThan(n);
  });
});

describe("fitZoom", () => {
  // The zooms MapLibre 6.16 gave in headless Chrome for the box filling a map of these sizes
  // (with the box as maxBounds, its height the limit at each).
  it.each([
    [1060, 860, 4.077],
    [1580, 1040, 4.351],
    [560, 560, 3.458],
  ])("fills %i × %i by its height at zoom %f", (width, height, zoom) => {
    // A viewport whose width is no limit: the box's height alone sets the zoom.
    expect(fitZoom(10 * width, height)).toBeCloseTo(zoom, 2);
  });

  it("is the lesser of the two axes' zooms", () => {
    expect(fitZoom(1060, 860)).toBeCloseTo(3.588, 2); // the width the limit: 62° across 1060 px
    expect(fitZoom(1060, 860)).toBeLessThan(fitZoom(10 * 1060, 860));
    expect(fitZoom(2000, 500)).toBeCloseTo(fitZoom(10 * 2000, 500), 9); // the height the limit
  });
});

// The view a camera shows in a `width` × `height` map, [west, south, east, north], as MapLibre
// draws it: Web Mercator, a world 512 · 2^zoom pixels across.
function viewOf(camera: { center: LonLat; zoom: number }, width: number, height: number): Bbox {
  const [lon, lat] = camera.center;
  const world = 512 * 2 ** camera.zoom;
  const y = (180 - (180 / Math.PI) * Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360))) / 360;
  const latOf = (v: number) =>
    (360 / Math.PI) * Math.atan(Math.exp(((180 - v * 360) * Math.PI) / 180)) - 90;
  const halfLon = (width / world) * 180;
  const halfY = height / world / 2;
  return [lon - halfLon, latOf(y + halfY), lon + halfLon, latOf(y - halfY)];
}

describe("holdToUs", () => {
  it("keeps a view smaller than the box inside it, on its edge from a centre outside", () => {
    const held = holdToUs([-157, 21], 8, 1060, 860);
    expect(held.zoom).toBe(8);
    expect(held.center[0]).toBeCloseTo(-127 + (1060 / (512 * 2 ** 8)) * 180, 9); // half a view in
    const [west, south] = viewOf(held, 1060, 860);
    expect(west).toBeCloseTo(-127, 9);
    expect(south).toBeCloseTo(23, 9);
    const [, , east, north] = viewOf(holdToUs([-40, 60], 8, 1060, 860), 1060, 860);
    expect(east).toBeCloseTo(-65, 9);
    expect(north).toBeCloseTo(51, 9);
  });

  it("keeps the whole box in view on an axis where the view is larger", () => {
    // 1060 × 860 at the fit zoom: the box's width fills the view, its height has room to spare.
    const tall = holdToUs([-157, 21], 1, 1060, 860);
    expect(tall.zoom).toBe(fitZoom(1060, 860));
    const [west, south, east, north] = viewOf(tall, 1060, 860);
    expect(west).toBeCloseTo(-127, 9);
    expect(east).toBeCloseTo(-65, 9);
    expect(north).toBeCloseTo(51, 9); // pushed from the south: the box's north edge at the view's
    expect(south).toBeLessThan(23);
    // 2000 × 500 at the fit zoom: the box's height fills the view, its width has room to spare.
    const wide = viewOf(holdToUs([-157, 21], 1, 2000, 500), 2000, 500);
    expect(wide[1]).toBeCloseTo(23, 9);
    expect(wide[3]).toBeCloseTo(51, 9);
    expect(wide[2]).toBeCloseTo(-65, 9); // pushed from the west: the box's east edge at the view's
    expect(wide[0]).toBeLessThan(-127);
  });

  it("raises a zoom below the fit zoom to it", () => {
    expect(holdToUs([-96, 38], 1, 1060, 860).zoom).toBe(fitZoom(1060, 860));
    expect(holdToUs([-96, 38], 1, 560, 560).zoom).toBe(fitZoom(560, 560));
  });

  it("returns a camera already held unchanged", () => {
    expect(holdToUs([-122.7, 45.5], 9.6, 1060, 860)).toEqual({ center: [-122.7, 45.5], zoom: 9.6 });
    expect(holdToUs([-96, 38], 5, 1060, 860)).toEqual({ center: [-96, 38], zoom: 5 });
  });
});
