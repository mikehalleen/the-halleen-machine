import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/tokens.css";
import "./styles/base.css";
import { trackVisualViewport } from "./layout/viewport";

// Theme attributes on <body>, read by tokens.css alone: dark / cyan / charcoal / outlined are
// the defaults. Set `data-theme="light"` for the light override; nothing in the app reads these.
Object.assign(document.body.dataset, { theme: "dark", scheme: "cyan", tone: "charcoal", edge: "outlined" });

// the shell follows the visual viewport so the composer clears the phone keyboard (see viewport.ts)
trackVisualViewport();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
