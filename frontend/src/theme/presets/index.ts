// The themes the settings offer (docs/reference/frontend.md, **Themes**), in the order the Theme
// row lists them: studio, the default, first; classic, the original look, last.
import type { Theme } from "../theme";
import { arcade } from "./arcade";
import { atlas } from "./atlas";
import { classic } from "./classic";
import { dashboard } from "./dashboard";
import { ledger } from "./ledger";
import { night } from "./night";
import { sign } from "./sign";
import { studio } from "./studio";
import { topo } from "./topo";
import { transit } from "./transit";

export const THEMES: readonly Theme[] = [
  studio,
  night,
  transit,
  topo,
  arcade,
  dashboard,
  atlas,
  sign,
  ledger,
  classic,
];
