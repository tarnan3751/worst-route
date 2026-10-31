// Numbers for people (docs/reference/frontend.md). The API's numbers are metric; miles are the
// default, kilometres the toggle. A drive time typed as a target reads `9h`, `90m`, `2h30m` or
// a number of minutes.
import type { CollectItem } from "./api/types";

export type Units = "mi" | "km";

const METRES_PER_MILE = 1609.344;
const FEET_PER_METRE = 3.28084;

const grouped = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const oneDecimal = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

/** A distance: miles (feet under a tenth of a mile) or kilometres (metres under one). */
export function formatDistance(metres: number, units: Units): string {
  if (units === "mi") {
    const miles = metres / METRES_PER_MILE;
    if (miles < 0.1) return `${grouped.format(metres * FEET_PER_METRE)} ft`;
    return `${miles < 100 ? oneDecimal.format(miles) : grouped.format(miles)} mi`;
  }
  const km = metres / 1000;
  if (km < 1) return `${grouped.format(metres)} m`;
  return `${km < 100 ? oneDecimal.format(km) : grouped.format(km)} km`;
}

/** A drive time: seconds, minutes, or hours and minutes. */
export function formatDuration(seconds: number): string {
  if (Math.round(seconds) < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${grouped.format(hours)} h ${rest} min` : `${grouped.format(hours)} h`;
}

/** Wall time, as a run's elapsed seconds. */
export function formatElapsed(seconds: number): string {
  return `${oneDecimal.format(seconds)} s`;
}

/** How many times worse; a round trip has no ratio. */
export function formatRatio(ratio: number | null): string {
  return ratio === null ? "–" : `${oneDecimal.format(ratio)}×`;
}

export function formatCount(n: number): string {
  return grouped.format(n);
}

// What a collect run collects, one and many.
export const ITEM_NAMES: Record<CollectItem, [string, string]> = {
  toll_booth: ["toll booth", "toll booths"],
  level_crossing: ["level crossing", "level crossings"],
  roundabout: ["roundabout", "roundabouts"],
  bridge: ["bridge", "bridges"],
};

/** A count of items: `1 bridge`, `24 bridges`. */
export function formatItems(n: number, item: CollectItem): string {
  return `${formatCount(n)} ${ITEM_NAMES[item][n === 1 ? 0 : 1]}`;
}

/** A coordinate as the stop list shows it. */
export function formatLatLon(lat: number, lon: number): string {
  return `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
}

/** A drive time typed by hand, in seconds: `9h`, `90m`, `2h30m`, `45s`, or a bare number of
 * minutes; null when it is none of those. */
export function parseDuration(text: string): number | null {
  const compact = text.trim().toLowerCase().replace(/\s+/g, "");
  if (/^\d+(\.\d+)?$/.test(compact)) return Number(compact) * 60;
  const parts = /^(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?:in)?)?(?:(\d+(?:\.\d+)?)s)?$/.exec(
    compact,
  );
  if (parts === null || parts.slice(1).every((part) => part === undefined)) return null;
  const [hours, minutes, seconds] = parts.slice(1).map((part) => Number(part ?? 0));
  return hours * 3600 + minutes * 60 + seconds;
}

/** Seconds as `parseDuration` reads them back: `9h`, `2h30m`, `45m`, `1h0m30s`. */
export function formatDurationInput(seconds: number): string {
  const whole = Math.round(seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const rest = whole % 60;
  let text = hours ? `${hours}h` : "";
  if (minutes || (hours && rest)) text += `${minutes}m`;
  if (rest) text += `${rest}s`;
  return text || "0m";
}
