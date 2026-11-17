import { defineConfig } from "@playwright/test";

// 后端 8100 由 make e2e（或手工 uvicorn）拉起；这里只为 Next 进程注入 API 地址。
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8100";

export default defineConfig({
  testDir: "e2e",
  timeout: 180_000,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:3100",
    headless: true,
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev -- -p 3100",
    url: "http://127.0.0.1:3100",
    reuseExistingServer: true,
    timeout: 120_000,
    env: {
      NEXT_PUBLIC_API_BASE: API_BASE_URL,
    },
  },
});