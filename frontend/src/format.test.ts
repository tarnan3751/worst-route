import { describe, expect, it } from "vitest";
import {
  formatCount,
  formatDistance,
  formatDuration,
  formatDurationInput,
  formatElapsed,
  formatClock,
  formatDay,
  formatFuel,
  formatItems,
  formatLatLon,
  formatOrdinal,
  formatPercent,
  formatRatio,
  formatStates,
  isoWithOffset,
  parseDuration,
  parseIsoTime,
} from "./format";

describe("formatDistance", () => {
  it("gives miles, feet under a tenth of a mile", () => {
    expect(formatDistance(318402, "mi")).toBe("198 mi");
    expect(formatDistance(7420, "mi")).toBe("4.6 mi");
    expect(formatDistance(161, "mi")).toBe("0.1 mi");
    expect(formatDistance(160, "mi")).toBe("525 ft");
    expect(formatDistance(12.4, "mi")).toBe("41 ft");
  });

  it("gives kilometres, metres under one", () => {
    expect(formatDistance(318402, "km")).toBe("318 km");
    expect(formatDistance(7420, "km")).toBe("7.4 km");
    expect(formatDistance(1000, "km")).toBe("1.0 km");
    expect(formatDistance(999.6, "km")).toBe("1,000 m");
    expect(formatDistance(214, "km")).toBe("214 m");
  });

  it("groups thousands", () => {
    expect(formatDistance(4_023_360, "mi")).toBe("2,500 mi");
  });
});

describe("formatDuration", () => {
  it("gives seconds, minutes, then hours and minutes", () => {
    expect(formatDuration(42)).toBe("42 s");
    expect(formatDuration(59.6)).toBe("1 min");
    expect(formatDuration(1130)).toBe("19 min");
    expect(formatDuration(3600)).toBe("1 h");
    expect(formatDuration(41880)).toBe("11 h 38 min");
    expect(formatDuration(3570)).toBe("1 h");
    expect(formatDuration(100 * 3600 + 60)).toBe("100 h 1 min");
  });
});

describe("the other numbers", () => {
  it("formats ratios, counts, elapsed time and coordinates", () => {
    expect(formatRatio(42.94)).toBe("42.9×");
    expect(formatRatio(5)).toBe("5.0×");
    expect(formatRatio(null)).toBe("–");
    expect(formatCount(82841)).toBe("82,841");
    expect(formatElapsed(11.83)).toBe("11.8 s");
    expect(formatLatLon(45.5231, -122.6765)).toBe("45.523100, -122.676500");
  });

  it("counts items, one and many", () => {
    expect(formatItems(1, "bridge")).toBe("1 bridge");
    expect(formatItems(24, "bridge")).toBe("24 bridges");
    expect(formatItems(0, "toll_booth")).toBe("0 toll booths");
    expect(formatItems(1211, "level_crossing")).toBe("1,211 level crossings");
    expect(formatItems(1, "roundabout")).toBe("1 roundabout");
  });

  it("writes a place in an order", () => {
    const places = [1, 2, 3, 4, 7, 11, 12, 13, 21, 22, 23, 101, 111].map(formatOrdinal);
    expect(places).toEqual([
      "1st", "2nd", "3rd", "4th", "7th", "11th", "12th", "13th", "21st", "22nd", "23rd",
      "101st", "111th",
    ]); // prettier-ignore
  });
});

describe("drive times typed as a target", () => {
  it("reads hours, minutes, seconds, and a bare number as minutes", () => {
    expect(parseDuration("9h")).toBe(32400);
    expect(parseDuration("90m")).toBe(5400);
    expect(parseDuration("2h30m")).toBe(9000);
    expect(parseDuration(" 2h 30min ")).toBe(9000);
    expect(parseDuration("1.5H")).toBe(5400);
    expect(parseDuration("45s")).toBe(45);
    expect(parseDuration("90")).toBe(5400);
  });

  it("refuses anything else", () => {
    for (const text of ["", "h", "9x", "2m30h", "-3h", "9 hours", "1:30"]) {
      expect(parseDuration(text)).toBeNull();
    }
  });

  it("writes back what it reads", () => {
    for (const seconds of [32400, 9000, 2700, 3630, 45, 7 * 24 * 3600]) {
      expect(parseDuration(formatDurationInput(seconds))).toBe(seconds);
    }
    expect(formatDurationInput(32400)).toBe("9h");
    expect(formatDurationInput(9000)).toBe("2h30m");
    expect(formatDurationInput(3630)).toBe("1h0m30s");
  });
});

describe("a stored route's numbers", () => {
  it("gives the extra fuel in gallons and its cost in dollars", () => {
    expect(formatFuel(6.6178, 25.8037)).toBe("+6.6 gal · $25.80");
    expect(formatFuel(312.04, 1213.83)).toBe("+312.0 gal · $1,213.83");
    expect(formatFuel(0, 0)).toBe("+0.0 gal · $0.00");
  });

  it("gives a share in whole percent, as the toasts do", () => {
    expect(formatPercent(0.2305)).toBe("23 %");
    expect(formatPercent(0.11965)).toBe("12 %");
    expect(formatPercent(0.004)).toBe("under 1 %");
    expect(formatPercent(1)).toBe("100 %");
  });

  it("gives the states by their codes, a name it does not know as it is", () => {
    expect(formatStates(["Oregon", "Washington"])).toBe("OR, WA");
    expect(formatStates(["District of Columbia", "Puerto Rico"])).toBe("DC, PR");
    expect(formatStates(["Atlantis"])).toBe("Atlantis");
  });
});

describe("times of day", () => {
  it("writes a time at the browser's offset, to the second, and reads it back", () => {
    for (const time of [
      "2026-10-31T16:00:30.700Z",
      "2026-07-04T05:59:59Z",
      "2026-01-01T00:00:00Z",
    ]) {
      const date = new Date(time);
      const iso = isoWithOffset(date);
      expect(iso).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$/);
      expect(Date.parse(iso)).toBe(Math.floor(date.getTime() / 1000) * 1000);
      expect(parseIsoTime(iso)).toBe(iso);
    }
  });

  it("reads only ISO 8601 times with an offset", () => {
    for (const ok of [
      "2026-10-31T09:00-07:00",
      "2026-10-31T09:00:00.123456+05:30",
      "2026-10-31T16:00:00Z",
    ]) {
      expect(parseIsoTime(ok)).toBe(ok);
    }
    for (const bad of ["2026-10-31T09:00", "2026-10-31", "2026-02-30T09:00:00Z", "noon", ""]) {
      expect(parseIsoTime(bad)).toBeNull();
    }
  });

  it("shows a time as a clock at its own offset, the day and the minute", () => {
    expect(formatClock("2026-10-31T17:42:59.9-07:00")).toBe("Sat 17:42");
    expect(formatClock("2026-10-31T23:05:00+05:30")).toBe("Sat 23:05");
    expect(formatClock("2026-11-02T00:00:00Z")).toBe("Mon 00:00");
  });

  it("names a UTC day as the daily route does", () => {
    expect(formatDay("2026-10-31")).toBe("Saturday, October 31");
    expect(formatDay("2026-01-01")).toBe("Thursday, January 1");
  });
});
