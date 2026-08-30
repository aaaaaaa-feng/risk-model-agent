import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { AppErrorBoundary } from "./components/AppErrorBoundary";
import { initializeLocalSession } from "./api";
import "./styles/index.css";

async function bootstrap() {
  try {
    await initializeLocalSession();
  } catch {
    // The regular API error states remain available when the local service is down.
  }
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <AppErrorBoundary>
        <App />
      </AppErrorBoundary>
    </React.StrictMode>,
  );
}

void bootstrap();
