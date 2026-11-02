// The themes the settings offer, in their order (docs/reference/frontend.md, **Themes**).
import type { Theme } from "../theme";
import { classic } from "./classic";
import { studio } from "./studio";

export const THEMES: readonly Theme[] = [studio, classic];
