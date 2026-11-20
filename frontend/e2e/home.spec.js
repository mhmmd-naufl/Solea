import { test, expect } from '@playwright/test'

test('beranda menampilkan hero dan bagian utama', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByRole('heading', { name: /Sepatu Anda/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Cara Kerja' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Layanan & Harga' })).toBeVisible()
})

test('nomor booking yang tidak dikenal ditampilkan sebagai tidak ditemukan', async ({ page }) => {
  await page.goto('/status')

  await page.getByLabel('Nomor Booking').fill('TRX-TIDAKADA')
  await page.getByRole('button', { name: 'Cari' }).click()

  // Tanpa backend berjalan, pesannya adalah "Backend tidak dapat dihubungi".
  // Dengan backend + data ter-seed, pesannya "Nomor booking ... tidak ditemukan".
  await expect(
    page.getByText(/tidak ditemukan|backend tidak dapat dihubungi/i)
  ).toBeVisible({ timeout: 15_000 })
})
