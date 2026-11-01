// The settings (docs/reference/frontend.md, **Settings**): the request's seed (empty: random,
// picked by the server), the seeds a job runs at once, keeping the best (1 up to the most the
// server runs, from GET /config, a setting above it lowered to it; the row hidden when that is 1,
// and not under the alphabet route), time budget, tier (by its roads, its code and road classes in
// the option's tooltip) and leash (empty: auto, with what auto became once a job has said; the
// corridor, as the panel calls it), the region (picked by the server from the stops, or one of
// those it serves pinned; the row only when it serves several), the mode (the longest, the
// slowest, collecting an item, picked under it, a brand's pilgrimage, the brand picked under it
// from the GET /brands of the region trips run on, asked again whenever that region changes, or the
// alphabet route; a line under them says what the one picked does), the turns (any, or left or
// right turns avoided, `strict` for none at all; hard mode, each road in one stretch; the worst
// order, the stops between the start and the end visited in whatever order is worst, not under the
// alphabet route; and Certified, the provably worst route in
// a small area, under the longest only, which then takes none of the target, the turns, hard mode,
// the worst order or a second seed, their controls disabled with their values kept), the
// inefficiency target (as bad as
// possible, a ratio to the sensible route, or a drive time typed as `9h`, `90m`, `2h30m` or
// minutes; typing a number picks its kind; no ratio under collect or brand, none under the alphabet
// route), the vehicle (its mpg and the gas price, for the route's extra fuel and its cost), the
// departure (empty: now; for the route's arrival), and the units the panel shows. Locked while a
// job runs, except the units. The setting an error names (`field`: the target, the item, the brand,
// the constraints, the worst order, Certified, the leash, the seeds, the vehicle or the departure;
// the seeds for PORTFOLIO_TOO_LARGE too) is highlighted.
import { errorBody, getBrands } from "../api/client";
import type { BrandEntry, CollectItem, Region, TargetKind, Tier } from "../api/types";
import {
  ITEM_NAMES,
  formatCount,
  formatDurationInput,
  isoWithOffset,
  parseDuration,
  type Units,
} from "../format";
import {
  BUDGET_MAX_S,
  GAS_PRICE_MAX,
  GAS_PRICE_MIN,
  ITEMS,
  LEASH_MAX_KM,
  MAX_PORTFOLIO,
  MPG_MAX,
  MPG_MIN,
  TARGET_MAX_DURATION_S,
  TIERS,
  takesTarget,
  type AppState,
  type Mode,
  type Settings,
  type Store,
  type TurnSetting,
} from "../state";
import { ROADS, regionLabel } from "../stops/pick";
import { busy } from "../stops/stopList";
import { el } from "../util/dom";
import { themeRow } from "./themeRow";

const TARGETS: [TargetKind, string, boolean][] = [
  ["max", "As bad as possible", true],
  ["ratio", "A ratio", true],
  ["duration", "A drive time", true],
];
const MODE_OPTIONS: [Mode, string, boolean][] = [
  ["distance", "Longest", true],
  ["time", "Slowest", true],
  ["collect", "Collect", true],
  ["brand", "Brand", true],
  ["alphabet", "Alphabet", true],
];
// What each mode does, under the Mode row.
const MODE_NOTES: Record<Mode, string> = {
  distance: "the longest legal route",
  time: "the slowest, counting delays",
  collect: "passes as many of one thing as it can",
  brand: "visits as many of a chain's locations as it can",
  alphabet: "named roads in A, B, C order",
};
// The road classes each tier keeps (T0..T4, `config.TIERS`), in its Roads option's tooltip.
const TIER_CLASSES = [
  "motorways and trunks",
  "primary roads and up",
  "secondary roads and up",
  "tertiary roads and up",
  "every road",
];
const TURNS: [TurnSetting, string, boolean][] = [
  ["any", "Any", true],
  ["no_left", "Avoid left turns", true],
  ["no_right", "Avoid right turns", true],
];
// Why a mode refuses a target kind, under the target row.
const TARGET_NOTES: Partial<Record<Mode, string>> = {
  collect: "a collect run takes a drive-time goal only",
  brand: "a brand run counts visits, so a drive-time goal only",
  alphabet: "the alphabet route takes no goal",
};
const HARD = "each road in one stretch; once left, never again";
const REORDER = "visits the stops between in the worst order; 7 at most, 5 on a big map";
const CERTIFIED =
  "proves there is no longer legal route in this small area; keep the corridor short to keep " +
  "the area small";
