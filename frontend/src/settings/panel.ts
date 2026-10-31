// The settings (docs/reference/frontend.md, **Settings**): the request's seed (empty: random,
// picked by the server), time budget, tier and leash (empty: auto, with what auto became once a
// job has said), the mode (the longest, the slowest, or collecting an item, picked under it),
// the turns (any, or left or right turns avoided, `strict` for none at all), the inefficiency
// target (as bad as possible, a ratio to the sensible route, or a drive time typed as `9h`,
// `90m`, `2h30m` or minutes; typing a number picks its kind; no ratio under collect), and the
// units the panel shows. Locked while a job runs, except the units. The setting an error names
// (`field`: the target, the item or the constraints) is highlighted.
import type { CollectItem, TargetKind, Tier } from "../api/types";
import { ITEM_NAMES, formatDurationInput, parseDuration, type Units } from "../format";
import {
  BUDGET_MAX_S,
  ITEMS,
  LEASH_MAX_KM,
  TARGET_MAX_DURATION_S,
  TIERS,
  type Mode,
  type Settings,
  type Store,
  type TurnSetting,
} from "../state";
import { busy } from "../stops/stopList";
import { el } from "../util/dom";

const TARGETS: [TargetKind, string, boolean][] = [
  ["max", "As bad as possible", true],
  ["ratio", "A ratio", true],
  ["duration", "A drive time", true],
];
const MODE_OPTIONS: [Mode, string, boolean][] = [
  ["distance", "Longest", true],
  ["time", "Slowest", true],
  ["collect", "Collect", true],
];
const TURNS: [TurnSetting, string, boolean][] = [
  ["any", "Any", true],
  ["no_left", "Avoid left turns", true],
  ["no_right", "Avoid right turns", true],
];
const UNITS: [Units, string][] = [
  ["mi", "Miles"],
  ["km", "Kilometres"],
];

/** A number typed in `input`: null when empty, undefined when not a number in [min, max]. */
function typed(input: HTMLInputElement, min: number, max: number, integer: boolean) {
  if (input.value.trim() === "") return null;
  const value = Number(input.value);
  const ok = integer ? Number.isSafeInteger(value) : Number.isFinite(value);
  return ok && value >= min && value <= max ? value : undefined;
}

