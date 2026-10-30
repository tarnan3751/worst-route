// Numbers for people (docs/reference/frontend.md). The API's numbers are metric; miles are the
// default, kilometres the toggle.

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

/** A coordinate as the stop list shows it. */
export function formatLatLon(lat: number, lon: number): string {
  return `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
}
