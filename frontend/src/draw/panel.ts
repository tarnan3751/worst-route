// The drawing's section of the panel (docs/reference/frontend.md, **Drawing**): what to do next,
// the drawing's length and stops (its ratio once done), the engine's par as it plans, the verdict
// once both are done, and Undo, Finish and Leave while drawing; once finished, the drawing's route
// and the engine's to show. Hidden while there is no drawing; scrolled into view when it appears
// and when the verdict comes. The logic is draw/drawing.ts's.
import type { Store } from "../state";
import { el } from "../util/dom";
import { type Drawing, parLine, progressLine, verdictLine, youLine } from "./drawing";

/** The drawing's section in `root`. */
export function mountDraw(root: HTMLElement, drawing: Drawing, store: Store): void {
  const button = (text: string, className: string, action: () => unknown) => {
    const b = el("button", className, text);
    b.type = "button";
    b.addEventListener("click", () => void action());
    return b;
  };
  const progress = el("p", "draw-progress");
  const you = el("p", "draw-you");
  const par = el("p", "draw-par");
  const verdict = el("p", "draw-verdict");
  const keys = el("p", "hint", "Backspace takes the last road back; Esc leaves drawing mode.");
  const undo = button("Undo", "", () => drawing.undo());
  const finish = button("Finish", "primary", () => drawing.finish());
  const leave = button("Leave", "", () => drawing.leave());
  const drawingButtons = el("div", "buttons");
  drawingButtons.append(undo, finish, leave);
  const mine = button("Your route", "", () => drawing.show("mine"));
  const engine = button("The engine's route", "", () => drawing.show("engine"));
  const shows = el("div", "buttons");
  shows.append(mine, engine);
  root.replaceChildren(
    el("h2", "", "Drawn by hand"),
    progress,
    you,
    par,
    verdict,
    drawingButtons,
    keys,
    shows,
  );

  const render = (): void => {
    const units = store.state.settings.units;
    let scroll = root.hidden && drawing.request !== null; // appearing
    root.hidden = drawing.request === null;
    progress.textContent = progressLine(drawing);
    you.textContent = youLine(drawing, units);
    par.textContent = parLine(drawing.par, units);
    const said = verdictLine(drawing, units);
    scroll ||= said !== "" && verdict.textContent === "";
    verdict.textContent = said;
    drawingButtons.hidden = !drawing.active;
    keys.hidden = !drawing.active;
    undo.disabled = !drawing.canUndo;
    finish.disabled = !drawing.canFinish;
    leave.disabled = drawing.pending;
    shows.hidden = drawing.finished === null;
    engine.disabled = drawing.par?.routeId == null || drawing.showing === "engine";
    mine.disabled = drawing.showing === "mine";
    if (scroll) root.scrollIntoView({ block: "end" });
  };
  drawing.addEventListener("change", render);
  store.watch(["settings"], render);
}
