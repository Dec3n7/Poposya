import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { initGlass } from "./glass";
import "./styles.css";

initGlass();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
