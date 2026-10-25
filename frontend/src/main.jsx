import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App.jsx";
import PullToRefresh from "./PullToRefresh.jsx";
import "./App.css";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <PullToRefresh />
    <App />
  </StrictMode>
);