/** A labelled row; a group of radios is a `div`, since labels do not nest. */
function field(label: string, control: HTMLElement, note?: HTMLElement): HTMLElement {
  const row = el(control.classList.contains("radios") ? "div" : "label", "field");
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

export function mountSettings(root: HTMLElement, store: Store): void {
  const seed = numberInput("random", 0, Number.MAX_SAFE_INTEGER, "1");
  const budget = numberInput("", 1, BUDGET_MAX_S, "1");
  const tier = select(TIERS.map((t): [Tier, string, boolean] => [t, t, true]));
  const tierNote = el("span", "resolved");
  const leash = numberInput("auto", 0.1, LEASH_MAX_KM, "0.1");
  const leashNote = el("span", "resolved");
  const target = radios("target", TARGETS);
  const ratio = numberInput("20", 1, Number.MAX_SAFE_INTEGER, "any");
  const duration = el("input", "duration");
  Object.assign(duration, { type: "text", placeholder: "9h" });
  target.inputs[1].parentElement?.append(" ", ratio, " ×");
  target.inputs[2].parentElement?.append(" ", duration);
  const targetNote = el("span", "resolved", "a collect run takes a drive-time target only");
  const mode = radios("mode", MODE_OPTIONS);
  const item = select(
    ITEMS.map((i): [CollectItem, string, boolean] => [i, ITEM_NAMES[i][1], true]),
  );
  mode.inputs[2].parentElement?.append(" ", item);
  const turns = radios("turns", TURNS);
  const strict = el("input");
  strict.type = "checkbox";
  const strictLabel = el("label");
  strictLabel.append(strict, " strict");
  turns.group.append(strictLabel);
  const strictNote = el(
    "span",
    "resolved",
    "strict rarely finds a route on a street grid: the route may not exist",
  );
  const units = radios(
    "units",
    UNITS.map(([u, text]): [Units, string, boolean] => [u, text, true]),
  );

  const modeRow = field("Mode", mode.group);
  const turnsRow = field("Turns", turns.group, strictNote);
  const targetRow = field("How bad", target.group, targetNote);

  root.replaceChildren(
    el("h2", "", "Settings"),
    field("Seed", seed),
    field("Time budget (s)", budget),
    field("Road tier", tier, tierNote),
    field("Leash (km)", leash, leashNote),
    modeRow,
    turnsRow,
    targetRow,
    field("Units", units.group),
  );

  const set = (patch: Partial<Settings>) => store.updateSettings(patch);
  const render = () => {
    const { settings } = store.state;
    seed.value = settings.seed === null ? "" : String(settings.seed);
    budget.value = String(settings.budgetS);
    tier.value = settings.tier;
    leash.value = settings.leashKm === "auto" ? "" : String(settings.leashKm);
    for (const input of target.inputs) input.checked = input.value === settings.target;
    ratio.value = String(settings.ratio);
    duration.value = formatDurationInput(settings.durationS);
    for (const input of mode.inputs) input.checked = input.value === settings.objective;
    item.value = settings.item;
    item.hidden = settings.objective !== "collect";
    for (const input of turns.inputs) input.checked = input.value === settings.turns;
    strict.checked = settings.strict;
    for (const input of units.inputs) input.checked = input.value === settings.units;
  };

  seed.addEventListener("change", () => {
    const value = typed(seed, 0, Number.MAX_SAFE_INTEGER, true);
    if (value === undefined) render();
    else set({ seed: value });
  });
  budget.addEventListener("change", () => {
    const value = typed(budget, 1, BUDGET_MAX_S, false);
    if (value === undefined || value === null) render();
    else set({ budgetS: value });
  });
  tier.addEventListener("change", () => set({ tier: tier.value as Tier }));
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
  // Collect takes no ratio: picking it from a ratio target makes the target as bad as possible.
  for (const input of mode.inputs) {
    input.addEventListener("change", () => {
      const objective = input.value as Mode;
      const ratioOut = objective === "collect" && store.state.settings.target === "ratio";
      set(ratioOut ? { objective, target: "max" } : { objective });
    });
  }
  item.addEventListener("change", () => set({ item: item.value as CollectItem }));
  for (const input of turns.inputs) {
    input.addEventListener("change", () => {
      const side = input.value as TurnSetting;
      set(side === "any" ? { turns: side, strict: false } : { turns: side });
    });
  }
  strict.addEventListener("change", () => set({ strict: strict.checked }));
  for (const input of units.inputs) {
    input.addEventListener("change", () => set({ units: input.value as Units }));
  }

  store.watch(["settings"], render);
  // The row an error's `field` names, highlighted while the error is shown.
  const fieldRows = new Map<unknown, HTMLElement>([
    ["item", modeRow],
    ["constraints", turnsRow],
    ["target", targetRow],
  ]);
  store.watch(["error"], ({ error }) => {
    const errored = fieldRows.get(error?.field);
    for (const row of fieldRows.values()) row.classList.toggle("has-error", row === errored);
  });
  let shown: unknown[] = [];
  store.watch(["run", "settings"], (state) => {
    const locked = busy(store);
    const { resolved } = state.run;
    const { objective, turns: side } = state.settings;
    const key = [locked, resolved, state.settings.tier, state.settings.leashKm, objective, side];
    if (key.every((value, k) => value === shown[k])) return;
    shown = key;
    for (const control of [seed, budget, tier, leash, duration, item]) {
      control.disabled = locked;
    }
    for (const input of [...target.inputs, ...mode.inputs, ...turns.inputs]) {
      input.disabled = locked;
    }
    const collecting = objective === "collect";
    ratio.disabled = target.inputs[1].disabled = locked || collecting;
    target.inputs[1].parentElement?.classList.toggle("disabled", collecting);
    targetNote.hidden = !collecting;
    strict.disabled = locked || side === "any";
    strictLabel.classList.toggle("disabled", side === "any");
    strictNote.hidden = side === "any";
    tierNote.textContent =
      resolved && state.settings.tier === "auto" ? `auto was ${resolved.tier}` : "";
    leashNote.textContent =
      resolved && state.settings.leashKm === "auto"
        ? `auto was ${resolved.leashKm.toFixed(1)} km`
        : "";
  });
}
