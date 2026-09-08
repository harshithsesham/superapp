import { defineConfig } from "vitest/config";
export default defineConfig({
  resolve: { alias: { "react-native": "react-native-web" } },
  test: { environment: "jsdom", include: ["src/**/*.test.tsx"], setupFiles: ["./src/test-setup.ts"], pool: "forks", maxWorkers: 1 },
});
