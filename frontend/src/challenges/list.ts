// The challenges (docs/reference/frontend.md, **Challenges**): a fixed list over the modes the API
// plans, each a region, a trip (or any trip in the region: `stops` null), the settings it fixes
// and a goal measured on a finished route (progress.ts grades it; panel.ts sets one up). Every
// stop and threshold below was picked from runs measured as the challenge sets it up, the rest at
// the panel's defaults (a 20 s budget, the automatic tier and leash), on the version 8 builds
// (Portland 0b72253e5e8a, Oregon a7674d56daf4, `us-t3` 6adae6917be1), noted beside it, so each is
// reachable as set up, the ratios with room for a run on a busy (public) server.
import type { CollectItem, Target, Tier } from "../api/types";
import type { Mode, TurnSetting } from "../state";

/** A stop a challenge fixes, named for the list. */
export interface ChallengeStop {
  lat: number;
  lon: number;
  label: string;
}

/** A brand as a challenge names it: its wikidata id, else its name (a brand's id is its
 * build's). */
export interface ChallengeBrand {
  wikidata: string;
  name: string;
}

/** The settings a challenge fixes, as the panel's (`Settings`) names them; setting one up gives
 * the others of these their defaults (the longest route, as bad as possible, any turns, no hard
 * mode, the stops in their own order, the automatic tier and leash), and a route counts only in
 * the worst order the challenge sets (`reorder`: none, unless it says so). */
export interface ChallengeSettings {
  objective?: Mode;
  item?: CollectItem;
  brand?: ChallengeBrand;
  tier?: Tier;
  leashKm?: number | "auto";
  turns?: TurnSetting;
  strict?: boolean;
  hard?: boolean;
  reorder?: boolean;
  target?: Target;
}

/** What a finished route must reach: a ratio; a count of an item; a brand's visits; the
 * alphabet's first `letters` letters in order; a drawing that beats the engine (judged on its
 * verdict); a hard-mode route's ratio; or today's route made worse (its ratio at least the
 * day's). */
export type Goal =
  | { kind: "ratio"; min: number }
  | { kind: "items"; item: CollectItem; min: number }
  | { kind: "brand"; min_visits: number }
  | { kind: "alphabet"; letters: number }
  | { kind: "drawn"; beat_engine: true }
  | { kind: "hard"; min_ratio: number }
  | { kind: "daily_worse" };

export interface Challenge {
  id: string; // the link's `?challenge=`, and the key its completion is kept under
  title: string;
  blurb: string;
  region: string | null; // the region pinned, a served region's id; null: today's route's own
  stops: ChallengeStop[] | null; // null: any stops in the region
  settings: ChallengeSettings;
  goal: Goal;
}

const POWELLS: ChallengeStop = { lat: 45.523475, lon: -122.676653, label: "Powell's Books" };
const REED: ChallengeStop = { lat: 45.481983, lon: -122.626625, label: "Reed College" };
const PDX: ChallengeStop = { lat: 45.5898, lon: -122.5951, label: "Portland Airport" };
const SQUARE: ChallengeStop = { lat: 45.5189, lon: -122.6793, label: "Pioneer Courthouse Square" };
const DOWNTOWN: ChallengeStop = { lat: 45.5231, lon: -122.6765, label: "Downtown Portland" };
const BEAVERTON: ChallengeStop = { lat: 45.4871, lon: -122.8037, label: "Beaverton" };
const MILWAUKIE: ChallengeStop = { lat: 45.4462, lon: -122.6393, label: "Milwaukie" };
const BEND: ChallengeStop = { lat: 44.0582, lon: -121.3153, label: "Bend" };
const CHICAGO: ChallengeStop = { lat: 41.8781, lon: -87.6298, label: "Chicago" };

