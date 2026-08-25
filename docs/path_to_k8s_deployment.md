What You're Doing That Wastes Space
1. Installing PyTorch in the API image — The Django web server never needs PyTorch. It only runs in Celery heavy_media workers. But your single Dockerfile builds one image used by ALL services (web, celery, celery_feed, celery_media, celery_beat).
2. Installing ML deps in the same image as the API — faster-whisper, sentence-transformers, keybert, librosa are only needed for media processing. They bloat every container, including the gunicorn web server and the feed refill worker (which only does Redis + DB queries).
3. Not using multi-stage builds — One image for everything means every service carries the full ML stack.
4. Installing build tools that persist — gcc, libpq-dev are only needed to compile psycopg2-binary. They can be removed after install.
5. Not pinning Python version in Docker — You have .pyc files for 3.11, 3.12, 3.13 locally. If your build host uses a different Python, incompatible binaries get baked in.
6. HuggingFace model cache during build — If HF_TOKEN is set and any import triggers model download, those weights (Whisper ~1GB, all-MiniLM ~90MB) get cached in the image layer.
7. pip install without --no-cache-dir on all installs — pip caches wheels. Without clearing, the cache persists in the image layer.
How to Optimize (Before Kubernetes)
Strategy 1: Split Into Two Images
Image A: echoflow-api (lightweight, ~500 MB)
- Base: python:3.11-slim
- Contains: Django, DRF, psycopg2, redis, celery (without ML), gunicorn, whitenoise
- Does NOT contain: PyTorch, librosa, faster-whisper, sentence-transformers, keybert
- Used by: web, celery_feed, celery_beat
Image B: echoflow-media (heavy, ~4-5 GB)
- Base: python:3.11-slim
- Contains: Everything in Image A + PyTorch, librosa, faster-whisper, sentence-transformers, keybert, FFmpeg
- Used by: celery_media only
# Dockerfile.api (for web, celery_feed, celery_beat)
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev gcc && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app/
RUN find /app -type d -name __pycache__ -exec rm -rf {} +
RUN pip uninstall -y torch sentence-transformers faster-whisper librosa keybert scipy numpy 2>/dev/null || true
USER appuser

# Dockerfile.media (for celery_media)
FROM echoflow-api AS base
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
USER appuser
Expected result: API image drops from 10GB to ~500MB. Media image stays ~4-5GB but only one container uses it.
Strategy 2: Lazy-Load Models at Runtime, Not Build Time
# Don't let model downloads happen during build
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
# Models are downloaded on first task execution, stored in named volume
VOLUME /root/.cache/huggingface
This ensures model weights are never baked into the image layer. They're downloaded at runtime and stored in a Docker volume.
Strategy 3: Remove Unused Dependencies
Your requirements.txt has packages you don't use in production:
- openai — commented-out code path
- SpeechRecognition — not used
- gtts — not used
- starlette — not used
- httpx — not used
- django-apscheduler — you use Celery Beat, not APScheduler
- django-allauth / dj-rest-auth — you use SimpleJWT
- librosa listed twice
Moving these to requirements-dev.txt or removing them saves ~200-300 MB.
Strategy 4: Clean Build Artifacts
RUN apt-get purge -y gcc libpq-dev && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
Build tools (gcc) are only needed to compile psycopg2-binary. Remove them after pip install.
Strategy 5: Use dive to Inspect Layers
dive your-image-name
This shows you exactly which layers contribute the most size and which files can be removed.
What Changes With Kubernetes
Architecture Shift
Current (Docker Compose):
  ┌─────────────────────────────────────────────┐
  │  Single host: web + 4 celery workers + db   │
  │  All on one machine, one Docker image       │
  └─────────────────────────────────────────────┘

Kubernetes:
  ┌──────────────────────────────────────────────────────────┐
  │  Pod: echoflow-api (3-10 replicas, HPA)                  │
  │  Pod: echoflow-media (2-20 replicas, GPU-capable)        │
  │  Pod: echoflow-feed-worker (2-5 replicas)                │
  │  Pod: echoflow-beat (1 replica)                          │
  │  External: RDS PostgreSQL, ElastiCache Redis, S3/CDN     │
  └──────────────────────────────────────────────────────────┘
