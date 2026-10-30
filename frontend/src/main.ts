// Bootstrap (docs/reference/frontend.md).
import "./style.css";
import { createMap } from "./map/map";

createMap(document.getElementById("map") as HTMLElement);