export const CHALLENGES: readonly Challenge[] = [
  // Seed 1: 27 bridges (58.4×), converged in 19.8 s.
  {
    id: "portland-bridges",
    title: "Cross 20 bridges",
    blurb: "Collect bridges from Powell's Books to Reed College: twenty or more, each a new one.",
    region: "portland",
    stops: [POWELLS, REED],
    settings: { objective: "collect", item: "bridge" },
    goal: { kind: "items", item: "bridge", min: 20 },
  },
  // Seed 1: 61 roundabouts (54.4×) in 20 s; through the frontend, 59 and 60 (seed 1 and a seed the
  // server picked). Powell's Books → Reed College, seed 1: 44.
  {
    id: "portland-roundabouts",
    title: "Go round 40 roundabouts",
    blurb: "Collect roundabouts from Beaverton to Milwaukie: forty or more, each a new one.",
    region: "portland",
    stops: [BEAVERTON, MILWAUKIE],
    settings: { objective: "collect", item: "roundabout" },
    goal: { kind: "items", item: "roundabout", min: 40 },
  },
  // Portland's only X roads (Southwest Xanthus Court, Xylopia) are dead ends, which a route that
  // never repeats an intersection cannot drive, so no route here gets past W. Seeds 1, 2 and 4:
  // A–W (23 letters) in 20 s; seed 3: A–U.
  {
    id: "portland-alphabet",
    title: "Drive the alphabet, A to W",
    blurb:
      "From Powell's Books to the airport on roads named A, B, C… through W, in order. " +
      "Portland's only X roads are dead ends, so W is as far as the alphabet goes here.",
    region: "portland",
    stops: [POWELLS, PDX],
    settings: { objective: "alphabet" },
    goal: { kind: "alphabet", letters: 23 },
  },
  // Seed 1: Powell's Books → Reed College 63.6× (converged in 16.3 s), Beaverton → Milwaukie
  // 47.3×.
  {
    id: "portland-30x",
    title: "A 30× route in town",
    blurb: "Any trip in Portland, thirty times the sensible route or worse.",
    region: "portland",
    stops: null,
    settings: {},
    goal: { kind: "ratio", min: 30 },
  },
  // Powell's Books → Reed College: 13.3× and 10.4× (seeds 1 and 2), converged in 5–7 s; through
  // the frontend, 13.3× and 12.9× (seed 1 and a seed the server picked). Beaverton → Milwaukie
  // 6.3× (seed 1): the trip matters, which the blurb hints.
  {
    id: "portland-hard",
    title: "Hard mode, 8×",
    blurb:
      "Any trip in Portland under hard mode, each road in one stretch: eight times or worse. " +
      "Downtown to Reed College can do it.",
    region: "portland",
    stops: null,
    settings: { hard: true },
    goal: { kind: "hard", min_ratio: 8 },
  },
  // On a 300 m leash the engine's par, aiming at 10×, comes to 7.9–10.1× within 0.3 s (seeds 1–12;
  // 62–84 edges; four converge short of it), so the engine's own longest, 11.0× (its full run on
  // seed 2, 90 edges), drawn by hand, beats all twelve pars: a drawing can win.
  {
    id: "portland-draw",
    title: "Beat the engine by hand",
    blurb:
      "Draw a route from Powell's Books to Pioneer Courthouse Square within 300 m of the way, " +
      "longer than the engine's own.",
    region: "portland",
    stops: [POWELLS, SQUARE],
    settings: { leashKm: 0.3 },
    goal: { kind: "drawn", beat_engine: true },
  },
  // Seed 1 on Oregon's build: Powell's Books → Reed College 17 of the 19 within reach (58.7×),
  // Beaverton → Milwaukie 43 of 67.
  {
    id: "oregon-starbucks",
    title: "Visit 10 Starbucks",
    blurb: "A pilgrimage anywhere in Oregon past ten Starbucks or more. Portland has plenty.",
    region: "oregon",
    stops: null,
    settings: { objective: "brand", brand: { wikidata: "Q37158", name: "Starbucks" } },
    goal: { kind: "brand", min_visits: 10 },
  },
  // Seeds 1 and 2: 12.6× and 15.2× in 20 s; through the frontend, on seeds the server picked,
  // 10.4× and 10.5×.
  {
    id: "oregon-bend",
    title: "Portland to Bend at 8×",
    blurb: "Across the Cascades on Oregon's roads, eight times the sensible drive or worse.",
    region: "oregon",
    stops: [DOWNTOWN, BEND],
    settings: {},
    goal: { kind: "ratio", min: 8 },
  },
  // Seeds 1, 2 and 3: 5.5×, 6.2× and 5.5× in 20 s (the automatic tier: primary roads and up);
  // through the frontend, on seeds the server picked, 6.5× and 6.0×.
  {
    id: "us-chicago",
    title: "Portland to Chicago at 4×",
    blurb: "Cross the country on the national build, four times the sensible drive or worse.",
    region: "us-t3",
    stops: [DOWNTOWN, CHICAGO],
    settings: {},
    goal: { kind: "ratio", min: 4 },
  },
  // Any day's route made worse once, its ratio at least the day's (a continuation toward a target
  // below the base's shrinks it), on whichever region the server plans today's route.
  {
    id: "daily-worse",
    title: "Make today's route worse",
    blurb: "Open today's route and make it worse once.",
    region: null,
    stops: null,
    settings: {},
    goal: { kind: "daily_worse" },
  },
];
