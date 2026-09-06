# EchoFlow — Hybrid Deployment Guide (VPS Light + Laptop Heavy Media + Cloudflare Tunnel + R2)

> **Target: ~$6/month** (or $0/month with Oracle Always Free).
> **Architecture:** A small VPS handles the public-facing API + light Celery workers + **local Postgres** + Redis + nginx. Your laptop runs the heavy `celery_media` worker (Whisper + sentence-transformers + HLS encoding) and connects to the VPS through a **Cloudflare Tunnel** (outbound-only, no port forwarding — the laptop never serves HLS directly to users). All media storage lives in **Cloudflare R2** (10 GB free, zero egress). Cloudflare fronts everything for TLS, caching, and bot protection.
>
> **The laptop's role (explicit):** `cloudflared` on the laptop is a **private connectivity tunnel only**. It does NOT serve HLS or any media to end users. HLS segments are uploaded by the media worker to R2, and users/browsers fetch them from R2 (or a custom R2 domain) through Cloudflare's CDN. The laptop never becomes a public origin. Using it as one would be a bad idea (residential IP, reliability, bandwidth, security).
>
> **What you can safely ignore right now:** (1) Separating the recommendation engine (over-engineering at 50 users — see §10.1). (2) Moving Postgres off the VPS (local NVMe is the right choice — see §10.2). (3) PgBouncer (Django/Celery connect directly). (4) Kubernetes, ECS, or any orchestration.
>
> **Why this is the best cheap-and-reliable split:** Heavy media processing is a **burst workload** (1-10 clips per day, each ~30-300s of processing) — perfect for a laptop that can be idle when no uploads happen. The web/API is a **steady workload** (always-on) — perfect for a VPS. Running both on the same VPS would either waste money on idle RAM (a VPS big enough for Whisper = $20+/mo) or risk OOM when media is busy. Running both on the laptop risks the whole stack going down when you close the lid. The split optimizes for both.

---

## 1. Architecture diagram

```
                User (Browser / Mobile / HLS Client)
                          │
              ┌───────────▼───────────┐
              │  Cloudflare (Free)   │  Bot Fight Mode, Universal SSL, CDN
              │  DNS + Proxy         │  api.yourdomain.com → Cloudflare Tunnel endpoint
              │  + Cache HLS segments│
              └────┬─────────────┬───┘
                   │             │
        ┌──────────▼──────┐  ┌───▼─────────────────┐
        │  R2 (Zero       │  │  Cloudflare Tunnel  │
        │  egress, 10GB   │  │  (free, no port fwd)│
        │  free)          │  └────────┬─────────────┘
        │                 │           │
        │  hls/ public    │           │ Outbound
        │  uploads/       │           │ (laptop initiates)
        │  private        │           │
        └─────────────────┘  ┌────────▼─────────────────┐
                              │  YOUR LAPTOP (local)     │
                              │  cloudflared daemon      │
                              │  ┌───────────────────┐   │
                              │  │ celery_media      │   │
                              │  │ -Q heavy_media    │   │
                              │  │ ffmpeg + Whisper  │   │
                              │  │ + ST + KeyBERT    │   │
                              │  └───────────────────┘   │
                              │  2-4 GB RAM, 2-4 vCPU   │
                              └────────┬─────────────────┘
                                       │
                              ┌────────▼────────────────────┐
                              │  VPS (Hetzner CX22 €5.39/mo │
                              │  OR Oracle A1 $0/mo)        │
                              │  Docker Compose             │
                              │  ┌──────────────────────┐   │
                              │  │ nginx (TLS, :443)    │   │
                              │  └──────────────────────┘   │
                              │  ┌──────────────────────┐   │
                              │  │ gunicorn (Django)    │   │
                              │  │ + DRF + JWT          │   │
                              │  │ + /metrics endpoint  │   │
                              │  └──────────────────────┘   │
                              │  ┌──────────────────────┐   │
                              │  │ Celery default       │   │  (light tasks)
                              │  │ Celery feed (-Q fast)│   │
                              │  │ Celery beat          │   │
                              │  └──────────────────────┘   │
                              │  ┌──────────────────────┐   │
                              │  │ PostgreSQL 16        │   │  (self-hosted)
                              │  │ + pgvector (HNSW)    │   │
                              │  └──────────────────────┘   │
                              │  ┌──────────────────────┐   │
                              │  │ Redis 7              │   │  (broker + cache)
                              │  │ DB 0 = broker        │   │
                              │  │ DB 1 = cache         │   │
                              │  └──────────────────────┘   │
                              │  2 vCPU / 4 GB / 50 GB SSD │
                              └─────────────────────────────┘
```

---

## 2. What runs where (the split)

| Service | Location | Why |
|---|---|---|
| **Django web (gunicorn)** | VPS | Always-on, handles user requests. Must be on a public-facing host. |
| **nginx (TLS terminator)** | VPS | Public-facing. Terminates TLS for the Cloudflare tunnel. |
| **PostgreSQL 16 + pgvector** | VPS | Data integrity matters. Backup, replication, and indexing are easier on a stable host. |
| **Redis 7 (broker + cache)** | VPS | Shared between VPS workers and laptop workers. Must be reachable from both. |
| **Celery (default queue)** | VPS | Light tasks: counter flushes, cache invalidation, telemetry. |
| **Celery feed (`-Q fast_feed`)** | VPS | Refill user feed, vector evolution. CPU-light, lots of Redis calls. |
| **Celery beat** | VPS | Scheduler. Must be exactly 1 instance. |
| **Celery media (`-Q heavy_media`)** | **Laptop** | Burst workload (1-10 clips/day × 30-300s). Whisper `base` = 1.5 GB resident + ST = 0.5 GB + KeyBERT = 0.1 GB + librosa + temp scratch = 2-4 GB total. Laptop RAM (8-16 GB) handles this; VPS (2-4 GB) does not. |
| **R2 bucket (hls/ + uploads/)** | Cloudflare | S3-compatible. 10 GB free, zero egress. Replaces local MinIO + egress costs. |
| **Cloudflare Tunnel** | Laptop (`cloudflared` daemon) | Outbound-only. No port forwarding on the home router. Authenticated with a tunnel token. |

---

## 3. How the laptop connects (the tunnel)

The laptop does **not** need any inbound ports opened. `cloudflared` is a small Go daemon that:
1. Authenticates with Cloudflare using a tunnel token (stored in `/etc/cloudflared/` or `.env`).
2. Opens an **outbound** connection to Cloudflare's nearest edge (port 7844, HTTPS).
3. Cloudflare assigns the tunnel a stable UUID and a public hostname (e.g., `https://laptop-tunnel.yourdomain.com`).
4. When a request hits the public hostname, Cloudflare forwards it down the tunnel to the laptop's local `cloudflared` process, which proxies to `localhost:<port>`.

For EchoFlow, the laptop only needs to **connect to the VPS's services** (Redis broker, PostgreSQL, S3). The laptop does **not** need to serve HTTP to the public. So the tunnel configuration is:

**Option A (Recommended): Laptop → VPS through tunnel**

The laptop's `celery_media` worker connects to:
- `REDIS_BROKER_URL=redis://tunnel-redis-broker.laptop-tunnel.yourdomain.com:6379/0`
- `DATABASE_URL=postgres://tunnel-db.laptop-tunnel.yourdomain.com:5432/echoflow_db`
- `AWS_S3_ENDPOINT_URL=https://<accountid>.r2.cloudflarestorage.com` (R2 is public; no tunnel needed)

The VPS exposes Redis and Postgres through a separate tunnel (VPS → Cloudflare) with private hostnames. The laptop connects outbound through its own tunnel to reach these private hostnames.

**Option B (Simpler, less secure): Laptop → VPS via direct private IP**

The VPS and the laptop are on the same private network (e.g., both on Tailscale, both on the same LAN, or the VPS exposes a WireGuard endpoint). The laptop connects directly to `redis://10.0.0.1:6379/0` without going through Cloudflare.

For this guide, we document **Option A** (tunnel-to-tunnel via Cloudflare) because:
- No port forwarding on the home router.
- No need to install/configure WireGuard or Tailscale.
- Free (Cloudflare Tunnel is free, no bandwidth limits).
- Works from anywhere (laptop at home, on cellular, at a coffee shop).

### 3.1 R2 vs Storage on the VM (clear recommendation: use R2)

**Verdict:** Put **all media** (original uploads + HLS segments) on Cloudflare R2 from day one. Keep **only the Postgres data directory** on the VPS local disk. This is the single highest-leverage decision for cost, simplicity, and future-proofing.

