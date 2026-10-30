// Small geometry helpers.
import type { Bbox, LonLat } from "../api/types";

export type Bounds = [LonLat, LonLat]; // south-west, north-east

/** A coordinate rounded to 6 decimals (0.1 m): what a clicked stop keeps, so the request and
 * the link that reproduces it carry the same numbers. */
export function round6(value: number): number {
  return Math.round(value * 1e6) / 1e6;
}

export function bboxBounds([west, south, east, north]: Bbox): Bounds {
  return [
    [west, south],
    [east, north],
  ];
}

/** The bounds of a line, or null for an empty one. */
export function lineBounds(coordinates: LonLat[]): Bounds | null {
  if (coordinates.length === 0) return null;
  let [west, south] = coordinates[0];
  let [east, north] = coordinates[0];
  for (const [lon, lat] of coordinates) {
    west = Math.min(west, lon);
    east = Math.max(east, lon);
    south = Math.min(south, lat);
    north = Math.max(north, lat);
  }
  return [
    [west, south],
    [east, north],
  ];
}