const CORRIDOR = "how far off the direct line it may wander";
const SEEDS = "runs that many searches and keeps the best; the map shows the one ahead";
const ONE_SEARCH = "the alphabet route runs one search";
const ONE_CERTIFIED = "a proof runs one search";
const NO_TARGET = "a proof finds the longest, so it takes no goal";
const UNITS: [Units, string][] = [
  ["mi", "Miles"],
  ["km", "Kilometres"],
];

/** A tier's place in T0..T4: 2 for "T2". */
function tierIndex(tier: string): number {
  return Number(tier.slice(1));
}

/** A number typed in `input`: null when empty, undefined when not a number in [min, max]. */
function typed(input: HTMLInputElement, min: number, max: number, integer: boolean) {
  if (input.value.trim() === "") return null;
  const value = Number(input.value);
  const ok = integer ? Number.isSafeInteger(value) : Number.isFinite(value);
  return ok && value >= min && value <= max ? value : undefined;
}

/** A labelled row; a group of controls is a `div`, since a label holds one. */
function field(label: string, control: HTMLElement, note?: HTMLElement): HTMLElement {
  const row = el(control instanceof HTMLDivElement ? "div" : "label", "field");
  row.append(el("span", "label", label), control);
  if (note) row.append(note);
  return row;
}

function numberInput(placeholder: string, min: number, max: number, step: string) {
  const input = el("input");
  Object.assign(input, { type: "number", placeholder, min: String(min), max: String(max), step });
  return input;
}

function select<T extends string>(options: [T, string, boolean][]): HTMLSelectElement {
  const control = el("select");
  for (const [value, text, enabled] of options) {
    const option = el("option", "", text);
    option.value = value;
    option.disabled = !enabled;
    control.append(option);
  }
  return control;
}

function radios<T extends string>(name: string, options: [T, string, boolean][]) {
  const group = el("div", "radios");
  const inputs = options.map(([value, text, enabled]) => {
    const input = el("input");
    Object.assign(input, { type: "radio", name, value, disabled: !enabled });
    const label = el("label", enabled ? "" : "disabled");
    label.append(input, ` ${text}`);
    group.append(label);
    return input;
  });
  return { group, inputs };
}

/** The brand picker's options: each brand by its name and how many locations it has,
 * "Starbucks (126)", in the API's order (the most located first). */
export function brandOptions(brands: BrandEntry[]): [string, string, boolean][] {
  return brands.map(({ id, name, locations }) => [
    String(id),
    `${name} (${formatCount(locations)})`,
    true,
  ]);
}

/** The region picker's options: the server's pick, then each region it serves by its name and
 * roads, "United States · main roads", in its order. */
export function regionOptions(regions: Region[]): [string, string, boolean][] {
  return [
    ["", "Picked from the stops", true],
    ...regions.map((r): [string, string, boolean] => [r.region, regionLabel(r), true]),
  ];
}

/** The seeds picker's options: 1 up to `most`, the most this server runs (never above
 * `MAX_PORTFOLIO`). */
export function seedOptions(most: number): [string, string, boolean][] {
  const count = Math.max(1, Math.min(most, MAX_PORTFOLIO));
  return Array.from({ length: count }, (_, k): [string, string, boolean] => {
    const seeds = String(k + 1);
    return [seeds, seeds, true];
  });
}

/** The settings' seeds within `most`: lowered to it when above it. */
export function seedsWithin(portfolio: number, most: number): number {
  return Math.max(1, Math.min(portfolio, most));
}

/** Keep the settings' seeds within `portfolioMax`, the most this server runs, once it has said:
 * lowered then, and whenever a link or a route opened later raises them. Gives the most. */
export async function followPortfolioMax(
  store: Store,
  portfolioMax: Promise<number>,
): Promise<number> {
  const most = await portfolioMax;
  store.watch(["settings"], ({ settings }) => {
    const within = seedsWithin(settings.portfolio, most);
    if (within !== settings.portfolio) store.updateSettings({ portfolio: within });
  });
  return most;
}

/** The brand of `brands` that stands for brand id `brand`: when it is one of `previous`, another
 * build's list (a brand's id is its build's), the same brand by its wikidata id, else its name;
 * else the same id; else the first. */
