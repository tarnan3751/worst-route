// The place search (docs/reference/frontend.md, **Stops**): a text field and a Search button at
// the top of the stop list. Nothing is asked while typing (Nominatim's policy forbids
// type-ahead): the button or Enter sends the text to GET /geocode, which the server caches and
// rate-limits. Up to five places are listed under the field; picking one places the next stop
// as a click on the map does (the `place` it is given: stopList.ts, placeStop), then clears the
// field. An error goes to the banner like every API error (RATE_LIMITED's message is the
// server's: "the geocoder is busy, try again in a few seconds"), and a search closes the banner
// first. Locked, like the stops, while a job runs, and until /regions has named the region.
//
// `PlaceSearch` holds the logic and no DOM, so the tests drive it (search.test.ts);
// `mountPlaceSearch` draws it and wires the form.
import { errorBody, geocode } from "../api/client";
import type { GeocodeCandidate } from "../api/types";
import type { Store } from "../state";
import { el } from "../util/dom";
import { busy } from "./stopList";

export class PlaceSearch extends EventTarget {
  text = "";
  candidates: GeocodeCandidate[] = [];
  searching = false;
  searched = false; // the last search answered: with nothing, "No places found"
  readonly #store: Store;
  readonly #place: (lat: number, lon: number) => boolean;

  /** `place` places the next stop at (lat, lon), as a map click does; whether it did. */
  constructor(store: Store, place: (lat: number, lon: number) => boolean) {
    super();
    this.#store = store;
    this.#place = place;
  }

  /** Whether the Search button would send anything now. */
  get canSearch(): boolean {
    const { region } = this.#store.state;
    return this.text.trim() !== "" && !this.searching && region !== null && !busy(this.#store);
  }

  /** The field's text; nothing is asked. */
  setText(text: string): void {
    this.text = text;
    this.#changed();
  }

  /** Ask the server for the places the text may mean (the button, or Enter). */
  async search(): Promise<void> {
    const { region } = this.#store.state;
    if (!this.canSearch || region === null) return;
    this.searching = true;
    this.searched = false;
    this.candidates = [];
    this.#store.update({ error: null });
    this.#changed();
    try {
      this.candidates = (await geocode(this.text, region.region)).candidates;
      this.searched = true;
    } catch (error) {
      this.#store.update({ error: errorBody(error) });
    } finally {
      this.searching = false;
      this.#changed();
    }
  }

  /** Place the next stop at candidate `index`, then clear the field and the list. */
  pick(index: number): void {
    const candidate = this.candidates[index];
    if (candidate === undefined || !this.#place(candidate.lat, candidate.lon)) return;
    this.text = "";
    this.candidates = [];
    this.searched = false;
    this.#changed();
  }

  #changed(): void {
    this.dispatchEvent(new Event("change"));
  }
}

/** The place search in `root`: the field and its button in a form (Enter submits it), the
 * search's state, and its candidates as buttons. */
export function mountPlaceSearch(root: HTMLElement, store: Store, search: PlaceSearch): void {
  const input = el("input", "place-query");
  input.type = "search";
  input.placeholder = "Search for a place";
  input.maxLength = 200; // the server's limit, once normalised
  input.autocomplete = "off";
  input.setAttribute("aria-label", "Place to search for");
  const button = el("button", "", "Search");
  button.type = "submit";
  const form = el("form", "place-search");
  form.append(input, button);
  const status = el("p", "place-status");
  const results = el("ul", "place-results");
  root.replaceChildren(form, status, results);

  input.addEventListener("input", () => search.setText(input.value));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void search.search();
  });

  let shown: unknown[] = [];
  const render = (): void => {
    const locked = busy(store) || store.state.region === null;
    const key = [search.text, search.candidates, search.searching, search.searched, locked];
    if (key.every((value, k) => value === shown[k])) return;
    shown = key;
    if (input.value !== search.text) input.value = search.text;
    input.disabled = locked;
    button.disabled = !search.canSearch;
    const empty = search.searched && search.candidates.length === 0;
    status.textContent = search.searching ? "Searching…" : empty ? "No places found." : "";
    status.hidden = status.textContent === "";
    results.replaceChildren(
      ...search.candidates.map((candidate, k) => {
        const pick = el("button", "place", candidate.label);
        pick.type = "button";
        pick.title = candidate.category;
        pick.disabled = locked;
        pick.addEventListener("click", () => search.pick(k));
        const item = el("li");
        item.append(pick);
        return item;
      }),
    );
  };
  search.addEventListener("change", render);
  store.watch(["region", "run"], render);
}
