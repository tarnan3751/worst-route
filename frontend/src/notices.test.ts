// The data sources and notices page (public/notices.html): each source with its licence and a
// link, the footer's link to it (relative, so it works under any prefix), and the page in the
// build, where `serve --frontend` serves it (Vite copies public/ into dist/ as it is).
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";
import { describe, expect, it } from "vitest";

const root = fileURLToPath(new URL("..", import.meta.url));
const read = (path: string): string => readFileSync(join(root, path), "utf8");

describe("the data sources and notices", () => {
  it("are linked from the footer, relatively, in a new tab", () => {
    const footer = /<footer id="credits">([\s\S]*?)<\/footer>/.exec(read("index.html"))?.[1];
    expect(footer).toBeDefined();
    const link = /<a href="([^"]*)"([^>]*)>Data sources<\/a>/.exec(footer!);
    expect(link?.[1]).toBe("notices.html");
    expect(link?.[2]).toContain('target="_blank"');
    // The credits before it stay.
    expect(footer).toContain("https://www.openstreetmap.org/copyright");
    expect(footer).toContain("U.S. Census Bureau");
  });

  it("list every source with its licence and a link", () => {
    const page = read("public/notices.html");
    expect(page).toContain("<title>Data sources and notices · Worst Route</title>");
    const entries = [...page.matchAll(/<li>([\s\S]*?)<\/li>/g)].map((m) => m[1]);
    const expected: [string, string, string][] = [
      ["Road data", "Open Database License (ODbL)", "https://www.openstreetmap.org/copyright"],
      ["Towns and states", "public domain", "https://www.census.gov/"],
      ["Basemap tiles", "Open Database License (ODbL)", "https://protomaps.com"],
      ["Basemap style", "BSD-3-Clause", "https://github.com/protomaps/basemaps"],
      ["Land cover", "CC BY 4.0", "https://esa-worldcover.org"],
      [
        "Online basemap tiles",
        "Open Database License (ODbL), © OpenMapTiles",
        "https://openfreemap.org",
      ],
      [
        "Online basemap style",
        "CC BY 4.0 (design), BSD-3-Clause (code)",
        "https://github.com/hyperknot/openfreemap-styles",
      ],
      ["Online map icons", "CC0 1.0", "https://github.com/mapbox/maki"],
      [
        "Map icons",
        "MIT License, Copyright (c) 2017 Mapzen",
        "https://github.com/tangrams/icons/blob/master/LICENSE.md",
      ],
      ["Fonts", "SIL Open Font License 1.1", "https://openfontlicense.org"],
      ["MapLibre GL JS", "BSD-3-Clause", "https://github.com/maplibre/maplibre-gl-js"],
      ["pmtiles", "BSD-3-Clause", "https://github.com/protomaps/PMTiles"],
      ["pywebview", "BSD-3-Clause", "https://github.com/r0x0r/pywebview"],
      [
        "PyInstaller",
        "GPL-2.0-or-later with the bootloader exception",
        "https://pyinstaller.org/en/stable/license.html",
      ],
      ["OR-Tools", "Apache-2.0", "https://github.com/google/or-tools"],
      ["absl-py", "Apache-2.0", "https://github.com/abseil/abseil-py"],
      ["protobuf", "BSD-3-Clause", "https://github.com/protocolbuffers/protobuf"],
      ["immutabledict", "MIT", "https://github.com/corenting/immutabledict"],
      [
        "Libraries in OR-Tools' native library",
        "each its own, as named; zlib License; bzip2 License",
        "https://github.com/coin-or",
      ],
      [
        "Python and its libraries",
        "each its own: THIRD-PARTY-LICENSES.txt in the app",
        "https://docs.python.org/3/license.html",
      ],
    ];
    expect(entries).toHaveLength(expected.length);
    expected.forEach(([name, licence, href], k) => {
      expect(entries[k]).toContain(`<strong>${name}</strong>`);
      expect(entries[k]).toContain(`<span class="licence">${licence}</span>`);
      expect(entries[k]).toContain(`href="${href}"`);
    });
    // Each library the grouped entry covers named (prettier may rewrap it), Eigen's source linked.
    const bundled = entries
      .find((e) => e.includes("<strong>Libraries in OR-Tools' native library"))
      ?.replace(/\s+/g, " ");
    const names = [
      "COIN-OR Cbc, Clp, Cgl, Osi and CoinUtils",
      "SCIP and SoPlex",
      "HiGHS",
      "RE2",
      "Abseil",
      "Protocol Buffers",
      "utf8_range (MIT, Copyright (c) 2019 Yibo Cai, Copyright 2022 Google LLC;",
      "Eigen (MPL-2.0;",
      "pybind11 with pybind11_abseil and pybind11_protobuf (BSD-3-Clause)",
      "zlib and bzip2",
    ];
    for (const name of names) expect(bundled).toContain(name);
    expect(bundled).toContain('href="https://gitlab.com/libeigen/eigen"');
    // Back to the map, relatively too.
    expect(page).toContain('<a href="./">Back to the map</a>');
  });

  it("are in the build", { timeout: 120_000 }, async () => {
    const outDir = mkdtempSync(join(tmpdir(), "worst-route-notices-"));
    try {
      await build({ root, logLevel: "silent", build: { outDir, emptyOutDir: true } });
      expect(existsSync(join(outDir, "index.html"))).toBe(true);
      expect(readFileSync(join(outDir, "notices.html"), "utf8")).toBe(read("public/notices.html"));
    } finally {
      rmSync(outDir, { recursive: true, force: true });
    }
  });
});
