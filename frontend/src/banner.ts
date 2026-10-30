// The error banner (docs/reference/frontend.md, **Errors**): every API error's code and message,
// over the map, until closed or the next action; never modal.
import type { Store } from "./state";
import { el } from "./util/dom";

export function mountBanner(root: HTMLElement, store: Store): void {
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
  });
}
