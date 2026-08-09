import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  use: {
    baseURL: process.env.CONCEPT_BRANCH_E2E_BASE_URL || 'http://127.0.0.1:5173',
    headless: true,
  },
  reporter: 'line',
})
