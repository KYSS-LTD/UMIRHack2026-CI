import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles/layers.css";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/shell.css";
import "./styles/nav.css";
import "./styles/desk.css";
import "./styles/thread.css";
import "./styles/stage.css";
import "./styles/views.css";
import "./styles/account.css";
import "./styles/overlays.css";
import "./styles/polish.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
