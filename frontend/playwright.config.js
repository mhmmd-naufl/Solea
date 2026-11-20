import { defineConfig, devices } from '@playwright/test'

// E2E dijalankan terhadap frontend yang sudah jalan. Set E2E_BASE_URL kalau
// memakai server sendiri; kalau tidak, Playwright menyalakan Vite dev server.
const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:3000'
const pakaiServerSendiri = Boolean(process.env.E2E_BASE_URL)

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: 'list',
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: pakaiServerSendiri
    ? undefined
    : {
        command: 'npm run dev -- --host 127.0.0.1',
        url: baseURL,
        reuseExistingServer: true,
        timeout: 120_000,
      },
})
