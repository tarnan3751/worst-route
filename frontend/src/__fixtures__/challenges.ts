// A day's challenges as GET /challenges answers them (tests only; docs/reference/api.md): the
// eight of 2026-11-02 on `us-t3`, written from the server's contract, and `dayOf`, any day's (a
// past day's without today's route made worse, as the server answers it).
import type { ChallengeEntry, ChallengeList, ChallengeSetup, DailyStop } from "../api/types";

const LOSANTVILLE: DailyStop = { lat: 40.024, lon: -85.18, label: "Losantville, IN" };
const WALLACE: DailyStop = { lat: 39.986, lon: -87.148, label: "Wallace, IN" };
const BEND: DailyStop = { lat: 44.058173, lon: -121.31531, label: "Bend, OR" };
const BOISE: DailyStop = { lat: 43.615019, lon: -116.202314, label: "Boise, ID" };

const LONGEST: ChallengeSetup = {
  stops: [LOSANTVILLE, WALLACE],
  objective: "distance",
  item: null,
  brand: null,
  constraints: { no_left: false, no_right: false, strict: false, hard: false },
  reorder: false,
  target: { kind: "max" },
};

/** The challenges of `date`, each of the seven types, and today's route made worse when `date`
 * is today (`today`). */
export function dayOf(date: string, today: boolean): ChallengeList {
  const challenges: ChallengeEntry[] = [
    {
      id: `long-way-${date}`,
      type: "long-way",
      title: "Losantville, IN to Wallace, IN at 16×",
      blurb: "The long way round: 16 times the sensible drive, or worse.",
      setup: LONGEST,
      goal: { kind: "ratio", min: 16 },
    },
    {
      id: `slow-way-${date}`,
      type: "slow-way",
      title: "Bend, OR to Boise, ID in 29 h",
      blurb: "Take your time: 29 hours on the road, or more.",
      setup: { ...LONGEST, stops: [BEND, BOISE], objective: "time" },
      goal: { kind: "duration", min_s: 29 * 3600 },
    },
    {
      id: `no-left-${date}`,
      type: "no-left",
      title: "Bend, OR to Boise, ID with no left turns",
      blurb: "Right turns only, and no shortcuts.",
      setup: {
        ...LONGEST,
        stops: [BEND, BOISE],
        constraints: { no_left: true, no_right: false, strict: true, hard: false },
      },
      goal: { kind: "no_left", min_ratio: 1 },
    },
    {
      id: `bridges-${date}`,
      type: "bridges",
      title: "Cross 20 bridges",
      blurb: "Twenty bridges or more from Losantville, IN to Wallace, IN.",
      setup: { ...LONGEST, objective: "collect", item: "bridge" },
      goal: { kind: "items", item: "bridge", min: 20 },
    },
    {
      id: `pilgrimage-${date}`,
      type: "pilgrimage",
      title: "Visit 12 McDonald's",
      blurb: "A pilgrimage past twelve McDonald's on the way.",
      setup: {
        ...LONGEST,
        objective: "brand",
        brand: { id: 1803, name: "McDonald's", wikidata: "Q38076" },
      },
      goal: { kind: "brand", min_visits: 12 },
    },
    {
      id: `hard-mode-${date}`,
      type: "hard-mode",
      title: "Hard mode: Bend, OR to Boise, ID at 3×",
      blurb: "Each road in one stretch, three times the sensible drive or worse.",
      setup: {
        ...LONGEST,
        stops: [BEND, BOISE],
        constraints: { no_left: false, no_right: false, strict: false, hard: true },
      },
      goal: { kind: "hard", min_ratio: 3 },
    },
    {
      id: `worst-order-${date}`,
      type: "worst-order",
      title: "Four towns in the worst order at 5.5×",
      blurb: "Visit all four, in whatever order is worst.",
      setup: { ...LONGEST, stops: [LOSANTVILLE, BEND, BOISE, WALLACE], reorder: true },
      goal: { kind: "ratio", min: 5.5 },
    },
  ];
  if (today) {
    challenges.push({
      id: `daily-worse-${date}`,
      type: "daily-worse",
      title: "Make today's route worse",
      blurb: "Open today's route and make it worse once.",
      setup: null,
      goal: { kind: "daily_worse" },
    });
  }
  return { date, region: "us-t3", build_id: "6adae6917be1", challenges };
}

export const ANSWER = dayOf("2026-11-02", true);
