import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { registerFonts } from "./dashboard/fonts";
import "./app.css";

registerFonts();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
