import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./auth/AuthContext";
import { initAppearance } from "./lib/appearance";
import "./styles/tokens.css";
import "./styles/app.css";
import "./styles/shell.css";

// apply any browser-local colour-token override before the first paint
initAppearance();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
