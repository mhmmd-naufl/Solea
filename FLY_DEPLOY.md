# Fly.io — Panduan Deploy (Backend)
#
# Ini panduan STEP-BY-STEP. Ikuti urutannya.

## 1. Install Fly CLI
- Windows: `winget install Fly.flyctl`
- Verifikasi: `fly version`

## 2. Login
```bash
fly auth login
```
(Buka browser → authorize → terminal.)

## 3. Buat App Fly.io
```bash
fly apps create solea-backend
```
(Fly akan tanya region — pilih `sin` untuk Singapore.)

## 4. Set Secrets (wajib)
```bash
fly secrets set SUPABASE_SERVICE_ROLE_KEY="<paste dari Supabase Dashboard>"
```
Ambil key dari: Supabase → Project Settings → API → `service_role` (Secret).
**Jangan** paste key di chat / commit ke Git.

## 5. Deploy
```bash
fly deploy
```
Tunggu ~1-2 menit. Fly akan:
- build Docker image dari `Dockerfile` di repo
- install `requirements.txt`
- push ke registry Fly
- update VM shared-CPU 256MB

## 6. Cek Status
```bash
fly status
fly logs
```

## 7. Copy Public URL
Dari `fly status`, ada `Public URL: https://solea-backend.fly.dev`
Copy URL-nya.

## 8. Connect Frontend
Edit `frontend/.env`:
```env
VITE_API_BASE_URL=https://solea-backend.fly.dev
```
Restart frontend: `npm run dev`
Buka `http://localhost:3000` → **demo hidup**.

## 9. (Opsional) Auto-Deploy dari GitHub
Repo sudah ada workflow `.github/workflows/fly-deploy.yml`
yang deploy tiap `push` ke `main`.

Untuk aktifkan:
1. Buka repo GitHub → Settings → Secrets & variables → Actions
2. Tambah secret: `SUPABASE_SERVICE_ROLE_KEY` = `<isi dari Supabase>`
3. Tambah secret: `FLY_API_TOKEN` = token Fly CLI (`fly auth token`)
4. Setelah push, GitHub akan auto-build & deploy.

## Update `fly.toml` (jika perlu)
File `fly.toml` sudah ada di root repo. Kalau mau ganti region/VM:
```bash
fly open   # buka dashboard web
```
Edit di dashboard, atau `fly config edit` (buka editor).

## Biaya
Free tier Fly.io:
- 1 VM 256MB shared-CPU
- 3 GB bandwidth / bulan
- 1800 jam / bulan
- **Tanpa kartu kredit**
Jika app idle > 15 menit, Fly auto-suspend (hemat). Wake-up ~30 detik.

## Kalau Error
- Build gagal: cek log `fly deploy` (muncul di terminal).
- App crash: cek `fly logs` — biasanya karena `SUPABASE_SERVICE_ROLE_KEY` belum di-set.
- CORS: pastikan `FRONTEND_ORIGINS` di `fly.toml` [env] sudah include origin frontend-mu.
