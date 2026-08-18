import { render } from "preact";
import { App } from "./app";
// Imported for its side effect: theme.ts reads the stored choice and writes
// data-theme on <html> at module evaluation, before Preact renders anything.
// Any later and a student who chose light gets a dark frame on every load.
import "./theme";
import "./styles.css";

const root = document.getElementById("app");
if (root) render(<App />, root);
