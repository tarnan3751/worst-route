import { describe, expect, it } from "vitest";
import { RECORDINGS, loadStream } from "../__fixtures__/stream";
import type { LonLat } from "../api/types";
import { spliceLine, type RouteLine } from "./routeLayer";

// A route of `nodes` nodes, every edge drawn with one shape point between its nodes: node k is
// point 2k, so node_index is [0, 2, 4, ...].
function line(nodes: number): RouteLine {
  const points: LonLat[] = [];
  for (let k = 0; k < 2 * nodes - 1; k++) points.push([k, 0]);
  return { points, index: Array.from({ length: nodes }, (_, k) => 2 * k) };
}

const nodes = (route: RouteLine) => route.index.map((k) => route.points[k]);

describe("spliceLine", () => {
  it("replaces nodes i..j by the detour's points and re-indexes the nodes after", () => {
    const route = line(5); // nodes at x = 0, 2, 4, 6, 8
    const coords: LonLat[] = [
      [2, 0],
      [2, 1],
      [3, 1],
      [4, 1],
      [6, 0],
    ];
    const out = spliceLine(route, 1, 3, coords, [0, 2, 4]);
    expect(out.points).toEqual([[0, 0], [1, 0], ...coords, [7, 0], [8, 0]]);
    expect(nodes(out)).toEqual([
      [0, 0],
      [2, 0],
      [3, 1],
      [6, 0],
      [8, 0],
    ]);
  });

  it("handles a detour shorter than what it replaces", () => {
    const route = line(5);
    const out = spliceLine(
      route,
      0,
      4,
      [
        [0, 0],
        [8, 0],
      ],
      [0, 1],
    );
    expect(out).toEqual({
      points: [
        [0, 0],
        [8, 0],
      ],
      index: [0, 1],
    });
  });

  it("handles a splice at either end of the route", () => {
    const route = line(4); // nodes at x = 0, 2, 4, 6
    const head = spliceLine(
      route,
      0,
      1,
      [
        [0, 0],
        [0, 5],
        [2, 0],
      ],
      [0, 1, 2],
    );
    expect(nodes(head)).toEqual([
      [0, 0],
      [0, 5],
      [2, 0],
      [4, 0],
      [6, 0],
    ]);
    const tail = spliceLine(
      route,
      2,
      3,
      [
        [4, 0],
        [5, 5],
        [6, 0],
      ],
      [0, 2],
    );
    expect(nodes(tail)).toEqual([
      [0, 0],
      [2, 0],
      [4, 0],
      [6, 0],
    ]);
    expect(tail.points.slice(-3)).toEqual([
      [4, 0],
      [5, 5],
      [6, 0],
    ]);
  });

  it("does not change the line it is given", () => {
    const route = line(3);
    const copy = structuredClone(route);
    spliceLine(
      route,
      0,
      2,
      [
        [0, 0],
        [9, 9],
        [4, 0],
      ],
      [0, 1, 2],
    );
    expect(route).toEqual(copy);
  });
});

describe.each(RECORDINGS)("replaying the recorded stream %s", (name) => {
  const fixture = loadStream(name);
  const { events, status, route } = fixture;
  const same = (a: LonLat, b: LonLat) => a[0] === b[0] && a[1] === b[1];

  // The client's algorithm (docs/reference/api.md, **Applying the stream**): a snapshot
  // replaces the line, each splice of a progress event replaces route nodes i..j.
  function replay(check?: (before: RouteLine, snapshot: RouteLine) => void): RouteLine {
    let current: RouteLine = { points: [], index: [] };
    for (const event of events) {
      if (event.event === "snapshot") {
        const next = { points: event.data.geometry.coordinates, index: event.data.node_index };
        if (current.index.length > 0) check?.(current, next);
        current = next;
      } else if (event.event === "progress") {
        for (const s of event.data.splices) {
          current = spliceLine(current, s.i, s.j, s.coords, s.node_index);
        }
      }
    }
    return current;
  }

  it("starts with a snapshot, splices it, and ends with done", () => {
    const names = events.map((event) => event.event);
    expect(names.slice(0, 3)).toEqual(["snapped", "baseline", "snapshot"]);
    expect(names).toContain("progress");
    expect(names.at(-1)).toBe("done");
    // The max-mode recording also has a snapshot after its 50th progress event.
    if (name === "portland-stream") expect(names.filter((n) => n === "snapshot")).toHaveLength(2);
  });

  it("agrees, node for node, with every snapshot that follows splices", () => {
    let checked = 0;
    replay((before, snapshot) => {
      expect(before.index.length).toBe(snapshot.index.length);
      before.index.forEach((at, k) => {
        if (!same(before.points[at], snapshot.points[snapshot.index[k]])) {
          throw new Error(`node ${k} differs from the snapshot's`);
        }
      });
      checked++;
    });
    expect(checked).toBe(events.filter((e) => e.event === "snapshot").length - 1);
  });

  it("ends with the stored route's nodes, every one where the final snapshot has it", () => {
    const final = replay();
    const snapshot = status.snapshot!;
    const count = route.legs.at(-1)!.node_range[1] + 1;
    expect(final.index.length).toBe(count);
    expect(snapshot.node_count).toBe(count);
    final.index.forEach((at, k) => {
      const want = snapshot.geometry.coordinates[snapshot.node_index[k]];
      if (!same(final.points[at], want))
        throw new Error(`node ${k}: ${final.points[at]} != ${want}`);
    });
    expect(snapshot.stop_idx).toEqual(route.stops.map((stop) => stop.route_index));
  });

  it("draws only points of the stored geometry, in order, with the same ends", () => {
    const drawn = replay().points.filter((p, k, all) => k === 0 || !same(p, all[k - 1]));
    const stored = route.geometry.coordinates;
    expect(drawn[0]).toEqual(stored[0]);
    expect(drawn.at(-1)).toEqual(stored.at(-1));
    let at = 0;
    for (const point of drawn) {
      while (at < stored.length && !same(stored[at], point)) at++;
      if (at === stored.length) throw new Error(`${point} is not on the stored route, in order`);
      at++;
    }
    expect(drawn.length).toBeLessThan(stored.length); // the snapshots are simplified
  });
});
