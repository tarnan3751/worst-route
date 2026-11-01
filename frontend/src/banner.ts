// The error banner (docs/reference/frontend.md, **Errors**): every API error's message as a
// sentence (`sentence`), its code in the banner's tooltip, over the map, until closed or the next
// action; never modal. A strict turn constraint that finds no route (INFEASIBLE_TURN_CONSTRAINT)
// offers to try again without strict, and a refusal hard mode is why without hard mode: a leg the
// seed cannot build under it (STOP_UNREACHABLE_WITHOUT_REPEAT, its message ending "Try without
// it."), or a base route that comes back to a road (BASE_ROUTE_MISMATCH, "which hard mode
// refuses"). The API's errors carry no field that says hard mode is why, so the messages' stable
// endings do.
import type { ErrorBody } from "./api/types";
import type { Store } from "./state";
import { el } from "./util/dom";

/** What an error's banner offers to drop and try again: strict, hard mode, or nothing. */
export function retryOf(error: ErrorBody): "strict" | "hard" | null {
  if (error.code === "INFEASIBLE_TURN_CONSTRAINT") return "strict";
  const { code, message } = error;
  if (code === "STOP_UNREACHABLE_WITHOUT_REPEAT" && message.endsWith("Try without it.")) {
    return "hard";
  }
  return code === "BASE_ROUTE_MISMATCH" && message.includes("hard mode") ? "hard" : null;
}

const RETRIES = { strict: "Try without strict", hard: 'Try without "No road twice, ever"' };

/** `message` as the banner says it: its first letter a capital, and a full stop at its end when
 * it ends without one. */
export function sentence(message: string): string {
  const text = message.charAt(0).toUpperCase() + message.slice(1);
  return /[.?!…]$/.test(text) ? text : `${text}.`;
}

/** The banner in `root`; `retry.strict` unticks strict and runs again, `retry.hard` hard mode. */
export function mountBanner(
  root: HTMLElement,
  store: Store,
  retry: Record<"strict" | "hard", () => void>,
): void {
  store.watch(["error"], ({ error }) => {
    root.hidden = error === null;
    root.title = error?.code ?? "";
    if (error === null) {
      root.replaceChildren();
      return;
    }
    const close = el("button", "close", "×");
    close.title = "Dismiss";
    close.addEventListener("click", () => store.update({ error: null }));
    // a refusal without a message (a proxy's empty 502) says its code instead
    const text = error.message === "" ? error.code : sentence(error.message);
    root.replaceChildren(el("span", "", text), close);
    const without = retryOf(error);
    if (without !== null) {
      const button = el("button", "retry", RETRIES[without]);
      button.addEventListener("click", retry[without]);
      close.before(button);
    }
  });
}
