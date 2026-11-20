import { test, expect } from '@playwright/test'

test('form login menolak input kosong', async ({ page }) => {
  await page.goto('/login')

  await page.locator('form').getByRole('button', { name: 'Masuk' }).click()

  await expect(page.getByText('Nomor WhatsApp wajib diisi')).toBeVisible()
})

// Butuh backend + akun admin yang sudah di-seed. Isi keduanya di environment
// supaya test ini ikut jalan di CI/staging; tanpa itu test dilewati.
const adminPhone = process.env.E2E_ADMIN_PHONE
const adminPassword = process.env.E2E_ADMIN_PASSWORD

test('admin bisa login dan membuka dashboard', async ({ page }) => {
  test.skip(!adminPhone || !adminPassword, 'Butuh E2E_ADMIN_PHONE dan E2E_ADMIN_PASSWORD')

  await page.goto('/login')
  await page.locator('#phone').fill(adminPhone)
  await page.locator('#password').fill(adminPassword)
  await page.locator('form').getByRole('button', { name: 'Masuk' }).click()

  await expect(page).toHaveURL(/\/admin/)
  await expect(page.getByText('Dashboard').first()).toBeVisible()
})
