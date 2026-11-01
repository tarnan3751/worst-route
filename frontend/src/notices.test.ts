// The data sources and notices page (public/notices.html): each source with its licence and a
// link, the themes' typefaces with their copyright notices and the SIL Open Font License once,
// the footer's link to it (relative, so it works under any prefix), and the page in the build,
// where `serve --frontend` serves it (Vite copies public/ into dist/ as it is).
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
    const sources = /<ul>([\s\S]*?)<\/ul>/.exec(page)?.[1] ?? "";
    const entries = [...sources.matchAll(/<li>([\s\S]*?)<\/li>/g)].map((m) => m[1]);
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

  it("name each theme's typeface with its copyright notice, and the OFL's text once", () => {
    const page = read("public/notices.html");
    expect(page).toContain('<h2 id="fonts">Fonts</h2>');
    const list = /<ul class="fonts">([\s\S]*?)<\/ul>/.exec(page)?.[1] ?? "";
    // Prettier may rewrap an entry: compared with its whitespace collapsed.
    const entries = [...list.matchAll(/<li>([\s\S]*?)<\/li>/g)].map((m) =>
      m[1].replace(/\s+/g, " ").trim(),
    );
    // Each notice as the font's own name table gives it (its copyright string, name ID 0).
    const expected: [string, string][] = [
      [
        "Manrope",
        "Copyright 2019 The Manrope Project Authors (https://github.com/sharanda/manrope)",
      ],
      ["Sora", "Copyright 2019 The Sora Project Authors (https://github.com/sora-xor/sora-font)"],
      [
        "Archivo",
        "Copyright 2020 The Archivo Project Authors (https://github.com/Omnibus-Type/Archivo)",
      ],
      ["Zilla Slab", "Copyright 2017, The Mozilla Foundation"],
      [
        "Nunito Sans",
        "Copyright 2016 The Nunito Sans Project Authors (https://github.com/Fonthausen/NunitoSans)",
      ],
      [
        "Press Start 2P",
        'Copyright 2012 The Press Start 2P Project Authors (cody@zone38.net), with Reserved Font Name "Press Start 2P"',
      ],
      ["VT323", "Copyright 2011, The VT323 Project Authors (peter.hull@oikoi.com)"],
      ["IBM Plex Mono", "Copyright 2017 IBM Corp. All rights reserved."],
      ["IBM Plex Sans", "Copyright 2019 IBM Corp. All rights reserved."],
      [
        "Barlow Condensed",
        "Copyright 2017 The Barlow Project Authors (https://github.com/jpt/barlow)",
      ],
      ["Source Sans 3", "© 2023 Adobe (http://www.adobe.com/), with Reserved Font Name ‘Source’"],
      [
        "Overpass",
        "Copyright 2021 The Overpass Project Authors (https://github.com/RedHatOfficial/Overpass)",
      ],
      [
        "Courier Prime",
        "Copyright 2015 The Courier Prime Project Authors (https://github.com/quoteunquoteapps/CourierPrime).",
      ],
    ];
    expect(entries).toEqual(
      expected.map(([family, notice]) => `<strong>${family}</strong> · ${notice}`),
    );
    // Every Fontsource package the app depends on named there, by its family.
    const dependencies = Object.keys(JSON.parse(read("package.json")).dependencies);
    for (const name of dependencies.filter((d) => d.startsWith("@fontsource/"))) {
      const { family } = JSON.parse(read(`node_modules/${name}/metadata.json`));
      expect(list, name).toContain(`<strong>${family}</strong>`);
    }
    // The licence's text once, whole: its title, its conditions and its disclaimer's last words.
    const text = /<pre class="licence-text">([\s\S]*?)<\/pre>/.exec(page)?.[1] ?? "";
    expect(page.split("SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007")).toHaveLength(2);
    expect(text).toContain("SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007");
    expect(text).toContain("PERMISSION &amp; CONDITIONS");
    expect(text).toContain("5) The Font Software, modified or unmodified, in part or in whole,");
    expect(text.trimEnd()).toMatch(/OTHER DEALINGS IN THE FONT SOFTWARE\.$/);
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
