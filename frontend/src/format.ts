// Numbers for people (docs/reference/frontend.md). The API's numbers are metric; miles are the
// default, kilometres the toggle. A drive time typed as a target reads `9h`, `90m`, `2h30m` or
// a number of minutes. Fuel is in US gallons and dollars, as the vehicle's mpg and gas price.
// Times of day are ISO 8601 with their offset in requests (a departure) and documents (an
// arrival), and a clock where they were given in the panel; the daily route's day is its date.
import type { CollectItem } from "./api/types";

export type Units = "mi" | "km";

const METRES_PER_MILE = 1609.344;
const FEET_PER_METRE = 3.28084;

const grouped = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const oneDecimal = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const dollars = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

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

/** A place in an order: `1st`, `2nd`, `3rd`, `4th`, `11th`, `21st`. */
export function formatOrdinal(n: number): string {
  const tens = n % 100;
  const suffix = tens >= 11 && tens <= 13 ? "th" : (["th", "st", "nd", "rd"][n % 10] ?? "th");
  return `${formatCount(n)}${suffix}`;
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

/** The fuel a route burns beyond the sensible route's, and what it costs: `+6.6 gal · $25.80`. */
export function formatFuel(gallons: number, usd: number): string {
  return `+${oneDecimal.format(gallons)} gal · ${dollars.format(usd)}`;
}

/** A share as a whole percentage, as the toasts write it: `23 %`, or `under 1 %`. */
export function formatPercent(fraction: number): string {
  const percent = Math.round(fraction * 100);
  return percent > 0 ? `${percent} %` : "under 1 %";
}

// The USPS code of each state and territory by its Census name, as the route document names them.
export const STATE_CODES: Record<string, string> = {
  Alabama: "AL",
  Alaska: "AK",
  "American Samoa": "AS",
  Arizona: "AZ",
  Arkansas: "AR",
  California: "CA",
  Colorado: "CO",
  "Commonwealth of the Northern Mariana Islands": "MP",
  Connecticut: "CT",
  Delaware: "DE",
  "District of Columbia": "DC",
  Florida: "FL",
  Georgia: "GA",
  Guam: "GU",
  Hawaii: "HI",
  Idaho: "ID",
  Illinois: "IL",
  Indiana: "IN",
  Iowa: "IA",
  Kansas: "KS",
  Kentucky: "KY",
  Louisiana: "LA",
  Maine: "ME",
  Maryland: "MD",
  Massachusetts: "MA",
  Michigan: "MI",
  Minnesota: "MN",
  Mississippi: "MS",
  Missouri: "MO",
  Montana: "MT",
  Nebraska: "NE",
  Nevada: "NV",
  "New Hampshire": "NH",
  "New Jersey": "NJ",
  "New Mexico": "NM",
  "New York": "NY",
  "North Carolina": "NC",
  "North Dakota": "ND",
  Ohio: "OH",
  Oklahoma: "OK",
  Oregon: "OR",
  Pennsylvania: "PA",
  "Puerto Rico": "PR",
  "Rhode Island": "RI",
  "South Carolina": "SC",
  "South Dakota": "SD",
  Tennessee: "TN",
  Texas: "TX",
  "United States Virgin Islands": "VI",
  Utah: "UT",
  Vermont: "VT",
  Virginia: "VA",
  Washington: "WA",
  "West Virginia": "WV",
  Wisconsin: "WI",
  Wyoming: "WY",
};

/** States by their codes: `OR, WA`; a name not known stays as it is. */
export function formatStates(names: string[]): string {
  return names.map((name) => STATE_CODES[name] ?? name).join(", ");
}

const two = (n: number) => String(Math.abs(n)).padStart(2, "0");
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
// An ISO 8601 time with its offset (or Z): what a request's departure takes.
const ISO_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/;

/** `date` at the browser's offset, to the second: `2026-10-31T09:00:00-07:00`. */
export function isoWithOffset(date: Date): string {
  const offset = -date.getTimezoneOffset();
  const day = `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())}`;
  const time = `${two(date.getHours())}:${two(date.getMinutes())}:${two(date.getSeconds())}`;
  const zone = `${offset < 0 ? "-" : "+"}${two(Math.trunc(offset / 60))}:${two(offset % 60)}`;
  return `${day}T${time}${zone}`;
}

/** The time `iso` with its clock at its own offset in its UTC fields. */
function clockOf(iso: string): Date {
  const zone = /([+-])(\d{2}):(\d{2})$/.exec(iso); // none for Z
  const minutes = zone ? (zone[1] === "-" ? -1 : 1) * (Number(zone[2]) * 60 + Number(zone[3])) : 0;
  return new Date(Date.parse(iso) + minutes * 60_000);
}

/** An ISO 8601 time with an offset, or null when `text` is none (a day the month lacks too). */
export function parseIsoTime(text: string): string | null {
  if (!ISO_TIME.test(text)) return null;
  const clock = clockOf(text);
  const real =
    !Number.isNaN(clock.getTime()) && clock.toISOString().slice(0, 16) === text.slice(0, 16);
  return real ? text : null;
}

/** An ISO 8601 time as a clock shows it at its own offset, the day and the minute:
 * `Fri 17:42`. */
export function formatClock(iso: string): string {
  const clock = clockOf(iso);
  return `${DAYS[clock.getUTCDay()]} ${two(clock.getUTCHours())}:${two(clock.getUTCMinutes())}`;
}

const dayFormat = new Intl.DateTimeFormat("en-US", {
  weekday: "long",
  month: "long",
  day: "numeric",
  timeZone: "UTC",
});

/** A date (`2026-10-31`, a UTC day) as the daily route names it: `Saturday, October 31`. */
export function formatDay(date: string): string {
  return dayFormat.format(new Date(`${date}T00:00:00Z`));
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