What You Need to Learn
Kubernetes Core Concepts
Concept	What It Is	Why It Matters for EchoFlow
Pod	Smallest deployable unit (1+ containers)	Each service (api, media, feed) is a Pod
Deployment	Manages Pod replicas, rolling updates	Scale API from 3 to 10 pods automatically
Service	Stable network endpoint for Pods	Route traffic to API pods behind ingress
Ingress	HTTP/L7 load balancer	Entry point for all API requests
HPA (Horizontal Pod Autoscaler)	Auto-scale based on CPU/memory/custom metrics	Scale media workers when queue depth is high
ConfigMap	Non-secret configuration	Store Django settings, Celery config
Secret	Encrypted sensitive data	Store DB password, JWT secret, HF token
PersistentVolume (PV)	Storage provisioned by cluster	PostgreSQL data, media storage
PersistentVolumeClaim (PVC)	Request for storage	Claim PV for PostgreSQL
Liveness Probe	"Is the container alive?"	Restart crashed gunicorn processes
Readiness Probe	"Is the container ready to serve?"	Don't send traffic until Django is migrated
Resource Requests/Limits	CPU/memory guarantees and caps	Prevent one Pod from starving others
Container Registry
Concept	What It Is
ECR / GCR / Docker Hub	Store your optimized images
Image tagging	echoflow-api:1.2.3, echoflow-media:1.2.3
CI/CD pipeline	Build → test → push to registry → deploy
Kubernetes Networking
Concept	What It Is
ClusterIP	Internal-only service (PostgreSQL, Redis)
NodePort	Expose on node IP (not recommended for prod)
LoadBalancer	Cloud provider external IP (for Ingress)
Ingress Controller	NGINX, Traefik, or cloud provider LB
Storage
Concept	What It Is
EmptyDir	Ephemeral, cleared when Pod dies (model cache)
HostPath	Mount host directory (not recommended)
PersistentVolumeClaim	Request storage from cluster (PostgreSQL data)
Ephemeral volumes	For model cache, temp files
What to Improve Before Kubernetes
1. Add health check endpoint (/health/) — Kubernetes needs liveness/readiness probes. Without them, K8s can't tell if your app is working.
2. Separate concerns into different Dockerfiles — The API image and media image split is critical. K8s will schedule pods on different node types (CPU vs GPU).
3. Add resource requests/limits — Tell K8s how much CPU/memory each Pod needs:
resources:
  requests:
    cpu: "500m"
    memory: "512Mi"
  limits:
    cpu: "2"
    memory: "2Gi"
4. Use ConfigMaps and Secrets — Never hardcode env vars. Store them in K8s resources.
5. Add structured logging — K8s aggregates logs from all Pods. JSON-formatted logs with trace IDs are essential.
6. Add metrics endpoint — Prometheus scraping for HPA decisions (queue depth, latency, error rate).
7. Implement graceful shutdown — Django needs to handle SIGTERM properly. Stop accepting new requests, finish in-flight requests, then exit.
8. Use named volumes for model cache — HuggingFace models should be in a shared volume, not downloaded per-Pod.
Estimated Sizes After Optimization
Image	Current	After Optimization
echoflow-api	10 GB (single image)	~400-600 MB
echoflow-media	10 GB (same image)	~3-5 GB
Total pull size	10 GB (one image, 5x replicated)	~2-3 GB (two images, used selectively)
Summary of Actions
Immediate (no K8s needed)
1. Split into Dockerfile.api and Dockerfile.media (multi-stage or separate files)
2. Remove unused dependencies from requirements.txt
3. Add HF_HUB_OFFLINE=1 during build, download models at runtime
4. Clean build tools (gcc) after pip install
5. Use dive to verify layer sizes
Pre-Kubernetes
 6. Add /health/ endpoint with liveness/readiness logic
 7. Add resource requests/limits to docker-compose (practice for K8s)
 8. Configure structured JSON logging
 9. Add Prometheus metrics endpoint (django-prometheus)
10. Implement graceful shutdown in Gunicorn
Kubernetes Learning Path
11. Learn: Pods, Deployments, Services, Ingress
12. Learn: ConfigMaps, Secrets, Probes, Resource limits
13. Learn: HPA, PersistentVolumes, Namespaces
14. Learn: Helm or Kustomize for templating
15. Learn: CI/CD with GitHub Actions → ECR → EKS
Long-Term Architecture
16. Move media to S3 + CDN (not local disk)
17. Add PgBouncer for connection pooling
18. Add Sentry for error tracking
19. Add rate limiting (Redis-based)
20. Consider GPU nodes for celery_media when scaling