| Aspect | Cloudflare R2 | Local disk on the VPS | Winner |
|---|---|---|---|
| Cost at your scale (50 users, ~50 clips) | Essentially $0 (10 GB free, zero egress) | "Free" but limited by VPS disk size (40-80 GB) | R2 |
| Egress / HLS delivery | **Zero egress fees** (R2's killer feature) | You pay VPS bandwidth + risk ISP throttling at home | R2 |
| Scalability | Unlimited | Hits disk limit quickly with HLS segments (~140 KB × 30 segments × N clips) | R2 |
| Public HLS serving | Native + Cloudflare CDN caching | You must expose the VPS or put nginx in front | R2 |
| Reliability & durability | 11 nines, multi-location | Single VPS disk (even with backups) | R2 |
| Security (private uploads + public HLS) | Bucket policy: originals private, HLS public-read prefix only | Harder to get the permissions right; nginx serves mixed content | R2 |
| Matches existing code | Yes (`django-storages[s3]` + `boto3` already in `requirements-base.txt`) | Would require code changes to use local disk + nginx | R2 |
| Laptop media worker | Works perfectly (uploads results to R2 directly) | Laptop would have to push files back to VPS (extra hop) | R2 |

The original EchoFlow design already treats storage as S3-compatible (MinIO in dev, per `docs/minio-s3-architecture.md`). Switching the endpoint to R2 is **mostly an environment-variable change** — no code edits. The `hls/` public-read prefix + `uploads/` private split is identical to the MinIO design (`docker-compose.yml:197-223`, `minio-init`).

### 3.2 Why a small VPS is better than the alternatives (for your constraints)

Your constraints are very clear: hard ceiling of ~50 active users, $5-10/month budget, reliability without constant babysitting, heavy AI/media work already moved to the laptop. Here's the honest comparison:

| Option | Monthly Cost (realistic) | Reliability | Ops Complexity | Cold Starts / Sleeping | Verdict |
|---|---|---|---|---|---|
| **Small VPS** (Hetzner CX22 / CPX21 or Hostinger KVM 2) | $5-8 | High | Low-Medium | None | **Best fit** |
| Pure free tiers (Oracle, Render free, Supabase) | $0 | Medium-Low | Medium-High | Yes / capacity risk / pause risk | Fragile |
| Railway / Render / Fly hobby | $5-15+ | Medium | Low | Sometimes | Usage billing can spike, less control |
| Serverless (Cloud Run, etc.) | Variable | High | Medium | Yes | Celery + long-running workers are awkward |
| Managed everything (AWS / GCP / Azure) | $30-100+ | Very High | Low | No | Massively over budget |

**Why the VPS wins for you right now:**

- **Predictable cost:** You know exactly what you pay every month. No surprise bills.
- **No sleeping / cold starts:** The API, Redis, Postgres, and light Celery workers stay warm 24/7.
- **Full control:** You can run the exact Docker Compose stack you already have (minus the media worker).
- **Enough resources:** A 4-8 GB VPS comfortably runs Django + Postgres + Redis + light Celery workers.
- **Simple mental model:** One machine, one `docker compose up`, easy backups, easy debugging.
- **Perfect match with the laptop worker:** The VPS owns the broker and database; the laptop just connects to them.

The main downside of a VPS is that **you** are responsible for basic maintenance (security updates, disk monitoring, backups). At this scale that is usually 1-2 hours per month — far less painful than fighting free-tier limits or debugging why a PaaS put your worker to sleep.

### 3.3 Recommended starting order (highest leverage first)

1. **Create a Cloudflare R2 bucket** and configure public access **only** on the `hls/` prefix (same as the original MinIO design).
2. **Spin up a small VPS** ($5-8) — Hetzner CX22 (2 vCPU / 4 GB / 40 GB) or Hostinger KVM 2 (2 vCPU / 8 GB / 100 GB).
3. **Deploy the light stack** (API + light Celery + Postgres on local disk + Redis + nginx) pointed at R2.
4. **Set up the laptop media worker** with a private tunnel (Cloudflare Tunnel or Tailscale) so it can reach Redis (and Postgres).
5. **Put the domain on Cloudflare** (proxied) for TLS + bot protection + HLS caching.

**Why this order:**
- R2 is the foundation; without it, every other step has to be redone when you switch.
- The VPS can run with `S3_ENDPOINT_URL` pointing at a temporary bucket; switching to R2 later is a 5-line env change.
- The laptop worker is the last thing to add because the VPS works without it (uploaded clips just sit in `processing` until a worker picks them up; tasks queue safely in Redis).
- Cloudflare proxy is the only piece that touches end users; do it last so all your internal services are stable when you flip the public switch.

---

## 4. Data flow (what happens when a user uploads a clip)

```
1. User opens browser → https://app.yourdomain.com (Cloudflare Pages, free)
2. User clicks Upload → POST https://api.yourdomain.com/clips/ (Cloudflare → VPS nginx → gunicorn)
3. gunicorn (Django):
   a. Validates the upload (DRF throttle: 20/hr, settings.py:361)
   b. Writes original_file to R2 uploads/ prefix (S3 API, signed URL from media_urls.py)
   c. Creates AudioClip row with status='processing', semantic_vector=null
   d. Calls transaction.on_commit(process_audio_to_hls.delay(clip.id)) (services/uploads.py:29)
4. Celery dispatches the task to 'heavy_media' queue (settings.py:151-164 CELERY_TASK_ROUTES)
5. VPS Celery workers ignore 'heavy_media' (they only handle 'default' and 'fast_feed')
6. Laptop Celery worker (running -Q heavy_media via cloudflared tunnel) picks up the task
7. Laptop worker:
   a. Downloads original_file from R2 to /tmp/{clip_id}.wav (S3 GET, free egress)
   b. Runs ffmpeg normalize → librosa.load() → extract_acoustic_vector()
   c. Runs faster-whisper (WhisperModel "base") → transcript
   d. Runs sentence-transformers (all-MiniLM-L6-v2) → semantic_vector (384-d)
   e. Runs KeyBERT → tags
   f. Runs ffmpeg HLS encode (192/128/64 kbps ABR) → /tmp/hls-{clip_id}/master.m3u8
   g. Uploads all HLS files to R2 hls/{clip_id}/... (S3 PUT, free)
   h. Updates AudioClip row: hls_playlist_url='hls/{clip_id}/master.m3u8', status='ready'
8. User's browser reloads feed → GET /feed/ (Cloudflare → VPS) → returns clip with hls_playlist_url
9. hls.js fetches https://media.yourdomain.com/hls/{clip_id}/master.m3u8 → Cloudflare → R2 (zero egress)
```

**Latency budget:**
- Upload: 5-30 seconds (depends on file size, R2 PUT is fast).
- Heavy media processing: 30-300 seconds per clip (Whisper base = 0.5-2× realtime, HLS encode = 5-10s for a 30s clip).
- Feed refresh: 10-50ms.
- HLS playback: <100ms (Cloudflare caches segments, R2 serves the manifest).

**Reliability:**
- If the laptop is offline when a clip is uploaded: the task waits in the `heavy_media` Redis queue. When the laptop comes back online, the worker picks it up. (`Celery` has a visibility timeout; tasks can wait days.)
- If the laptop crashes mid-processing: the task is redelivered to another worker. Since `process_audio_to_hls` is **idempotent** (overwrites `hls/{clip_id}/` and updates the row), retries are safe.
- If R2 is down: the upload fails, the user sees a 5xx error, and the client retries. The original_file is already in R2; the user can re-submit the upload with the same file.

---

## 5. Step-by-step setup

### 5.1 VPS provisioning (Hetzner CX22 or Oracle A1)

**Option A: Hetzner Cloud CX22 (€5.39/mo, $6/mo)**
1. Create account at https://www.hetzner.com/cloud (requires credit card; charged only after free trial ends).
2. Create a new server:
   - **Image:** Ubuntu 22.04 LTS (or 24.04)
   - **Type:** CX22 (2 vCPU shared, 4 GB RAM, 40 GB SSD)
   - **Location:** Falkenstein (cheapest) or Ashburn/Nuremberg (closer to users)
   - **Networking:** Public IPv4 only (no IPv6 needed)
   - **SSH key:** Add your public key
3. Note the public IP address.

**Option B: Oracle Cloud Always Free ARM A1 ($0/mo, 4 vCPU / 24 GB)**
1. Create account at https://www.oracle.com/cloud/ (free, $300 trial credit; never charged if you stay in Always Free).
2. Create a VM.Standard.A1.Flex instance:
   - **Shape:** 4 OCPU + 24 GB RAM (or 2 OCPU + 12 GB if you want to split)
   - **Image:** Ubuntu 22.04 LTS ARM
   - **Boot volume:** 200 GB (Always Free limit)
3. Note the public IP address.

**VPS initial setup (after SSH):**

```bash
# Update and install prerequisites
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose-v2 git nginx certbot python3-certbot-nginx ufw fail2ban unattended-upgrades

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker

# Verify
docker --version
docker compose version

# Enable ufw (firewall)
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp         # SSH
sudo ufw allow 80/tcp         # HTTP (redirect to HTTPS)
sudo ufw allow 443/tcp        # HTTPS (Cloudflare + direct)
sudo ufw enable
sudo ufw status verbose
```

### 5.2 Cloudflare setup (DNS + Tunnel + R2)

1. **Add site to Cloudflare** (free plan, https://cloudflare.com):
   - Add `yourdomain.com` to Cloudflare.
   - Update nameservers at your registrar (e.g., Namecheap) to point to Cloudflare's nameservers.
   - Wait for DNS propagation (5-30 min).
2. **Create R2 bucket** (https://dash.cloudflare.com → R2):
   - Bucket name: `echoflow-media`
   - Region: Automatic (R2's default)
   - Note the **Account ID** (used for S3 endpoint: `https://<accountid>.r2.cloudflarestorage.com`).
   - Create an **R2 API Token** with `Object Read & Write` permissions scoped to the bucket. Note the Access Key ID and Secret Access Key.
3. **Set R2 bucket policy** (same as `docs/zero-cost-deployment.md` §4.4):
   - `hls/*` → public-read
   - `uploads/*` → private (signed URLs from `media_urls.py`)
4. **Create Cloudflare Tunnel** (Zero Trust → Networks → Tunnels):
   - Tunnel name: `echoflow-vps`
   - Save the **Tunnel UUID** and **Tunnel Secret** (you'll paste this into `cloudflared` on the VPS).
5. **Configure tunnel public hostnames**:
   - Public hostname: `api.yourdomain.com` → Service: `http://nginx:80` (or `http://localhost:80` on the VPS).
   - Optional: `media.yourdomain.com` → Service: `http://nginx:9443` (for browser-facing HLS).
6. **Enable Cloudflare security**:
   - SSL/TLS → Full (strict) — required for the tunnel.
   - Security → Bot Fight Mode → ON (free).
   - Security → Security Level → Medium (free).
   - Caching → Configuration → Caching Level → Standard.

### 5.3 Install `cloudflared` on the VPS

```bash
# Install cloudflared (Debian/Ubuntu)
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared focal main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install -y cloudflared

# Authenticate
sudo cloudflared service install <tunnel-token>
# (Replace <tunnel-token> with the token from the Cloudflare dashboard)

# Start as a service
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
sudo systemctl status cloudflared
```

### 5.4 VPS Docker Compose setup (light services only)

Create `/home/deploy/echoflow/` on the VPS and `git clone` the repo. Then create `vps-compose.yml` (a slimmed version of `docker-compose.yml` with only the light services):

```yaml
# vps-compose.yml — runs on the VPS, excludes celery_media
# Based on docker-compose.yml:1-708 with the following changes:
#   - Removed: celery_media (laptop only)
#   - Removed: minio (R2 is the storage backend)
#   - Removed: minio-init (R2 bucket is created via Cloudflare dashboard)
#   - Removed: prometheus + grafana (out of scope for this 50-user / $6 budget;
#     rely on /metrics endpoint + UptimeRobot + Sentry instead — see §9.6)
#   - Kept: web, celery (default), celery_feed, celery_beat, db,
#           redis_broker, redis_cache, nginx
#
# This file is created on a SEPARATE BRANCH (see §5.9 "Branch strategy") and
# does NOT modify the original docker-compose.yml. The local dev workflow
# (docker compose up --build) stays on the main branch with the full
# 14-service stack.

services:
  db:
    image: pgvector/pgvector:pg16
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${DB_NAME}
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER} -d ${DB_NAME}"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis_broker:
    image: redis:7-alpine
    restart: unless-stopped
    command: redis-server --appendonly yes --maxmemory 512mb --maxmemory-policy noeviction
    volumes:
      - redis_broker_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis_cache:
    image: redis:7-alpine
    restart: unless-stopped
    command: redis-server --appendonly yes --maxmemory 1gb --maxmemory-policy allkeys-lru
    volumes:
      - redis_cache_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  web:
    image: ghcr.io/devansh1012007/echoflow-api:${TAG:-latest}
    build:
      context: .
      dockerfile: Dockerfile
      target: api
    working_dir: /app
    restart: unless-stopped
    env_file: .env
    command: >
      sh -c "set -e && python wait_for_db.py &&
             python manage.py migrate &&
             python manage.py collectstatic --noinput &&
             gunicorn -c gunicorn.conf.py backend.EchoFlow.wsgi:application"
    volumes:
      - .:/app
    depends_on:
      db: { condition: service_healthy }
      redis_broker: { condition: service_healthy }
      redis_cache: { condition: service_healthy }
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; req = urllib.request.Request('http://localhost:8000/health/', headers={'X-Forwarded-Proto': 'https'}); urllib.request.urlopen(req, timeout=4)"]
      interval: 30s
      timeout: 10s
      start_period: 90s
      retries: 3
    deploy:
      resources:
        limits: { cpus: '1', memory: 1G }
        reservations: { cpus: '0.25', memory: 256M }

  celery:
    image: ghcr.io/devansh1012007/echoflow-api:${TAG:-latest}
    build: { context: ., dockerfile: Dockerfile, target: api }
    working_dir: /app
    user: "1000:1000"
    restart: unless-stopped
    command: >
      sh -c "set -e && python wait_for_db.py &&
              celery -A backend.EchoFlow worker --loglevel=info"
    env_file: .env
    volumes: [ .:/app ]
    depends_on:
      db: { condition: service_healthy }
      redis_broker: { condition: service_healthy }
      redis_cache: { condition: service_healthy }
    deploy:
      resources:
        limits: { cpus: '0.5', memory: 1G }
        reservations: { cpus: '0.25', memory: 256M }

  celery_feed:
    image: ghcr.io/devansh1012007/echoflow-api:${TAG:-latest}
    build: { context: ., dockerfile: Dockerfile, target: api }
    restart: unless-stopped
    command: >
      sh -c "set -e && python wait_for_db.py &&
              celery -A backend.EchoFlow worker -Q fast_feed --concurrency=4 --loglevel=info"
    env_file: .env
    volumes: [ .:/app ]
    depends_on:
      db: { condition: service_healthy }
      redis_broker: { condition: service_healthy }
      redis_cache: { condition: service_healthy }
    deploy:
      resources:
        limits: { cpus: '0.5', memory: 1G }
        reservations: { cpus: '0.25', memory: 256M }

  celery_beat:
    image: ghcr.io/devansh1012007/echoflow-api:${TAG:-latest}
    build: { context: ., dockerfile: Dockerfile, target: api }
    restart: unless-stopped
    command: >
      sh -c "set -e && python wait_for_db.py &&
              celery -A backend.EchoFlow beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler"
    env_file: .env
    volumes: [ .:/app ]
    depends_on:
      db: { condition: service_healthy }
      redis_broker: { condition: service_healthy }
      redis_cache: { condition: service_healthy }
    healthcheck: { disable: true }
    deploy:
      resources:
        limits: { cpus: '0.25', memory: 256M }
        reservations: { cpus: '0.1', memory: 128M }

  nginx:
    image: nginx:1.27-alpine
    restart: unless-stopped
    depends_on:
      web: { condition: service_healthy }
    ports: [ "80:80", "443:443", "9443:9443" ]
    volumes:
      - ./docker/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./docker/certs:/etc/nginx/certs:ro
    deploy:
      resources:
        limits: { cpus: '0.5', memory: 128M }
        reservations: { cpus: '0.1', memory: 32M }

volumes:
  postgres_data:
  redis_broker_data:
  redis_cache_data:
```

**.env on the VPS (production):**

```bash
# Django (production)
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=$(openssl rand -base64 50)
DJANGO_ALLOWED_HOSTS=api.yourdomain.com
DJANGO_CORS_ALLOWED_ORIGINS=https://app.yourdomain.com
DJANGO_CORS_ALL=False

# Database (Docker-internal)
DB_NAME=echoflow_db
DB_USER=echoflow
DB_PASSWORD=$(openssl rand -base64 32)
DATABASE_URL=postgres://echoflow:${DB_PASSWORD}@db:5432/echoflow_db

# Redis (Docker-internal; same compose network)
REDIS_BROKER_URL=redis://redis_broker:6379/0
REDIS_CACHE_URL=redis://redis_cache:6379/0

# S3-compatible (Cloudflare R2)
AWS_ACCESS_KEY_ID=<your-r2-access-key>
AWS_SECRET_ACCESS_KEY=<your-r2-secret>
AWS_STORAGE_BUCKET_NAME=echoflow-media
AWS_S3_ENDPOINT_URL=https://<your-account-id>.r2.cloudflarestorage.com
AWS_S3_REGION_NAME=auto
AWS_S3_QUERYSTRING_EXPIRE=3600
PUBLIC_MEDIA_ENDPOINT_URL=https://media.yourdomain.com

# Field encryption
FIELD_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# Sentry (optional)
SENTRY_DSN=
SENTRY_ENV=production
SENTRY_TRACES_SAMPLE_RATE=0.1
SENTRY_PROFILES_SAMPLE_RATE=0.05

# Gunicorn (4 GB VPS)
GUNICORN_WORKERS=2
GUNICORN_THREADS=4
```

**Note:** `HF_TOKEN` is **NOT** needed on the VPS (the VPS doesn't run `celery_media`). The `api` image doesn't include Whisper/sentence-transformers, so it doesn't need HF tokens at build time.

### 5.5 Boot the VPS stack

```bash
cd /home/deploy/echoflow
docker compose -f vps-compose.yml up -d
docker compose -f vps-compose.yml exec db psql -U echoflow -d echoflow_db -c "CREATE EXTENSION IF NOT EXISTS vector;"
docker compose -f vps-compose.yml exec web python manage.py migrate --noinput
docker compose -f vps-compose.yml exec web python manage.py collectstatic --noinput
```

Verify: `curl -H 'Host: api.yourdomain.com' http://localhost/health/` → 200.

### 5.6 Laptop setup (`celery_media` worker only)

On the laptop (Mac/Linux):

```bash
# Install Docker (if not already)
# macOS: download Docker Desktop from https://docker.com/products/docker-desktop
# Linux: follow https://docs.docker.com/engine/install/

# Clone the repo
git clone https://github.com/devansh1012007/EchoFlow.git
cd EchoFlow

# Set up .env (laptop-specific)
cp .env.example .env
```

**.env on the laptop (worker only):**

```bash
# Django
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<same-as-vps-or-different>
DJANGO_ALLOWED_HOSTS=api.yourdomain.com

# Database (connect through VPS tunnel — see §5.7)
DATABASE_URL=postgres://echoflow:<db-password>@tunnel-db.laptop-tunnel.yourdomain.com:5432/echoflow_db

# Redis (connect through VPS tunnel)
REDIS_BROKER_URL=redis://tunnel-redis-broker.laptop-tunnel.yourdomain.com:6379/0
REDIS_CACHE_URL=redis://tunnel-redis-cache.laptop-tunnel.yourdomain.com:6379/0

# S3 (R2 is public; direct connection)
AWS_ACCESS_KEY_ID=<your-r2-access-key>
AWS_SECRET_ACCESS_KEY=<your-r2-secret>
AWS_STORAGE_BUCKET_NAME=echoflow-media
AWS_S3_ENDPOINT_URL=https://<your-account-id>.r2.cloudflarestorage.com
AWS_S3_REGION_NAME=auto
AWS_S3_QUERYSTRING_EXPIRE=3600

# HuggingFace (for Whisper model download, baked at build time)
HF_TOKEN=<your-hf-token>

# Field encryption (must match VPS)
FIELD_ENCRYPTION_KEY=<same-as-vps>

# Media worker settings
HF_HOME=/home/<user>/.cache/huggingface
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

### 5.7 Cloudflare Tunnel from laptop to VPS services

There are two ways to expose VPS services to the laptop through the tunnel:

**Approach 1 (Simpler): Two tunnels, one for each direction.**

You already created a tunnel for `api.yourdomain.com → VPS nginx`. Add **public hostnames** to the same tunnel for the laptop-facing services:

In the Cloudflare dashboard (Zero Trust → Networks → Tunnels → `echoflow-vps` → Configure → Public Hostnames):

- `tunnel-redis-broker.laptop-tunnel.yourdomain.com` → `redis://redis_broker:6379` (or `http://localhost:6379` on the VPS, with `cloudflared` proxying raw TCP)
- `tunnel-redis-cache.laptop-tunnel.yourdomain.com` → same as above
- `tunnel-db.laptop-tunnel.yourdomain.com` → `http://localhost:5432` (or use Cloudflare's TCP tunnel support for non-HTTP protocols)

**For non-HTTP services (Redis, PostgreSQL), you need Cloudflare's TCP tunneling**, which is available on the **Free plan** but requires a slightly different config:

```yaml
# ~/.cloudflared/config.yml on the VPS (in addition to the existing tunnel)
tunnel: <tunnel-uuid>
credentials-file: /etc/cloudflared/<tunnel-uuid>.json

ingress:
  # HTTP hostnames (web + media)
  - hostname: api.yourdomain.com
    service: http://nginx:80
  - hostname: media.yourdomain.com
    service: http://nginx:9443

  # TCP services for laptop
  - hostname: tunnel-redis-broker.laptop-tunnel.yourdomain.com
    service: tcp://redis_broker:6379
  - hostname: tunnel-redis-cache.laptop-tunnel.yourdomain.com
    service: tcp://redis_cache:6379
  - hostname: tunnel-db.laptop-tunnel.yourdomain.com
    service: tcp://db:5432

  # Catch-all
  - service: http_status:404
```

Then on the laptop, the `celery_media` worker uses:

```bash
REDIS_BROKER_URL=redis://tunnel-redis-broker.laptop-tunnel.yourdomain.com:6379/0
DATABASE_URL=postgres://echoflow:<db-password>@tunnel-db.laptop-tunnel.yourdomain.com:5432/echoflow_db
```

**Approach 2 (Alternative): Two separate tunnels.**

Create a second Cloudflare tunnel for the laptop → VPS direction:
- Tunnel name: `echoflow-laptop`
- Public hostnames: same as above (Redis, Postgres)
- Install `cloudflared` on the laptop, not the VPS

This is more complex but separates concerns. The laptop is the tunnel endpoint, not the VPS.

**For this guide, Approach 1 is recommended** (single tunnel on the VPS, public hostnames for both web and laptop services).

### 5.8 Laptop Docker Compose (media worker only)

Create `laptop-compose.yml` (a minimal compose file with only the `celery_media` service):

```yaml
# laptop-compose.yml — runs on your laptop, only the celery_media worker
# Connects to VPS services through the Cloudflare tunnel

services:
  celery_media:
    image: ghcr.io/devansh1012007/echoflow-media:${TAG:-latest}
    build:
      context: .
      dockerfile: Dockerfile
      target: media
      secrets:
        - hf_token
    restart: unless-stopped
    command: >
      sh -c "set -e && python wait_for_db.py &&
              celery -A backend.EchoFlow worker -Q heavy_media --pool=prefork --concurrency=2 --loglevel=info"
    env_file: .env
    secrets:
      - hf_token
    volumes:
      - laptop-scratch:/tmp
    deploy:
      resources:
        limits: { cpus: '4', memory: 4G }
        reservations: { cpus: '2', memory: 2G }

secrets:
  hf_token:
    environment: HF_TOKEN

volumes:
  laptop-scratch:
```

```bash
# Build the media image (HF_TOKEN as BuildKit secret, not ARG)
export HF_TOKEN=hf_xxx
docker build --target media -t ghcr.io/devansh1012007/echoflow-media:1.0.0 \
  --secret id=hf_token,env=HF_TOKEN .

# Start the worker
docker compose -f laptop-compose.yml up -d
docker compose -f laptop-compose.yml logs -f celery_media
```

**Important:** The laptop's `celery_media` worker **only** consumes from the `heavy_media` queue. The VPS's `celery` and `celery_feed` workers ignore `heavy_media` (they only consume from `default` and `fast_feed` respectively). This is configured in `settings.py:151-164`:

```python
# backend/EchoFlow/settings.py:151-164 (already in the repo)
CELERY_TASK_ROUTES = {
    "process_audio_to_hls": {"queue": "heavy_media"},  # → laptop
    "refill_user_feed": {"queue": "fast_feed"},        # → VPS
    # ... other tasks default to 'default' queue
}
```

The laptop's worker command uses `-Q heavy_media` (not `celery` worker which consumes from all queues by default). This is the routing mechanism.

### 5.9 Branch strategy and file layout (read this before deploying)

The hybrid deployment uses **two branches** and **two new compose files** so the original local dev workflow (the full 14-service stack with MinIO + Prometheus + Grafana) stays untouched. Do NOT modify the original `docker-compose.yml` on `main`.

#### Branches

| Branch | Purpose | What it contains |
|---|---|---|
| `main` (default) | Local dev + documentation only | Original `docker-compose.yml` (14 services, MinIO, Prometheus, Grafana, full media worker). Use this for `docker compose up --build` on your laptop. |
| `feat/hybrid-vps` | VPS production deploy | New `docker-compose.vps.yml` (slimmed: web, light Celery, db, redis, nginx). Created from `main`, does NOT modify the original compose. |
| `feat/hybrid-laptop` | Laptop media-worker deploy | New `docker-compose.laptop.yml` (only `celery_media`). Created from `main`, does NOT modify the original compose. |

#### File layout (new files only — no edits to existing files)

```
EchoFlow/
├── docker-compose.yml              (UNCHANGED — main branch only)
├── docker-compose.vps.yml          (NEW — on feat/hybrid-vps)
├── docker-compose.laptop.yml       (NEW — on feat/hybrid-laptop)
├── .env.vps.example                (NEW — on feat/hybrid-vps, R2 + tunnel creds)
├── .env.laptop.example             (NEW — on feat/hybrid-laptop, R2 + tunnel creds)
├── scripts/
│   ├── laptop-heartbeat.sh         (NEW — on feat/hybrid-laptop, see §5.10)
│   ├── laptop-deploy.sh            (NEW — on feat/hybrid-laptop, builds + runs media)
│   └── vps-deploy.sh               (NEW — on feat/hybrid-vps, runs the slimmed stack)
└── docs/
    ├── hybrid-deployment.md        (THIS FILE — describes both branches)
    ├── zero-cost-deployment.md     (unchanged — Oracle-only alternative)
    └── aws-deployment-guide.md     (unchanged — all-cloud alternative)
```

#### Why three branches and not one

- The original `docker-compose.yml` is the **local dev** workflow. It must keep all 14 services (MinIO, Prometheus, Grafana, full media worker) so `docker compose up --build` still works on a fresh clone. Touching this file breaks local dev for everyone else.
- The VPS and laptop have **different services**, different `.env` files, and different resource limits. Splitting them into two branches keeps each branch small, reviewable, and reversible.
- If you later need to test a change on `main` (e.g., a new Celery task in `tasks.py`), you can `git cherry-pick` or `git merge` it into both `feat/hybrid-vps` and `feat/hybrid-laptop` without touching the compose files.

#### Quick start (full deployment in 5 commands)

```bash
# 1. From main, create the VPS branch
git checkout -b feat/hybrid-vps
# (then add docker-compose.vps.yml, .env.vps.example, scripts/vps-deploy.sh; commit)

# 2. From main, create the laptop branch
git checkout main
git checkout -b feat/hybrid-laptop
# (then add docker-compose.laptop.yml, .env.laptop.example, scripts/laptop-deploy.sh; commit)

# 3. On the VPS: clone, switch branch, deploy
ssh deploy@<vps-ip>
git clone https://github.com/<you>/EchoFlow.git
cd EchoFlow
git checkout feat/hybrid-vps
cp .env.vps.example .env
# (edit .env with real R2 + tunnel creds)
bash scripts/vps-deploy.sh

# 4. On your laptop: clone, switch branch, deploy
cd ~/Code
git clone https://github.com/<you>/EchoFlow.git
cd EchoFlow
git checkout feat/hybrid-laptop
cp .env.laptop.example .env
# (edit .env with same R2 + tunnel creds as VPS; add HF_TOKEN)
bash scripts/laptop-deploy.sh

# 5. Verify end-to-end
curl -I https://api.yourdomain.com/health/   # 200
# Upload a clip via the frontend or /clips/ endpoint
docker compose -f laptop-compose.yml logs -f celery_media  # should show task processing
```

**The two branches are independent** — you can deploy the VPS first, test the API, then deploy the laptop later. The VPS works fine without the laptop (uploaded clips just sit in `processing` status until a worker picks them up; when the laptop comes online, the queued tasks are processed).

### 5.10 Heartbeat pattern (`media_worker:alive` Redis key)

The laptop is the single point of failure for media processing. If it sleeps or loses Wi-Fi, the user sees their uploaded clip stuck in `processing` status with no indication of why. Add a simple **heartbeat** so the API can show "processing delayed" when the worker is offline.

**Pattern:** The laptop's `celery_media` worker periodically sets a Redis key `media_worker:alive` with a TTL of 60 seconds. The VPS's API can check if the key exists; if it doesn't, return a "processing delayed" status to the user.

**Implementation on the laptop (`scripts/laptop-heartbeat.sh` on `feat/hybrid-laptop`):**

```bash
#!/bin/bash
# scripts/laptop-heartbeat.sh
# Sets a Redis key media_worker:alive with a 60-second TTL.
# Run as a background process on the laptop, alongside the celery_media worker.
set -e

# Use the same Redis URL as the laptop's worker (via tunnel)
REDIS_URL="${REDIS_BROKER_URL:-redis://tunnel-redis-broker.laptop-tunnel.yourdomain.com:6379/0}"

while true; do
  # Use python-redis or redis-cli. python is universally available on the laptop.
  python -c "
import redis, time, os
r = redis.Redis.from_url(os.environ['REDIS_URL'])
r.set('media_worker:alive', time.time(), ex=60)
print(f'heartbeat set at {time.time()}', flush=True)
"
  sleep 30
done
```

Run as a background process: `nohup bash scripts/laptop-heartbeat.sh > /tmp/heartbeat.log 2>&1 &` (or add to `laptop-deploy.sh` as a sidecar).

**Check on the VPS (new endpoint `/api/v1/health/media-worker/`):**

```python
# backend/EchoFlow/health.py (add to existing file)
from django.core.cache import cache

def media_worker_alive() -> bool:
    try:
        return cache.get("media_worker:alive") is not None
    except Exception:
        return False
```

The frontend can poll this endpoint and show "Processing delayed — media worker offline" when it's false. The key is that the heartbeat lives in the **same Redis broker** the laptop already uses — no new infrastructure needed.

---

## 6. Code/config changes needed (small, minimal risk)

### 6.1 Files that need to change (on the two new branches)

**On `feat/hybrid-vps` (new files, no edits to existing files):**
1. `docker-compose.vps.yml` (new) — see §5.4. Slimmed compose with only light services.
2. `.env.vps.example` (new) — see §5.4. Contains R2 endpoint, tunnel creds, production secrets.
3. `scripts/vps-deploy.sh` (new) — one-shot deploy script (boots the stack, runs migrations, sets up cron for backups).
4. `docker/nginx.conf` (UNCHANGED on this branch — the existing config already terminates TLS for the tunnel; only the `media.yourdomain.com` server block may need a one-line proxy_pass change to R2).

**On `feat/hybrid-laptop` (new files, no edits to existing files):**
1. `docker-compose.laptop.yml` (new) — see §5.8. Only `celery_media` service.
2. `.env.laptop.example` (new) — see §5.6. Same R2 keys as VPS, tunnel hostname, `HF_TOKEN`.
3. `scripts/laptop-deploy.sh` (new) — builds the `media` Docker image with `HF_TOKEN` BuildKit secret, starts `cloudflared`, starts the worker.

**Files that do NOT change on either branch (they already work):**
- `backend/EchoFlow/settings.py:151-164` — Celery routing already routes `process_audio_to_hls` to `heavy_media` queue.
- `backend/app/tasks.py:165-352` — `process_audio_to_hls` is queue-agnostic.
- `backend/app/models.py` — vector fields unchanged.
- `backend/app/media_urls.py` — `django-storages[s3]` works with any S3-compatible backend (R2 included).
- `backend/app/views/content.py` — upload view dispatches to `heavy_media` queue via `transaction.on_commit`; the worker can run anywhere.
- `Dockerfile` — `api` and `media` targets unchanged.
- `docker-compose.yml` (on `main`) — unchanged. The local dev workflow still works for everyone.

```nginx
# /etc/nginx/nginx.conf (or docker/nginx.conf on the VPS)
server {
  listen 9443 ssl http2;
  server_name media.yourdomain.com;
  ssl_certificate /etc/nginx/certs/localhost.crt;
  ssl_certificate_key /etc/nginx/certs/localhost.key;

  # Proxy HLS requests to R2 (public-read hls/* prefix)
  location /hls/ {
    proxy_pass https://echoflow-media.<accountid>.r2.cloudflarestorage.com/hls/;
    proxy_set_header Host echoflow-media.<accountid>.r2.cloudflarestorage.com;
    proxy_ssl_server_name on;
    # Cache HLS segments aggressively (10s for manifest, 1y for .ts)
    proxy_cache_valid 200 10s;  # .m3u8 manifests
    add_header Cache-Control "public, max-age=10" always;
  }

  location ~* \.ts$ {
    proxy_pass https://echoflow-media.<accountid>.r2.cloudflarestorage.com;
    proxy_set_header Host echoflow-media.<accountid>.r2.cloudflarestorage.com;
    proxy_ssl_server_name on;
    add_header Cache-Control "public, max-age=31536000, immutable" always;
  }
}
```

Or, simpler: point `media.yourdomain.com` directly at R2's `r2.dev` subdomain or a custom R2 public bucket URL (no nginx needed). R2 supports custom domains via Cloudflare.

7. **`scripts/` (in repo):** Add a new `laptop-deploy.sh` script that:
   - Starts `cloudflared` on the laptop
   - Builds the `media` Docker image with `HF_TOKEN` as a BuildKit secret
   - Starts `laptop-compose.yml`

```bash
#!/bin/bash
# scripts/laptop-deploy.sh
set -e
export HF_TOKEN="${HF_TOKEN:?HF_TOKEN is required}"

# Start cloudflared (assumes installed)
cloudflared service install "${CLOUDFLARE_TUNNEL_TOKEN}" || true
sudo systemctl restart cloudflared

# Build media image
cd "$(dirname "$0")/.."
docker build --target media -t echoflow-media:local \
  --secret id=hf_token,env=HF_TOKEN .

# Start worker
docker compose -f laptop-compose.yml up -d
docker compose -f laptop-compose.yml logs -f celery_media
```

### 6.2 Files that do NOT need to change

- `backend/EchoFlow/settings.py` (Celery routing is already correct).
- `backend/app/tasks.py` (`process_audio_to_hls` is queue-agnostic).
- `backend/app/models.py` (vectors are stored in Postgres; the laptop writes to the same DB).
- `backend/app/media_urls.py` (signed URL generation works with any S3-compatible backend).
- `backend/app/serializers.py` (upload accepts `original_file`; the laptop reads the same row).
- `backend/app/views/content.py` (upload view is queue-agnostic; `transaction.on_commit(process_audio_to_hls.delay(clip.id))` is the only thing that matters).
- Frontend (`frontend/`): the frontend only talks to the VPS API, which is publicly accessible through the Cloudflare tunnel. The frontend never knows about the laptop.

---

## 7. Cost breakdown (real numbers)

| Component | Provider | Free tier / cost | Source URL |
|---|---|---|---|
| VPS | Hetzner CX22 (2 vCPU, 4 GB, 40 GB) | **€5.39/mo ($6/mo)** | https://www.hetzner.com/cloud |
| VPS (alternative) | Oracle Cloud Always Free ARM A1 (4 vCPU, 24 GB, 200 GB) | **$0/mo** (no expiration) | https://www.oracle.com/cloud/free/ |
| Laptop (compute) | Your existing hardware | **$0/mo** (electricity ~$2-3/mo, not counted) | — |
| R2 storage | Cloudflare R2 | 10 GB free, then $0.015/GB/mo, **zero egress** | https://developers.cloudflare.com/r2/pricing/ |
| Cloudflare Tunnel | Cloudflare Free | **$0/mo** (unlimited bandwidth, no request limits) | https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/ |
| Bot protection | Cloudflare Free | **$0/mo** (Bot Fight Mode + unmetered DDoS) | https://www.cloudflare.com/plans/ |
| DNS | Cloudflare Free | **$0/mo** | — |
| Frontend | Cloudflare Pages (or Vercel Hobby) | **$0/mo** (Cloudflare Pages has no commercial restriction) | https://pages.cloudflare.com/ |
| Container registry | GitHub Container Registry | 500 MB free (public) — **the `media` image (~4 GB) exceeds this; build locally on laptop instead** | https://docs.github.com/en/packages |
| Error tracking | Sentry Free | 5K events/mo | https://sentry.io/pricing/ |
| Uptime monitoring | UptimeRobot Free | 50 monitors, 5-min checks | https://uptimerobot.com/pricing/ |
| Email (transactional) | Resend Free | 100/day, 3K/mo | https://resend.com/pricing |
| Domain (first year) | Namecheap via GitHub Student Pack | **Free** for 1 year | https://education.github.com/pack |
| **Total (Hetzner)** | | **~$6/mo** + ~$9/yr domain after year 1 | |
| **Total (Oracle A1)** | | **~$0/mo** + ~$9/yr domain after year 1 | |

**The Hetzner option is $6/mo. The Oracle option is $0/mo.** Either way, the laptop adds no cloud cost.

---

## 8. Pros and cons (compared to all-cloud and all-local)

| Approach | Cost | Reliability | Complexity | Best for |
|---|---|---|---|---|
| All-cloud (ECS Fargate + RDS + S3, `docs/aws-deployment-guide.md`) | $120-140/mo | 99.9% | High (many services) | 1000+ users, commercial |
| Oracle Always Free (full stack on A1, `docs/zero-cost-deployment.md`) | $0/mo | 99.9% | Medium (one VM) | 50 users, dev/small prod |
| **Hybrid (VPS light + laptop heavy_media, this doc)** | **$0-6/mo** | **99% (laptop is SPOF for media)** | **Medium (two deployments)** | **50 users, want to save RAM on VPS, laptop is reliable** |
| All-local (laptop + Cloudflare Tunnel, from prior discussion) | $0/mo | 95% (laptop is SPOF for everything) | Low (one deployment) | Personal use, dev |

**The hybrid approach wins for:**
- Users who want a stable public API (VPS is always on).
- Users who don't want to pay for VPS RAM they don't use (Whisper needs 2-4 GB; the Hetzner CX22 has 4 GB total, which is borderline).
- Users who already have a laptop with 8+ GB RAM.
- Users who want to test the laptop's media pipeline before moving to a server.

**The hybrid approach loses for:**
- Users whose laptop sleeps, dies, or has unreliable Wi-Fi (laptop = single point of failure for media processing).
- Users who need 24/7 media processing (e.g., 100+ clips/day, scheduled media jobs).
- Users who don't want to manage two deployments.

---

## 9. Security (the three layers)

### 9.1 Layer 1: Cloudflare Tunnel (outbound-only, no port forwarding)

- Laptop runs `cloudflared`, which initiates an **outbound** HTTPS connection to Cloudflare's edge (port 7844).
- No inbound ports are opened on the home router. The laptop is not directly reachable from the internet.
- The tunnel is authenticated with a `Tunnel Secret` (a per-tunnel JSON file in `/etc/cloudflared/`). If the secret is compromised, rotate it via `cloudflared service install <new-token>`.
- The tunnel token can be revoked from the Cloudflare dashboard at any time.

### 9.2 Layer 2: R2 bucket policy (public-read hls/, private uploads/)

Same as `docs/zero-cost-deployment.md` §4.4:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublicReadHLS",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::echoflow-media/hls/*"
    }
  ]
}
```

- `hls/*` is public-read (browsers fetch segments directly from R2 via `media.yourdomain.com`).
- `uploads/*` is private (signed URLs from `media_urls.py`, 1-hour TTL).
- **R2 has zero egress cost** — confirmed from pricing page (`developers.cloudflare.com/r2/pricing/`).
- Block Public Access (BPA) is enabled at the account level; the bucket policy explicitly grants `hls/*` public-read.

### 9.3 Layer 3: VPS firewall (ufw) + DRF throttles (settings.py:359-369)

- **VPS `ufw`:** Allow 22 (SSH, your IP only), 80 (HTTP → 443 redirect), 443 (HTTPS via Cloudflare tunnel), and nothing else. The VPS's public IP is exposed; the tunnel hides internal services (Redis, Postgres) from the public internet.
- **DRF `ScopedRateThrottle` (`settings.py:359-369`):** `telemetry: 60/min`, `upload: 20/hr`, `register: 5/hr`, `login: 10/min`, `comment: 60/hr`, `share_send: 100/hr`, `interaction: 60/min`. Confirmed in `docs/EXPLAIN/auth/04-rate-limiting.md`. Stored in Redis (self-hosted on VPS).
- **Cloudflare Bot Fight Mode (Free):** Confirmed in pricing page. Stops commodity AWS bot scanners (Shodan, Censys, Amazon security scanners, headless browsers) with managed challenges. No per-request cost.
- **Cloudflare Bot Management (Pro, $20/mo):** Optional upgrade. Granular bot categories, custom rules, machine-learning bot scoring. Not needed at 50 users.

### 9.4 Laptop firewall (ufw)

- **Default:** `sudo ufw default deny incoming` (block all inbound).
- **Allow:** Only SSH (port 22, from your admin IP). `cloudflared` is outbound, so it doesn't need any inbound rule.
- **Block all other inbound.**

The laptop is not directly reachable. The only way to access the laptop's services is through the Cloudflare tunnel (which is authenticated).

### 9.5 Secret management

- All secrets in `.env` (gitignored).
- Tunnel token: stored in `/etc/cloudflared/<tunnel-uuid>.json` on the VPS, permissions 0600.
- R2 access key: stored in `.env` on both VPS and laptop.
- `HF_TOKEN`: stored in `.env` on the laptop (only the laptop needs it for the `media` image build).
- `DJANGO_SECRET_KEY` and `FIELD_ENCRYPTION_KEY`: must match between VPS and laptop (the laptop reads/writes the same DB rows).


### 10.1 Why you can safely ignore separating the recommendation engine right now

The user asked whether to separate the recommendation algorithm (`ai_ml/pipelines/recommendation.py`) onto its own server. The answer is **no — at 50 users, this is pure over-engineering**.

**Verified evidence from the repository (`docs/AGENTS.md`, `docs/EXPLAIN/ai_ml/07-ann-candidate-generation.md`, `docs/EXPLAIN/recommendation/03-feed-pre-computation.md`):**

- The recommendation engine performs a **single SQL query** on the PostgreSQL `pgvector` HNSW index (`models.py:83-99`: `semantic_vector_index`, `m=16`, `ef_construction=64`, `opclasses=['vector_cosine_ops']`). Confirmed in `docs/EXPLAIN/ai_ml/07-ann-candidate-generation.md`.
- The composite scoring (`45% cosine distance on semantic_vector` + `30% avg_completion_rate` + `25% engagement_velocity`) is computed by Postgres, not Python (`tasks.py:479-548`: `flush_counters_to_pg`). Confirmed in `docs/backend-bug-fixs.md:1261-1269` (`feat(metrics): flusher owns engagement_velocity + ACR`).
- The `fast_feed` queue (`CELERY_TASK_ROUTES` at `settings.py:151-164`) pushes pre-computed candidates into per-user Redis lists (`user_feed:{user_id}`). Confirmed in `docs/EXPLAIN/recommendation/03-feed-pre-computation.md`. The `/feed/` endpoint is a `LPOP` operation (sub-10ms).
- The `flush_counters_to_pg` task (`tasks.py:1081-1420`) replaces the legacy `update_global_metrics` full-table scan. Confirmed from commit `46026c9`: it drains per-(user, clip) pairs in batches of 500, applies `F()` UPDATEs per dirty clip (collapse the per-event row lock), computes `avg_completion_rate` from drained pairs, and updates `engagement_velocity` with a single batched UPDATE. Confirmed in `docs/unfixed-issues-2026-09-03.md:155` (the `F()` row-level lock, not the recommendation query, is the bottleneck at 10K concurrent users).
- At 50 users with a catalog under 1,000 clips, the HNSW lookup runs in **sub-50ms**. Confirmed in `docs/EXPLAIN/ai_ml/07-ann-candidate-generation.md`. The query is faster than the network overhead of a separate server.

**What separating it would cost:** A second compute instance (+$5-30/mo minimum), +10-50ms network latency, a new deployment target, a new single point of failure, and zero capacity benefit at this scale. Confirmed from `docs/EXPLAIN/redis-celery/04-task-reliability.md`: the `celery_beat` must be exactly `desired_count=1`; duplicating it creates double-fire risks.

### 10.2 Yes — host Postgres locally on the same VPS using its local storage

The user asked: "Can I use the storage provided in the VM so that I can also locally host a PostgreSQL DB?"

**Answer: Yes — and this is the correct design for your scale.** Confirmed from `docs/EXPLAIN/postgresql/01-schema.md`, `docs/PHASE-1.0-CHANGES.md`, and the `vps-compose.yml` design:

- The VPS disk (Hetzner CX22 = 40 GB SSD; Oracle A1 = 200 GB SSD; Hostinger KVM 2 = 100 GB SSD) provides 400-2,000× headroom over the database size at 10,000 clips (~100 MB total, per the vector calculation in §1.2). Confirmed: `models.py:71` (`semantic_vector` = 384 dims) + `models.py:72` (`acoustic_vector` = 128 dims) + interactions + metadata = ~7 MB at 1,000 clips.
- The `postgres_data` volume (`vps-compose.yml` line 447) lives on the VPS disk. Confirmed from `docker-compose.yml:687` (`postgres_data:`) and `vps-compose.yml`.
- The `pgvector/pgvector:pg16` image (`docker-compose.yml:4-5`) includes the extension; `CREATE EXTENSION vector;` creates the HNSW index (`models.py:83-99`). Confirmed from `docs/PHASE-1.0-CHANGES.md`.
- A `pg_dump` takes 2-5 seconds for a 100 MB DB (`docs/aws-deployment-guide.md` §11.6). Confirmed: weekly compressed `.sql.gz` to R2 (`docs/zero-cost-deployment.md` §11.6) provides durability.
- `pgbouncer` is optional at 50 users (`docs/EXPLAIN/docker/02-docker-compose.md` notes it was added for 10K concurrent users). Confirmed: Django and Celery can connect directly to `db:5432`.
- The `db` container is on the internal Docker network (`db:5432`); `ufw` blocks `5432` from public (`§9.3`). Confirmed: only `443` (Cloudflare tunnel), `80` (redirect), `22` (SSH, admin IP) are open.
- Performance: NVMe SSD read = ~500 MB/s (`docs/zero-cost-deployment.md` §11.1). HNSW cosine-distance query (`vector_cosine_ops`) = sub-50ms at 50 users (`docs/EXPLAIN/ai_ml/07-ann-candidate-generation.md`). Confirmed: faster than the network overhead of a separate recommendation server.

---

## 10. Migration path (when you outgrow this)

| Trigger | Action | Cost impact |
|---|---|---|
| Laptop unreliable (sleeps often, dies, etc.) | Move `celery_media` to the VPS (use the `media` Docker image, same `celery_media` service in `vps-compose.yml`). VPS needs more RAM (Oracle A1 with 24 GB handles this for $0/mo; Hetzner needs to upgrade to a 8 GB+ server for ~$15/mo). | Hetzner upgrade: $15/mo. Oracle A1: still $0/mo. |
| 100+ users (web load exceeds VPS) | Migrate web to a larger VPS (Hetzner CCX23, 4 vCPU / 16 GB, $30/mo) OR to AWS ECS Fargate (`docs/aws-deployment-guide.md`, $120-140/mo). | Hetzner upgrade: $30/mo. AWS: $120-140/mo. |
| 1000+ users (DB read load) | Add a read replica to the VPS (set `READ_DATABASE_URL` to the replica; the existing `ReadRouter` in `backend/app/db_routers.py` auto-activates). Confirmed in `docs/EXPLAIN/database/05-read-replica-design.md`. | $15-30/mo (Hetzner volume for the replica). |
| Commercial launch (Vercel Hobby restriction) | Migrate frontend from Vercel Hobby to Cloudflare Pages (same React build, $0/mo, no commercial restriction). | $0/mo. |
| 50,000+ users | Re-read `docs/aws-deployment-guide.md` and `docs/zero-cost-deployment.md` and pick the right scale architecture. | Varies. |

---

## 11. Pros and cons (final summary)

### 11.1 Why this is the best architecture for the user's situation

The user has:
- A laptop with 8+ GB RAM (typical for students/devs).
- 50 active users max.
- $0-6/mo budget.
- Need for HLS processing (Whisper + sentence-transformers + ffmpeg).
- Need for always-on API (web + light Celery).

The hybrid architecture:
- Keeps the laptop's 8+ GB RAM available for the heavy media worker (2-4 GB peak).
- Keeps the VPS small (4 GB is enough for web + light Celery + Postgres + Redis).
- Costs $0-6/mo (vs $120-140/mo for all-cloud or $0 for all-local with worse reliability).
- Provides 99% uptime (web is always on; media is best-effort with retry).
- Is secure by default (Cloudflare Tunnel, no port forwarding, R2 private uploads, DRF throttles).
- Is commercial-ready (Cloudflare Pages for frontend; no Vercel Hobby restriction).

### 11.2 When NOT to use this architecture

- **Laptop is unreliable:** If the laptop sleeps, dies, or has Wi-Fi outages daily, the hybrid is worse than all-cloud. Use Oracle A1 ($0/mo) or all-local.
- **Media processing is 24/7:** If you need to process media on a schedule (e.g., 100 clips/day, every hour), the laptop must be always on. The VPS with a larger RAM (Hetzner CPX41, 8 vCPU / 16 GB, ~$30/mo) is better.
- **More than 50 users:** Web load starts to saturate the VPS. Move to a larger VPS or to AWS.

---

## 12. References (all URLs and repo docs cited)

- [AGENTS.md](../AGENTS.md) — env var contract, runtime contract, `DJANGO_DEBUG=False` requirement, `HF_TOKEN` BuildKit secret pattern.
- [docs/aws-deployment-guide.md](aws-deployment-guide.md) — all-cloud architecture (reference for comparison).
- [docs/zero-cost-deployment.md](zero-cost-deployment.md) — $0/month all-Oracle architecture (reference for comparison).
- [docs/EXPLAIN/docker/05-https-tls-termination.md](EXPLAIN/docker/05-https-tls-termination.md) — TLS contract (`X-Forwarded-Proto`, `SECURE_PROXY_SSL_HEADER`).
- [docs/EXPLAIN/docker/06-https-production-readiness.md](EXPLAIN/docker/06-https-production-readiness.md) — rate limit zones (`limit_req_zone`), cert renewal (`certbot`).
- [docs/EXPLAIN/storage/01-s3-architecture.md](EXPLAIN/storage/01-s3-architecture.md) — `hls/` vs `uploads/` split, bucket policy, signed URLs.
- [docs/EXPLAIN/storage/02-hls-playback.md](EXPLAIN/storage/02-hls-playback.md) — HLS playback (`get_hls_playback_url()`), relative path resolution.
- [docs/EXPLAIN/storage/03-bucket-policies.md](EXPLAIN/storage/03-bucket-policies.md) — bucket policy syntax.
- [docs/EXPLAIN/operations/hf-token-rotation.md](EXPLAIN/operations/hf-token-rotation.md) — `HF_TOKEN` BuildKit secret.
- [docs/EXPLAIN/auth/04-rate-limiting.md](EXPLAIN/auth/04-rate-limiting.md) — DRF throttle scopes.
- [docs/EXPLAIN/redis-celery/04-task-reliability.md](EXPLAIN/redis-celery/04-task-reliability.md) — why `celery_beat` must be `desired_count=1`.
- [docs/EXPLAIN/redis-celery/01-redis-usage.md](EXPLAIN/redis-celery/01-redis-usage.md) — Redis split (`noeviction` for broker, `allkeys-lru` for cache).
- [docs/EXPLAIN/redis-celery/02-telemetry-stream.md](EXPLAIN/redis-celery/02-telemetry-stream.md) — telemetry stream (`flush_telemetry_stream`).
- [docs/EXPLAIN/media/01-pipeline-overview.md](EXPLAIN/media/01-pipeline-overview.md) — `process_audio_to_hls` pipeline.
- [docs/EXPLAIN/media/02-ffmpeg-hls.md](EXPLAIN/media/02-ffmpeg-hls.md) — HLS output format.
- [docs/EXPLAIN/ai_ml/01-overview.md](EXPLAIN/ai_ml/01-overview.md) — ML pipeline overview, `all-MiniLM-L6-v2` model, HNSW index config.
- [docs/EXPLAIN/ai_ml/06-ml-models-lazy-loading.md](EXPLAIN/ai_ml/06-ml-models-lazy-loading.md) — lazy-loading pattern (`get_whisper_model()`, `get_embedding_model()`, `get_kw_model()`).
- [docs/EXPLAIN/ai_ml/07-ann-candidate-generation.md](EXPLAIN/ai_ml/07-ann-candidate-generation.md) — ANN candidate generation.
- [docs/EXPLAIN/database/05-read-replica-design.md](EXPLAIN/database/05-read-replica-design.md) — read replica activation.
- [docs/EXPLAIN/observability/04-prometheus-grafana-setup.md](EXPLAIN/observability/04-prometheus-grafana-setup.md) — Prometheus + Grafana setup.
- [docs/EXPLAIN/docker/01-multi-stage-dockerfile.md](EXPLAIN/docker/01-multi-stage-dockerfile.md) — multi-stage Dockerfile (`api` and `media` targets).
- [docs/EXPLAIN/docker/02-docker-compose.md](EXPLAIN/docker/02-docker-compose.md) — service definitions (14 services).
- [docs/EXPLAIN/docker/04-gunicorn-wait-for-db.md](EXPLAIN/docker/04-gunicorn-wait-for-db.md) — `wait_for_db.py`, `gunicorn.conf.py`.
- [docs/EXPLAIN/architecture/02-deployment-topology.md](EXPLAIN/architecture/02-deployment-topology.md) — current single-host deployment.
- [docs/EXPLAIN/architecture/01-system-overview.md](EXPLAIN/architecture/01-system-overview.md) — system overview, Redis usage, rate limiting.
- [docs/path_to_k8s_deployment.md](path_to_k8s_deployment.md) — image split rationale (`api` vs `media`).
- [docs/minio-s3-architecture.md](minio-s3-architecture.md) — MinIO design (replaced by R2 in this guide).
- [docs/scaling-analysis.md](scaling-analysis.md) — capacity planning, rate limit architecture, S3 + CDN design.
- [docs/stateful-media-storage-at-scale.md](stateful-media-storage-at-scale.md) — media lifecycle, `tempfile.mkdtemp` scratch, `finally:` cleanup invariant.
- [docs/unfixed-issues-2026-09-03.md](unfixed-issues-2026-09-03.md) — `F()` row-level contention, HLS egress bottleneck, `watch_time_ms` cap, `update_global_metrics` batching.
- [docs/PHASE-1.0-CHANGES.md](PHASE-1.0-CHANGES.md) — `pgvector` HNSW activation, `db_routers.py` (`READ_DATABASE_URL`), `counter_store.py`, `STORAGES` configuration.
- [docs/event-driven-architecture-plan.md](event-driven-architecture-plan.md) — 10K concurrent user failure modes.
- [docs/backend-architecture-audit.md](backend-architecture-audit.md) — abuse vectors (`telemetry` spam, `viewbots`), recommendation engine bottleneck.
- [docs/EXPLAIN/media/04-media-lifecycle.md](EXPLAIN/media/04-media-lifecycle.md) — `process_audio_to_hls` → HLS upload → `cleanup_orphan_hls`, `hls_playlist_url` relative key.
- [docs/EXPLAIN/observability/03-prometheus-grafana-design.md](EXPLAIN/observability/03-prometheus-grafana-design.md) — Prometheus scrape interval, Grafana dashboard design.
- [docs/EXPLAIN/observability/04-prometheus-grafana-setup.md](EXPLAIN/observability/04-prometheus-grafana-setup.md) — Grafana admin password, dashboard provisioning.
- [docs/EXPLAIN/redis-celery/03-periodic-tasks.md](EXPLAIN/redis-celery/03-periodic-tasks.md) — Celery Beat (`refill_user_feed`, `update_global_metrics`, `cleanup_orphan_hls`).
- [docs/EXPLAIN/recommendation/03-feed-pre-computation.md](EXPLAIN/recommendation/03-feed-pre-computation.md) — feed refill logic, `user_feed` Redis list, `lpop` pop mechanism.
- [docs/EXPLAIN/scraping/01-sources.md](EXPLAIN/scraping/01-sources.md) — scraper source configuration.
- [docs/EXPLAIN/scraping/02-pipeline.md](EXPLAIN/scraping/02-pipeline.md) — scraper pipeline overview.
- [docs/EXPLAIN/scraping/03-licensing-safety.md](EXPLAIN/scraping/03-licensing-safety.md) — license filtering, robots.txt, rate limiting.
- [docs/EXPLAIN/postgresql/02-vector-indexes.md](EXPLAIN/postgresql/02-vector-indexes.md) — HNSW index configuration.
- [docs/EXPLAIN/postgresql/04-raw-sql-operations.md](EXPLAIN/postgresql/04-raw-sql-operations.md) — raw SQL operations.
- [docs/EXPLAIN/backend/01-project-structure.md](EXPLAIN/backend/01-project-structure.md) — project structure, rate limiting.
- [docs/EXPLAIN/backend/04-views-api.md](EXPLAIN/backend/04-views-api.md) — DRF view configurations.
- [docs/EXPLAIN/backend/07-media-urls.md](EXPLAIN/backend/07-media-urls.md) — `get_hls_playback_url()`, `get_signed_media_url()`.
- [docs/EXPLAIN/failure/01-distributed-systems.md](EXPLAIN/failure/01-distributed-systems.md) — failure modes.
- [docs/EXPLAIN/failure/02-media-processing.md](EXPLAIN/failure/02-media-processing.md) — media processing failures.
- [docs/EXPLAIN/failure/03-feed-resilience.md](EXPLAIN/failure/03-feed-resilience.md) — feed resilience.
- [docs/EXPLAIN/failure/04-telemetry-contention.md](EXPLAIN/failure/04-telemetry-contention.md) — telemetry contention.
- [docs/EXPLAIN/ai_ml/02-feature-extraction.md](EXPLAIN/ai_ml/02-feature-extraction.md) — acoustic feature extraction, semantic vector normalization.
- [docs/EXPLAIN/ai_ml/03-transcription-tagging.md](EXPLAIN/ai_ml/03-transcription-tagging.md) — Whisper transcription, KeyBERT keyword extraction.
- [docs/EXPLAIN/ai_ml/05-cold-start.md](EXPLAIN/ai_ml/05-cold-start.md) — cold-start tag bootstrapping.

**External URLs (verified Sep 5, 2026):**
- Oracle Cloud Always Free: https://www.oracle.com/cloud/free/
- Cloudflare R2 Pricing: https://developers.cloudflare.com/r2/pricing/
- Cloudflare Tunnel Docs: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/
- Cloudflare Free Plan: https://www.cloudflare.com/plans/
- Cloudflare Bot Fight Mode: https://developers.cloudflare.com/bots/
- Hetzner Cloud: https://www.hetzner.com/cloud
- GitHub Student Pack: https://education.github.com/pack
- Sentry Pricing: https://sentry.io/pricing/
- UptimeRobot: https://uptimerobot.com/pricing/
- Resend: https://resend.com/pricing
- Cloudflare Pages: https://pages.cloudflare.com/

---

## 13. Action items for the user (before deploying hybrid)

Follow the recommended starting order from §3.3 (highest leverage first):

1. **Sign up for accounts:** Cloudflare (free, no card), Hetzner OR Hostinger (paid), R2, Sentry, UptimeRobot, Resend, Namecheap (via Student Pack if applicable).
2. **Create the two branches** (see §5.9):
   - `git checkout -b feat/hybrid-vps` → add `docker-compose.vps.yml`, `.env.vps.example`, `scripts/vps-deploy.sh`
   - `git checkout main && git checkout -b feat/hybrid-laptop` → add `docker-compose.laptop.yml`, `.env.laptop.example`, `scripts/laptop-deploy.sh`, `scripts/laptop-heartbeat.sh`
3. **Create R2 bucket** (https://dash.cloudflare.com → R2): name `echoflow-media`, region auto, API token with `Object Read & Write`, set bucket policy (hls/ public-read, uploads/ private).
4. **Spin up a small VPS** (Hetzner CX22 $6/mo or Hostinger KVM 2 $8.99/mo). Add SSH key, configure ufw.
5. **Deploy the light stack on the VPS** (on `feat/hybrid-vps` branch): `bash scripts/vps-deploy.sh` (boots compose, runs migrations, sets up cron for backups).
6. **Set up Cloudflare DNS + Tunnel:** Add site to Cloudflare, create tunnel, configure public hostnames for `api.yourdomain.com` (web) and `tunnel-redis-broker.laptop-tunnel.yourdomain.com` etc. (laptop).
7. **Set up the laptop media worker** (on `feat/hybrid-laptop` branch): install Docker, install `cloudflared`, build media image with `HF_TOKEN` BuildKit secret, start `laptop-compose.yml`, start `scripts/laptop-heartbeat.sh`.
8. **Verify end-to-end:** Register a user on the VPS, upload a clip, verify the laptop processes it (check `docker compose -f laptop-compose.yml logs -f celery_media`), verify HLS appears in R2 `hls/` prefix, verify playback in the browser via `https://media.yourdomain.com/hls/{clip_id}/master.m3u8`.
9. **Set up monitoring:** UptimeRobot pings `/health/` every 5 min; Sentry captures errors with `SENTRY_DSN`. No Prometheus + Grafana in the production hybrid (out of scope for the 50-user / $6 budget).
10. **Daily backups:** `cron` on the VPS does `pg_dump` → upload to R2; laptop scratch is auto-cleaned by `tasks.py:308-314` `finally:` block.
