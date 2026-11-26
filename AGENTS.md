# Solea — Catatan untuk Agen

## Git / GitHub
- Remote: `https://github.com/mhmmd-naufl/Solea.git` — akun GitHub: **mhmmd-naufl** (login via `gh auth`, akun aktif).
- **JANGAN** tambahkan atribusi AI di commit message — tidak ada footer "Generated with Devin", tidak ada `Co-Authored-By: Devin`. Commit harus tampak murni dari akun user.
- Commit message mengikuti gaya repo: conventional commits Bahasa Indonesia, mis. `docs(readme): ...`, `fix(ci): ...`.
- Sebelum push: `git pull --rebase origin main` (remote bisa punya commit baru).
- Push langsung ke `main` diperbolehkan (tidak ada branch protection terlihat).

## Project
- Backend: FastAPI + Supabase (Python), frontend React di `frontend/`.
- SQL setup: `SETUP_SOLEA.sql`; deploy Fly.io (`fly.toml`, `Dockerfile`); frontend demo live di Vercel.
- Scripts util di `scripts/` (`screenshot_demo.py`, `serve_spa.py`, `backup_database.py`, dll).

## Preferensi User
- Komunikasi dalam Bahasa Indonesia.
- Jangan buat agen/subagent kecuali diminta.
