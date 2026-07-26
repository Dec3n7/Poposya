import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { ToastProvider } from "./components/Toast";
import { initGlass } from "./glass";
import "./styles.css";

initGlass();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ToastProvider>
      <App />
    </ToastProvider>
  </StrictMode>,
);
