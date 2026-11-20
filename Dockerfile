# Dockerfile untuk deploy Solea backend ke Fly.io
#
# Cara pakai di Fly CLI:
#   1. fly launch   (baca fly.toml, buat app, tanyakan nama)
#   2. fly secrets set SUPABASE_SERVICE_ROLE_KEY=<isi dari Supabase>
#   3. fly deploy   (build & deploy Dockerfile ini)
#
# Fly.io pakai Docker build. Image base: python:3.12-slim.

FROM python:3.12-slim

# Set working dir
WORKDIR /app

# Install dependencies (cache layer: hanya re-run kalau requirements.txt berubah)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy seluruh source code
COPY . .

# Port yang diminta oleh fly.toml [http_service].internal_port
EXPOSE 8080

# Jalankan uvicorn. Env vars (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
# JWT_SECRET_KEY, APP_ENV, BUSINESS_NAME, FRONTEND_ORIGINS) dibaca dari
# fly secrets / fly config, bukan dari Dockerfile.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
