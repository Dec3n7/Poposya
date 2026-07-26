import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ToastProvider } from "./components/Toast";
import { initGlass } from "./glass";
import "./styles.css";

initGlass();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary center>
      <ToastProvider>
        <App />
      </ToastProvider>
    </ErrorBoundary>
  </StrictMode>,
);
