// The error banner (docs/reference/frontend.md, **Errors**): every API error's code and message,
// over the map, until closed or the next action; never modal. A strict turn constraint that
// finds no route (INFEASIBLE_TURN_CONSTRAINT) offers to try again without strict.
import type { Store } from "./state";
import { el } from "./util/dom";

/** The banner in `root`; `withoutStrict` unticks strict and runs again. */
export function mountBanner(root: HTMLElement, store: Store, withoutStrict: () => void): void {
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
    if (error.code === "INFEASIBLE_TURN_CONSTRAINT") {
      const retry = el("button", "retry", "Try without strict");
      retry.addEventListener("click", withoutStrict);
      close.before(retry);
    }
  });
}
