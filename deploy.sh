#!/usr/bin/env bash
# deploy.sh — helper deploy Solea backend ke Fly.io
#
# Pakai (dari folder root repo, terminal bash/git-bash/WSL):
#   ./deploy.sh
#
# Prereq:
#   - Fly CLI terinstall & login (`fly auth login`)
#   - SUPABASE_SERVICE_ROLE_KEY sudah di-set (pakai `fly secrets set SUPABASE_SERVICE_ROLE_KEY=...`)
#
# Ini hanya convenience script. Kalau mau manual, cukup:
#   fly deploy

set -euo pipefail

echo "==> Cek login Fly.io..."
fly auth whoami || { echo "Belum login. Jalankan: fly auth login"; exit 1; }

echo "==> Cek secret SUPABASE_SERVICE_ROLE_KEY..."
if ! fly secrets | grep -q SUPABASE_SERVICE_ROLE_KEY; then
  echo "  SUPABASE_SERVICE_ROLE_KEY belum ada di secrets."
  echo "  Jalankan dulu: fly secrets set SUPABASE_SERVICE_ROLE_KEY=<paste dari Supabase>"
  exit 1
fi

echo "==> Deploy (build Docker + push + update)..."
fly deploy --strategy rolling

echo ""
echo "==> Cek URL public..."
fly status
echo ""
echo "Selesai. Copy URL public, lalu isi di frontend/.env:"
echo "  VITE_API_BASE_URL=https://<nama-app>.fly.dev"
