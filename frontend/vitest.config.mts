import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: {
    environment: "jsdom",
    include: [
      "tests/unit/**/*.test.{ts,tsx}",
      "tests/component/**/*.test.tsx",
      "tests/contract/**/*.test.ts",
    ],
    setupFiles: ["tests/setup.ts"],
    css: false,
    coverage: {
      provider: "v8",
      include: ["src/lib/**", "src/store/**", "src/components/**", "src/hooks/**"],
      exclude: ["src/lib/contracts/**"],
      reporter: ["text-summary", "json-summary"],
    },
  },
});