function brandIn(brands: BrandEntry[], previous: BrandEntry[] | null, brand: number | null) {
  const was = previous?.find(({ id }) => id === brand);
  const same =
    was === undefined
      ? brands.find(({ id }) => id === brand)
      : brands.find((b) =>
          was.wikidata !== null ? b.wikidata === was.wikidata : b.name === was.name,
        );
  return (same ?? brands[0]).id;
}

/** The brands of `region` (GET /brands), into the store, unless trips have moved on to another
 * region meanwhile; the settings' brand becomes the same brand when it was one of `previous`
 * (the last region's), else stays when the region has it, else becomes the first (none yet, or
 * one a link named that the build lacks). */
export async function loadBrands(
  store: Store,
  region: string,
  previous: BrandEntry[] | null = null,
): Promise<BrandEntry[]> {
  const { brands } = await getBrands(region);
  const { settings, region: now } = store.state;
  if (now !== null && now.region !== region) return brands;
  const brand = brands.length === 0 ? settings.brand : brandIn(brands, previous, settings.brand);
  store.update({ brands, settings: { ...settings, brand } });
  return brands;
}

/** Ask for the brands of the region trips run on whenever it changes (`loadBrands`, the brand
 * picked carried over from the last brands loaded), `show` putting "loading…" in the picker
 * meanwhile, or "brands unavailable" when the request fails (its error in the banner), unless the
 * trips have moved on to another region since. A failure lets the next change of region ask
 * again, to the same region too, so the picker is never left waiting. */
export function followBrands(store: Store, show: (text: string) => void): void {
  let asked: string | null = null; // the region whose brands were asked for last
  let known: BrandEntry[] | null = null; // the last brands loaded, whichever region's
  store.watch(["brands"], ({ brands }) => {
    known = brands ?? known;
  });
  store.watch(["region"], ({ region: on, brands }) => {
    if (on === null || on.region === asked) return;
    const region = on.region;
    asked = region;
    show("loading…");
    if (brands !== null) store.update({ brands: null });
    loadBrands(store, region, known).catch((error: unknown) => {
      if (asked === region) asked = null;
      const now = store.state.region;
      if (now !== null && now.region !== region) return;
      show("brands unavailable");
      store.update({ error: errorBody(error) });
    });
  });
}

/** The settings in `root`; `portfolioMax`, the most seeds this server runs (GET /config), shows
 * the seeds' row once it is more than one. */
