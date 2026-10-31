// The error banner (docs/reference/frontend.md, **Errors**): every API error's code and message,
// over the map, until closed or the next action; never modal. A strict turn constraint that
// finds no route (INFEASIBLE_TURN_CONSTRAINT) offers to try again without strict, and a refusal
// hard mode is why without hard mode: a leg the seed cannot build under it
// (STOP_UNREACHABLE_WITHOUT_REPEAT, "under hard mode"), or a base route that comes back to a road
// (BASE_ROUTE_MISMATCH, "which hard mode refuses").
import type { ErrorBody } from "./api/types";
import type { Store } from "./state";
import { el } from "./util/dom";

/** What an error's banner offers to drop and try again: strict, hard mode, or nothing. */
export function retryOf(error: ErrorBody): "strict" | "hard" | null {
  if (error.code === "INFEASIBLE_TURN_CONSTRAINT") return "strict";
  const { code, message } = error;
  if (code === "STOP_UNREACHABLE_WITHOUT_REPEAT" && message.includes("under hard mode")) {
    return "hard";
  }
  return code === "BASE_ROUTE_MISMATCH" && message.includes("hard mode") ? "hard" : null;
}

const RETRIES = { strict: "Try without strict", hard: "Try without hard mode" };

/** The banner in `root`; `retry.strict` unticks strict and runs again, `retry.hard` hard mode. */
export function mountBanner(
  root: HTMLElement,
  store: Store,
  retry: Record<"strict" | "hard", () => void>,
): void {
  store.watch(["error"], ({ error }) => {
    root.hidden = error === null;
    if (error === null) {
      root.replaceChildren();
      return;
    }
    const close = el("button", "close", "×");
    close.title = "Dismiss";
    close.addEventListener("click", () => store.update({ error: null }));
    root.replaceChildren(el("strong", "", error.code), el("span", "", error.message), close);
    const without = retryOf(error);
    if (without !== null) {
      const button = el("button", "retry", RETRIES[without]);
      button.addEventListener("click", retry[without]);
      close.before(button);
    }
  });
}
