import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { registerFonts } from "./dashboard/fonts";
import "./app.css";
import "./components/sidebar.css";

registerFonts();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
