import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served from https://<user>.github.io/pokemon-card-tracker/ on GitHub
// Pages, so assets need to resolve under that subpath rather than root.
export default defineConfig({
  base: "/pokemon-card-tracker/",
  plugins: [react()],
});