export function mountSettings(
  root: HTMLElement,
  store: Store,
  portfolioMax: Promise<number> = Promise.resolve(1),
): void {
  const seed = numberInput("random", 0, Number.MAX_SAFE_INTEGER, "1");
  const seeds = el("select");
  seeds.ariaLabel = "runs at once";
  const seedsNote = el("span", "resolved", SEEDS);
  const budget = numberInput("", 1, BUDGET_MAX_S, "1");
  const tier = select(
    TIERS.map((t): [Tier, string, boolean] => [t, t === "auto" ? t : ROADS[tierIndex(t)], true]),
  );
  for (const option of tier.options) {
    if (option.value !== "auto") {
      option.title = `${option.value}: ${TIER_CLASSES[tierIndex(option.value)]}`;
    }
  }
  const tierNote = el("span", "resolved");
  const leash = numberInput("auto", 0.1, LEASH_MAX_KM, "0.1");
  const leashNote = el("span", "resolved", CORRIDOR);
  const region = select<string>(regionOptions([]));
  const regionNote = el("span", "resolved", "the smallest map that holds every stop");
  const target = radios("target", TARGETS);
  const ratio = numberInput("20", 1, Number.MAX_SAFE_INTEGER, "any");
  const duration = el("input", "duration");
  Object.assign(duration, { type: "text", placeholder: "9h" });
  target.inputs[1].parentElement?.append(" ", ratio, " ×");
  target.inputs[2].parentElement?.append(" ", duration);
  const targetNote = el("span", "resolved");
  const mode = radios("mode", MODE_OPTIONS);
  const modeNote = el("span", "resolved");
  const item = select(
    ITEMS.map((i): [CollectItem, string, boolean] => [i, ITEM_NAMES[i][1], true]),
  );
  mode.inputs[2].parentElement?.append(" ", item);
  const brand = select<string>([["", "loading…", true]]); // the region's brands, once known
  brand.ariaLabel = "brand";
  mode.inputs[3].parentElement?.append(" ", brand);
  const turns = radios("turns", TURNS);
  const strict = el("input");
  strict.type = "checkbox";
  const strictLabel = el("label");
  strictLabel.append(strict, " strict");
  turns.group.append(strictLabel);
  const strictNote = el("span", "resolved", "strict rarely finds a route on a street grid");
  const hard = el("input");
  hard.type = "checkbox";
  const hardLabel = el("label");
  hardLabel.append(hard, " No road twice, ever");
  hardLabel.title = HARD;
  turns.group.append(hardLabel);
  const hardNote = el("span", "resolved", HARD);
  const reorder = el("input");
  reorder.type = "checkbox";
  const reorderLabel = el("label");
  reorderLabel.append(reorder, " Worst order of stops");
  reorderLabel.title = REORDER;
  turns.group.append(reorderLabel);
  const reorderNote = el("span", "resolved", REORDER);
  const certified = el("input");
  certified.type = "checkbox";
  const certifiedLabel = el("label");
  certifiedLabel.append(certified, " Prove it");
  certifiedLabel.title = CERTIFIED;
  turns.group.append(certifiedLabel);
  const certifiedNote = el("span", "resolved", CERTIFIED);
  const mpg = numberInput("", MPG_MIN, MPG_MAX, "any");
  mpg.ariaLabel = "miles per gallon";
  const gasPrice = numberInput("", GAS_PRICE_MIN, GAS_PRICE_MAX, "0.01");
  gasPrice.ariaLabel = "gas price in dollars a gallon";
  const mpgLabel = el("label");
  mpgLabel.append(mpg, " mpg");
  const priceLabel = el("label");
  priceLabel.append("$ ", gasPrice, " a gallon");
  const vehicle = el("div", "vehicle");
  vehicle.append(mpgLabel, priceLabel);
  const departure = el("input");
  departure.type = "datetime-local";
  const departureNote = el("span", "resolved", "empty means now");
  const units = radios(
    "units",
    UNITS.map(([u, text]): [Units, string, boolean] => [u, text, true]),
  );

  const seedsRow = field("Runs at once", seeds, seedsNote);
  seedsRow.hidden = true; // until the server says it runs more than one
  const leashRow = field("Corridor (km)", leash, leashNote);
  const regionRow = field("Region", region, regionNote);
  const modeRow = field("Mode", mode.group, modeNote);
  const turnsRow = field("Turns", turns.group, strictNote);
  turnsRow.append(hardNote, reorderNote, certifiedNote);
  const targetRow = field("Goal", target.group, targetNote);
  const vehicleRow = field("Vehicle", vehicle);
  const departureRow = field("Departure", departure, departureNote);

  root.replaceChildren(
    el("h2", "", "Settings"),
    field("Seed", seed),
    seedsRow,
    field("Time budget (s)", budget),
    field("Roads", tier, tierNote),
    leashRow,
    regionRow,
    modeRow,
    turnsRow,
    targetRow,
    vehicleRow,
    departureRow,
    field("Units", units.group),
    themeRow(store),
  );

  const set = (patch: Partial<Settings>) => store.updateSettings(patch);
  const render = () => {
    const { settings, brands } = store.state;
    seed.value = settings.seed === null ? "" : String(settings.seed);
    seeds.value = String(settings.portfolio);
    budget.value = String(settings.budgetS);
    tier.value = settings.tier;
    leash.value = settings.leashKm === "auto" ? "" : String(settings.leashKm);
    region.value = settings.region ?? "";
    regionNote.hidden = settings.region !== null;
    for (const input of target.inputs) input.checked = input.value === settings.target;
    ratio.value = String(settings.ratio);
    duration.value = formatDurationInput(settings.durationS);
    for (const input of mode.inputs) input.checked = input.value === settings.objective;
    modeNote.textContent = MODE_NOTES[settings.objective];
    item.value = settings.item;
    item.hidden = settings.objective !== "collect";
    brand.value = brands?.some(({ id }) => id === settings.brand) ? String(settings.brand) : "";
    brand.hidden = settings.objective !== "brand";
    for (const input of turns.inputs) input.checked = input.value === settings.turns;
    strict.checked = settings.strict;
    hard.checked = settings.hard;
    reorder.checked = settings.reorder;
    certified.checked = settings.certified;
    mpg.value = String(settings.mpg);
    gasPrice.value = String(settings.gasPriceUsd);
    departure.value = settings.departure?.slice(0, 16) ?? ""; // its clock, at its own offset
    for (const input of units.inputs) input.checked = input.value === settings.units;
  };

  seed.addEventListener("change", () => {
    const value = typed(seed, 0, Number.MAX_SAFE_INTEGER, true);
    if (value === undefined) render();
    else set({ seed: value });
  });
  seeds.addEventListener("change", () => set({ portfolio: Number(seeds.value) }));
  budget.addEventListener("change", () => {
    const value = typed(budget, 1, BUDGET_MAX_S, false);
    if (value === undefined || value === null) render();
    else set({ budgetS: value });
  });
  tier.addEventListener("change", () => set({ tier: tier.value as Tier }));
  region.addEventListener("change", () => set({ region: region.value || null }));
  leash.addEventListener("change", () => {
    const value = typed(leash, 0.1, LEASH_MAX_KM, false);
    if (value === undefined) render();
    else set({ leashKm: value ?? "auto" });
  });
  for (const input of target.inputs) {
    input.addEventListener("change", () => set({ target: input.value as TargetKind }));
  }
  ratio.addEventListener("change", () => {
    const value = typed(ratio, 1, Number.MAX_SAFE_INTEGER, false);
    if (value === undefined || value === null || value === 1) render();
    else set({ target: "ratio", ratio: value });
  });
  duration.addEventListener("change", () => {
    const seconds = parseDuration(duration.value);
    if (seconds === null || seconds <= 0 || seconds > TARGET_MAX_DURATION_S) render();
    else set({ target: "duration", durationS: seconds });
  });
  // A mode that refuses the target chosen (collect and brand a ratio, the alphabet route any)
  // makes it as bad as possible.
  for (const input of mode.inputs) {
    input.addEventListener("change", () => {
      const objective = input.value as Mode;
      const fits = takesTarget(objective, store.state.settings.target);
      set(fits ? { objective } : { objective, target: "max" });
    });
  }
  item.addEventListener("change", () => set({ item: item.value as CollectItem }));
  brand.addEventListener("change", () => set({ brand: Number(brand.value) }));
  for (const input of turns.inputs) {
    input.addEventListener("change", () => {
      const side = input.value as TurnSetting;
      set(side === "any" ? { turns: side, strict: false } : { turns: side });
    });
  }
  strict.addEventListener("change", () => set({ strict: strict.checked }));
  hard.addEventListener("change", () => set({ hard: hard.checked }));
  reorder.addEventListener("change", () => set({ reorder: reorder.checked }));
  certified.addEventListener("change", () => set({ certified: certified.checked }));
  mpg.addEventListener("change", () => {
    const value = typed(mpg, MPG_MIN, MPG_MAX, false);
    if (value === undefined || value === null) render();
    else set({ mpg: value });
  });
  gasPrice.addEventListener("change", () => {
    const value = typed(gasPrice, GAS_PRICE_MIN, GAS_PRICE_MAX, false);
    if (value === undefined || value === null) render();
    else set({ gasPriceUsd: value });
  });
  // A time typed is the browser's local time; one that came with a link keeps its own offset
  // until it is changed.
  departure.addEventListener("change", () => {
    const at = new Date(departure.value);
    if (departure.value === "") set({ departure: null });
    else if (Number.isNaN(at.getTime())) render();
    else set({ departure: isoWithOffset(at) });
  });
  for (const input of units.inputs) {
    input.addEventListener("change", () => set({ units: input.value as Units }));
  }

  store.watch(["settings"], render);
  // The row an error's `field` names, highlighted while the error is shown.
  const fieldRows = new Map<unknown, HTMLElement>([
    ["item", modeRow],
    ["brand", modeRow],
    ["constraints", turnsRow],
    ["reorder", turnsRow],
    ["certified", turnsRow],
    ["leash", leashRow],
    ["portfolio", seedsRow],
    ["target", targetRow],
    ["vehicle", vehicleRow],
    ["departure", departureRow],
  ]);
  store.watch(["error"], ({ error }) => {
    const errored = error?.code === "PORTFOLIO_TOO_LARGE" ? seedsRow : fieldRows.get(error?.field);
    for (const row of fieldRows.values()) row.classList.toggle("has-error", row === errored);
  });
  let shown: unknown[] = [];
  const lock = (state: Readonly<AppState>) => {
    const locked = busy(store);
    const { resolved } = state.run;
    const { brands } = state;
    const { objective, turns: side, hard: hardMode, reorder: reordering } = state.settings;
    const { portfolio, certified: certifying } = state.settings;
    const key = [
      locked,
      resolved,
      state.settings.tier,
      state.settings.leashKm,
      objective,
      side,
      hardMode,
      reordering,
      portfolio,
      certifying,
      brands,
    ];
    if (key.every((value, k) => value === shown[k])) return;
    shown = key;
    for (const control of [seed, budget, tier, leash, region, item, mpg, gasPrice, departure]) {
      control.disabled = locked;
    }
    brand.disabled = locked || brands === null;
    // Certified counts under the longest only (the box keeps its tick under another mode), and
    // a certified route takes no target, turn constraint, hard mode, worst order or second seed:
    // their controls are disabled, keeping their values for when it is unticked.
    const certifiedRun = certifying && objective === "distance";
    for (const input of mode.inputs) input.disabled = locked;
    for (const input of turns.inputs) input.disabled = locked || certifiedRun;
    mode.inputs[3].disabled = locked || brands?.length === 0;
    mode.inputs[3].parentElement?.classList.toggle("disabled", brands?.length === 0);
    target.inputs.forEach((input) => {
      const refused =
        !takesTarget(objective, input.value as TargetKind) ||
        (certifiedRun && input.value !== "max");
      input.disabled = locked || refused;
      input.parentElement?.classList.toggle("disabled", refused);
    });
    ratio.disabled = target.inputs[1].disabled;
    duration.disabled = target.inputs[2].disabled;
    const targetNoteText = certifiedRun ? NO_TARGET : TARGET_NOTES[objective];
    targetNote.textContent = targetNoteText ?? "";
    targetNote.hidden = targetNoteText === undefined;
    strict.disabled = locked || side === "any" || certifiedRun;
    strictLabel.classList.toggle("disabled", side === "any" || certifiedRun);
    strictNote.hidden = side === "any" || certifiedRun;
    hard.disabled = locked || certifiedRun;
    hardLabel.classList.toggle("disabled", certifiedRun);
    hardNote.hidden = !hardMode || certifiedRun;
    // The alphabet route has no stop to reorder: the box keeps its tick, which counts again
    // under another mode.
    const alphabet = objective === "alphabet";
    reorder.disabled = locked || alphabet || certifiedRun;
    reorderLabel.classList.toggle("disabled", alphabet || certifiedRun);
    reorderNote.hidden = !reordering || alphabet || certifiedRun;
    certified.disabled = locked || objective !== "distance";
    certifiedLabel.classList.toggle("disabled", objective !== "distance");
    certifiedNote.hidden = !certifiedRun;
    // nor does it run more than one search: the seeds keep their number for another mode
    const oneSearch = alphabet || certifiedRun;
    seeds.disabled = locked || oneSearch;
    seedsNote.textContent = alphabet ? ONE_SEARCH : certifiedRun ? ONE_CERTIFIED : SEEDS;
    seedsNote.hidden = portfolio < 2 && !oneSearch; // the reason whenever it is disabled
    tierNote.textContent =
      resolved && state.settings.tier === "auto"
        ? `auto was ${ROADS[tierIndex(resolved.tier)]}`
        : "";
    leashNote.textContent =
      resolved && state.settings.leashKm === "auto"
        ? `${CORRIDOR}; auto was ${resolved.leashKm.toFixed(resolved.leashKm < 1 ? 2 : 1)} km`
        : CORRIDOR;
  };
  store.watch(["run", "settings", "brands"], lock);
  store.watch(["brands"], ({ brands }) => {
    if (brands !== null) brand.replaceChildren(...select(brandOptions(brands)).options);
    render();
  });
  // The brands of the region trips run on: the picker's options once GET /brands has answered
  // ("loading…" until then, the picker disabled, and under Brand Go waits); a region without any
  // has no Brand mode.
  followBrands(store, (text) => brand.replaceChildren(...select([["", text, true]]).options));
  store.watch(["regions"], ({ regions }) => {
    region.replaceChildren(...select(regionOptions(regions ?? [])).options);
    regionRow.hidden = (regions?.length ?? 0) < 2;
    render();
  });
  // The seeds this server runs, once it has said (a link's kept until then, the row hidden).
  const offer = (most: number) => {
    seeds.replaceChildren(...select(seedOptions(most)).options);
    seedsRow.hidden = most < 2;
    render();
  };
  seeds.replaceChildren(...select(seedOptions(MAX_PORTFOLIO)).options);
  render();
  void followPortfolioMax(store, portfolioMax).then(offer);
}
