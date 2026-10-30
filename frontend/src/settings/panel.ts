// The settings (docs/reference/frontend.md, **Settings**): the request's seed (empty: random,
// picked by the server), time budget, tier and leash (empty: auto, with what auto became once a
// job has said), the inefficiency target (as bad as possible, a ratio to the sensible route, or
// a drive time typed as `9h`, `90m`, `2h30m` or minutes; typing a number picks its kind), the
// objective ("longest distance" is built; the others are shown, disabled), and the units the
// panel shows. Locked while a job runs, except the units.
import type { Objective, TargetKind, Tier } from "../api/types";
import { formatDurationInput, parseDuration, type Units } from "../format";
import {
  BUDGET_MAX_S,
  LEASH_MAX_KM,
  TARGET_MAX_DURATION_S,
  TIERS,
  type Settings,
  type Store,
} from "../state";
import { busy } from "../stops/stopList";
import { el } from "../util/dom";

const TARGETS: [TargetKind, string, boolean][] = [
  ["max", "As bad as possible", true],
  ["ratio", "A ratio", true],
  ["duration", "A drive time", true],
];
const OBJECTIVES: [Objective, string, boolean][] = [
  ["distance", "Longest distance", true],
  ["time", "Slowest time — soon", false],
  ["collect", "Collect things — soon", false],
  ["brand", "Brand pilgrimage — soon", false],
  ["alphabet", "Alphabet route — soon", false],
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
  const objective = select(OBJECTIVES);
  const units = radios(
    "units",
    UNITS.map(([u, text]): [Units, string, boolean] => [u, text, true]),
  );

  root.replaceChildren(
    el("h2", "", "Settings"),
    field("Seed", seed),
    field("Time budget (s)", budget),
    field("Road tier", tier, tierNote),
    field("Leash (km)", leash, leashNote),
    field("How bad", target.group),
    field("Make it", objective),
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
    objective.value = settings.objective;
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
  objective.addEventListener("change", () => set({ objective: objective.value as Objective }));
  for (const input of units.inputs) {
    input.addEventListener("change", () => set({ units: input.value as Units }));
  }

  store.watch(["settings"], render);
  let shown: unknown[] = [];
  store.watch(["run", "settings"], (state) => {
    const locked = busy(store);
    const { resolved } = state.run;
    const key = [locked, resolved, state.settings.tier, state.settings.leashKm];
    if (key.every((value, k) => value === shown[k])) return;
    shown = key;
    for (const control of [seed, budget, tier, leash, ratio, duration, objective]) {
      control.disabled = locked;
    }
    for (const input of target.inputs) input.disabled = locked;
    tierNote.textContent =
      resolved && state.settings.tier === "auto" ? `auto was ${resolved.tier}` : "";
    leashNote.textContent =
      resolved && state.settings.leashKm === "auto"
        ? `auto was ${resolved.leashKm.toFixed(1)} km`
        : "";
  });
}
