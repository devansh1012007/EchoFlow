# EchoFlow — AWS Deployment Guide

> Small-scale, production-grade deployment of EchoFlow to AWS.
> Scope: push images to **Amazon ECR**, run on **Amazon ECS Fargate** (with a **single-EC2 budget alternative**), use **RDS PostgreSQL 16 + pgvector**, **ElastiCache Redis 7**, **Amazon S3** for media, and front the API with **Application Load Balancer + AWS WAF + Shield Standard** for TLS and bot resistance.

This guide maps the current 14-service Docker Compose stack (`docker-compose.yml`) to AWS equivalents, gives both **AWS CLI** and **Terraform** snippets, and is optimised for **low cost** and **security at small scale**.

> **Prerequisite reading:** [AGENTS.md](../AGENTS.md) (env vars, runtime contracts), [docs/path_to_k8s_deployment.md](path_to_k8s_deployment.md) (image-split rationale), [docs/EXPLAIN/docker/05-https-tls-termination.md](EXPLAIN/docker/05-https-tls-termination.md) (TLS contract), [docs/EXPLAIN/storage/01-s3-architecture.md](EXPLAIN/storage/01-s3-architecture.md) (S3 split), [docs/EXPLAIN/operations/hf-token-rotation.md](EXPLAIN/operations/hf-token-rotation.md) (HF_TOKEN secret), [docs/backend-architecture-audit.md](backend-architecture-audit.md) (bottlenecks we are not solving here).

---

## 1. Architecture Mapping (Docker Compose → AWS)

| Docker Compose service | Image / Purpose | AWS equivalent (small-scale) |
|---|---|---|
| `db` (pgvector/pgvector:pg16) | PostgreSQL 16 + pgvector, 2 GB limit | **Amazon RDS for PostgreSQL 16** (db.t4g.micro, 20 GB gp3, `pgvector` extension) |
| `pgbouncer` | Transaction-pooled front for `db` | **RDS Proxy** (transaction mode) or drop entirely (RDS is small) |
| `redis_broker` (noeviction, 512 MB) | Celery broker, telemetry stream | **ElastiCache for Redis 7** (cache.t4g.micro, 1 GB, noeviction) |
| `redis_cache` (allkeys-lru, 3 GB) | Django cache + per-user feed lists | **ElastiCache for Redis 7** (cache.t4g.small, 2 GB, allkeys-lru) — **separate** cluster |
| `minio` + `minio-init` | S3-compatible object storage | **Amazon S3** (one bucket, two prefixes) |
| `nginx` (TLS terminator) | `:80/443/9443` reverse proxy | **Application Load Balancer (ALB)** + **AWS Certificate Manager (ACM)** |
| `web` (gunicorn, `:8000`) | Django API | **ECS Fargate service** `web` (task with `api` image, port 8000) |
| `celery` (default queue) | Background tasks | **ECS Fargate service** `celery` (Spot) |
| `celery_feed` (`-Q fast_feed`) | Feed refill | **ECS Fargate service** `celery-feed` (Spot) |
| `celery_media` (`-Q heavy_media`, Whisper+ST+KeyBERT) | HLS + AI pipeline | **ECS Fargate service** `celery-media` (Spot, 4 GB / 2 vCPU) |
| `celery_beat` (scheduler) | Periodic tasks | **ECS Fargate service** `celery-beat` (1 task, no Spot) |
| `prometheus` | Metrics scrape | **Amazon Managed Prometheus** (free tier) — *or skip for small scale, use CloudWatch + `/metrics/` logs* |
| `grafana` | Dashboards | **Amazon Managed Grafana** (free workspace) — *or skip for small scale* |

**Why two Redis clusters?** The same reason as Compose: the broker cannot tolerate eviction under memory pressure (`noeviction`); the cache is safe to evict (`allkeys-lru`) because feed refill and telemetry flush are idempotent. See `docker-compose.yml:35-40` for the original rationale. Two `cache.t4g.micro/nano` nodes is ~$12/mo each.

**Why S3 over MinIO in prod?** MinIO's `docker-compose.yml` is dev-parity for real S3 (see `docs/EXPLAIN/storage/01-s3-architecture.md`). The app already uses `boto3` and `django-storages`; the only change is `AWS_S3_ENDPOINT_URL=` (empty for real S3).

---

## 2. Target AWS Architecture (Diagram)

```
                    Internet
                       │
            ┌──────────▼──────────┐
            │  Route 53 (DNS)     │   api.echoflow.example
            └──────────┬──────────┘
                       │
            ┌──────────▼──────────┐
            │  AWS Shield Standard│   free, L3/L4 DDoS
            └──────────┬──────────┘
                       │
            ┌──────────▼──────────┐
            │  AWS WAF v2         │   Bot Control + rate-limit rules
            │  + Web ACL          │   (see §6 for rule mapping)
            └──────────┬──────────┘
                       │
            ┌──────────▼──────────┐
            │  ALB (ACM TLS)      │   :443 (TLS 1.2/1.3) → ECS
            │                     │   :80 → :443 redirect
            │  X-Forwarded-Proto  │
            └──────────┬──────────┘
                       │ HTTP
        ┌──────────────┼──────────────────────────┐
        │              │                          │
┌───────▼─────┐ ┌──────▼──────┐ ┌─────────────────▼────────────┐
│  ECS: web   │ │ ECS: celery │ │  ECS: celery-media/feed/beat│
│ (Fargate)   │ │ (Spot)      │ │  (Spot)                      │
│ api image   │ │ api image   │ │  media image for celery-media│
└──────┬──────┘ └──────┬──────┘ └────────────┬─────────────────┘
       │                │                     │
       └────────────────┴──────────┬──────────┘
                                   │
            ┌──────────────────────┼──────────────────────┐
            │                      │                      │
   ┌────────▼─────────┐  ┌─────────▼────────┐  ┌─────────▼────────┐
   │ RDS PostgreSQL 16│  │ ElastiCache Redis│  │ Amazon S3        │
   │ (db.t4g.micro)   │  │ broker + cache   │  │ echoflow-media   │
   │ pgvector ext     │  │ (cache.t4g.micro)│  │  hls/ public-read│
   │ private subnet   │  │ private subnet   │  │  uploads/ private│
   └──────────────────┘  └──────────────────┘  └──────────────────┘

   Secrets Manager  ←  DJANGO_SECRET_KEY, FIELD_ENCRYPTION_KEY,
                        HF_TOKEN (build secret), DB credentials
```

---

## 3. Environment Variables & Secrets Management

Secrets are split into **AWS Secrets Manager** (rotated, sensitive) and **Systems Manager Parameter Store** (non-sensitive config). ECS task definitions reference them via `secrets:` (Secrets Manager) and `environment:` (Parameter Store). See `docs/EXPLAIN/docker/03-environment-variables.md` for the canonical variable list.

| Variable | Source | Secret Manager? | Notes |
|---|---|---|---|
| `DJANGO_SECRET_KEY` | Secrets Manager | ✅ | Generate with `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"` |
| `FIELD_ENCRYPTION_KEY` | Secrets Manager | ✅ | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DATABASE_URL` | Secrets Manager (JSON) | ✅ | Includes RDS password; or split: host/user in Parameter Store, password in Secrets Manager |
| `REDIS_BROKER_URL` | Secrets Manager | ✅ | `redis://:password@<broker>.clustercfg.<region>.cache.amazonaws.com:6379/0` |
| `REDIS_CACHE_URL` | Secrets Manager | ✅ | Same pattern, second cluster |
| `AWS_ACCESS_KEY_ID` | **Task IAM role** (preferred) | ❌ | Do **not** put in env; use ECS task role with `AmazonS3FullAccess` (or custom policy, §8.4) |
| `AWS_SECRET_ACCESS_KEY` | **Task IAM role** (preferred) | ❌ | Same — task role supplies creds via IMDS |
| `AWS_STORAGE_BUCKET_NAME` | Parameter Store | ❌ | `echoflow-media` |
| `AWS_S3_ENDPOINT_URL` | Parameter Store | ❌ | **Leave BLANK** for real S3 (code falls back to `https://s3.<region>.amazonaws.com`) |
| `AWS_S3_REGION_NAME` | Parameter Store | ❌ | e.g. `us-east-1` |
| `AWS_S3_QUERYSTRING_EXPIRE` | Parameter Store | ❌ | `3600` |
| `PUBLIC_MEDIA_ENDPOINT_URL` | Parameter Store | ❌ | `https://cdn.echoflow.example` (CloudFront) or ALB DNS in dev |
| `HF_TOKEN` | **Build secret only** | BuildKit secret | See `docs/EXPLAIN/operations/hf-token-rotation.md`; never set as runtime env |
| `DJANGO_DEBUG` | Parameter Store | ❌ | `False` (required for `SECURE_SSL_REDIRECT` to work) |
| `DJANGO_ALLOWED_HOSTS` | Parameter Store | ❌ | `api.echoflow.example` |
| `DJANGO_CORS_ALLOWED_ORIGINS` | Parameter Store | ❌ | `https://app.echoflow.example` (must be `https://`) |
| `GUNICORN_WORKERS` | Parameter Store | ❌ | `2` (Fargate has 0.5 vCPU / 1 GB) |
| `GUNICORN_THREADS` | Parameter Store | ❌ | `4` |
| `SENTRY_DSN` | Secrets Manager | ✅ | Optional |
| `SENTRY_ENV` | Parameter Store | ❌ | `production` |
| `SCRAPER_*` / `FREESOUND_API_KEY` | Parameter Store / Secrets Manager | mixed | `FREESOUND_API_KEY` → Secrets; rest → Parameter Store |

**Why IAM roles instead of static AWS keys?** ECS task roles supply temporary credentials via IMDS. Long-lived `AWS_ACCESS_KEY_ID`/`SECRET` are a leak vector and a bot-scan target (see §6.2).

---

## 4. TLS / Certificate Management

We **replace `nginx` with ALB + ACM**. The Django `if not DEBUG:` block (`backend/EchoFlow/settings.py:529-539`) already enforces:

- `SECURE_SSL_REDIRECT=True`
- `SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https')`
- `SESSION_COOKIE_SECURE=True`
- `CSRF_COOKIE_SECURE=True`
- HSTS 1 year + includeSubDomains + preload

ALB must be configured to send `X-Forwarded-Proto: https` on every request, otherwise Django's redirect loop breaks (same failure mode as the local nginx setup — see [docs/EXPLAIN/docker/05-https-tls-termination.md](EXPLAIN/docker/05-https-tls-termination.md)).

### 4.1 Provision a certificate with ACM

```bash
# CLI
aws acm request-certificate \
  --domain-name api.echoflow.example \
  --validation-method DNS \
  --region us-east-1
# Add the CNAME record ACM returns to Route 53 (or your DNS).
```

```hcl
# Terraform
resource "aws_acm_certificate" "api" {
  domain_name       = "api.echoflow.example"
  validation_method = "DNS"
  lifecycle { create_before_destroy = true }
}
```

### 4.2 ALB listener — HTTPS only, HTTP→HTTPS redirect

```hcl
resource "aws_lb" "echoflow" {
  name               = "echoflow-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.echoflow.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.api.arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

resource "aws_lb_listener" "redirect" {
  load_balancer_arn = aws_lb.echoflow.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}
```

ALB's default behavior already adds `X-Forwarded-Proto: https` to upstream requests when the listener is HTTPS. No code changes needed.

---

## 5. Container Registry (ECR) & Image Build

The repo's `Dockerfile` is **multi-stage** with two final targets: `api` and `media` (see `Dockerfile` and `docs/path_to_k8s_deployment.md:11-38`). We push **both** to ECR.

### 5.1 ECR repositories

```bash
aws ecr create-repository --repository-name echoflow/api    --region us-east-1
aws ecr create-repository --repository-name echoflow/media  --region us-east-1
```

```hcl
resource "aws_ecr_repository" "api"   { name = "echoflow/api"   }
resource "aws_ecr_repository" "media" { name = "echoflow/media" }
```

### 5.2 Build + push (CI / local)

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGION=us-east-1

# Login once
aws ecr get-login-password --region $REGION | \
  docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com

# Build the API image (no HF_TOKEN needed for the api target)
docker build --target api   -t $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/echoflow/api:1.0.0   .
docker push  $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/echoflow/api:1.0.0

# Build the MEDIA image (HF_TOKEN as a BuildKit SECRET, never ARG — see
# docs/EXPLAIN/operations/hf-token-rotation.md)
export HF_TOKEN=hf_xxx
docker build --target media -t $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/echoflow/media:1.0.0 \
  --secret id=hf_token,env=HF_TOKEN .
docker push  $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/echoflow/media:1.0.0
```

> **SECURITY:** `--secret` (BuildKit mount) never persists the token in a layer. `--build-arg HF_TOKEN=…` would leak it via `docker history`. See `Dockerfile:119-120`.

---

## 6. Security & Bot Resistance

Bots, scanners, and viewbots are the #1 abuse vector for short-form apps (`docs/backend-architecture-audit.md:142`). The defence has three layers.

### 6.1 Defence-in-depth layers

| Layer | Service | Stops | Cost |
|---|---|---|---|
| L3/L4 DDoS | **AWS Shield Standard** (auto-attached to ALB) | volumetric floods, SYN flood, UDP amplification | Free |
| L7 bot / SQLi / XSS | **AWS WAF v2 — Web ACL** | OWASP Top 10, known bad bots, scrapers | ~$5/mo + $0.60 per million requests |
| L7 Bot Control (managed) | **AWS WAF Bot Control** (paid rule group) | sophisticated bots that bypass simple rules | $10/mo + $0.10 per thousand requests |
| L7 Rate limit (per-IP) | **AWS WAF Rate-based rule** | spam, credential stuffing, telemetry spam | $0.20 per million requests |
| App-layer | **DRF `ScopedRateThrottle`** | per-user abuse, viewbot fraud | Free (Redis) |

> **Small-scale budget note:** Start with **Shield Standard + WAF Core (SQLi/XSS) + 3 rate-based rules** (~$5–10/mo). Add **Bot Control** only if you see suspicious traffic in WAF logs.

### 6.2 Map DRF throttle scopes to WAF rate-based rules

The DRF throttling is already configured (`backend/EchoFlow/settings.py:359-369`; see [docs/EXPLAIN/auth/04-rate-limiting.md](EXPLAIN/auth/04-rate-limiting.md)):

| DRF scope | Limit | WAF rate-based rule (per IP) |
|---|---|---|
| `register` | 5/hr | `POST /auth/register/`  → 30/hr |
| `login` | 10/min | `POST /auth/login/`    → 60/hr |
| `upload` | 20/hr | `POST /clips/`         → 60/hr |
| `telemetry` | 60/min | `POST /interactions/.../log-telemetry/` → 600/hr |
| `comment` | 60/hr | `POST /comments/`      → 200/hr |
| `interaction` | 60/min | `POST /interactions/.../toggle-like/`, `/register-skip/` → 600/hr |
| `share_send` | 100/hr | `POST /share/.../send-share/` → 200/hr |

```hcl
# WAF rate-based rule example (telemetry endpoint)
resource "aws_wafv2_web_acl" "echoflow" {
  name  = "echoflow-waf"
  scope = "REGIONAL"
  default_action { allow {} }

  rule {
    name     = "rate-limit-telemetry"
    priority = 1
    action { block {} }
    statement {
      rate_based_statement {
        limit              = 600        # 600 per 5-min window = 2/sec sustained
        aggregate_key_type = "IP"
        scope_down_statement {
          byte_match_statement {
            field_to_match { uri_path {} }
            positional_constraint = "CONTAINS"
            search_string         = "/log-telemetry/"
            text_transformation { priority = 0; type = "NONE" }
          }
        }
      }
    }
    visibility_config { cloudwatch_metrics_enabled = true; metric_name = "rate-limit-telemetry"; sampled_requests_enabled = true }
  }

  rule {
    name     = "aws-managed-common"
    priority = 10
    override_action { none {} }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"
      }
    }
    visibility_config { cloudwatch_metrics_enabled = true; metric_name = "aws-managed-common"; sampled_requests_enabled = true }
  }

  rule {
    name     = "aws-known-bad-inputs"
    priority = 20
    override_action { none {} }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
      }
    }
    visibility_config { cloudwatch_metrics_enabled = true; metric_name = "aws-known-bad-inputs"; sampled_requests_enabled = true }
  }

  visibility_config { cloudwatch_metrics_enabled = true; metric_name = "echoflow-waf"; sampled_requests_enabled = true }
}

# Attach to ALB
resource "aws_wafv2_web_acl_association" "echoflow" {
  resource_arn = aws_lb.echoflow.arn
  web_acl_arn  = aws_wafv2_web_acl.echoflow.arn
}
```

### 6.3 Security Groups (network isolation)

```hcl
# ALB: public, accepts 80/443 from internet
resource "aws_security_group" "alb" {
  ingress { from_port = 80  ; to_port = 80  ; protocol = "tcp"; cidr_blocks = ["0.0.0.0/0"] }
  ingress { from_port = 443 ; to_port = 443 ; protocol = "tcp"; cidr_blocks = ["0.0.0.0/0"] }
  egress  { from_port = 0   ; to_port = 0   ; protocol = "-1";  cidr_blocks = ["0.0.0.0/0"] }
}

# ECS tasks: only accept from ALB
resource "aws_security_group" "ecs" {
  ingress { from_port = 8000; to_port = 8000; protocol = "tcp"; security_groups = [aws_security_group.alb.id] }
  egress  { from_port = 0   ; to_port = 0   ; protocol = "-1";  cidr_blocks = ["0.0.0.0/0"] }
}

# RDS: only from ECS
resource "aws_security_group" "rds" {
  ingress { from_port = 5432; to_port = 5432; protocol = "tcp"; security_groups = [aws_security_group.ecs.id] }
}

# ElastiCache: only from ECS
resource "aws_security_group" "redis" {
  ingress { from_port = 6379; to_port = 6379; protocol = "tcp"; security_groups = [aws_security_group.ecs.id] }
}
```

### 6.4 S3 bucket policy (hls/ public-read, uploads/ private)

`hls/` must be **public-read** because the HLS multi-file protocol resolves relative paths in the player without query-string forward (`docker-compose.yml:208-222`; [docs/EXPLAIN/storage/01-s3-architecture.md](EXPLAIN/storage/01-s3-architecture.md)). `uploads/` stays **private** with signed URLs from `media_urls.py`.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowECSTaskRole",
      "Effect": "Allow",
      "Principal": { "AWS": "arn:aws:iam::ACCOUNT:role/echoflow-ecs-task" },
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
      "Resource": ["arn:aws:s3:::echoflow-media", "arn:aws:s3:::echoflow-media/*"]
    },
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

Block Public Access (BPA) is **on at the account level** for safety, then the bucket policy above explicitly grants the `hls/*` read. Do **not** turn off BPA for the whole account.

```hcl
resource "aws_s3_bucket_public_access_block" "echoflow" {
  bucket                  = aws_s3_bucket.media.id
  block_public_acls       = true
  block_public_policy     = false  # bucket policy with hls/* is intentional
  ignore_public_acls      = true
  restrict_public_buckets = false
}
```

---

## 7. RDS, ElastiCache, S3 (managed services)

### 7.1 RDS PostgreSQL 16 + pgvector

```hcl
resource "aws_db_instance" "echoflow" {
  identifier              = "echoflow-db"
  engine                  = "postgres"
  engine_version          = "16"
  instance_class          = "db.t4g.micro"     # ~$15/mo
  allocated_storage       = 20
  storage_type            = "gp3"
  db_name                 = "echoflow_db"
  username                = "echoflow"
  password_arns           = [aws_secretsmanager_secret_version.db_password.arn] # RDS IAM auth
  vpc_security_group_ids  = [aws_security_group.rds.id]
  db_subnet_group_name    = aws_db_subnet_group.echoflow.name
  skip_final_snapshot     = true               # set false in real prod
  backup_retention_period = 7
  parameter_group_name    = aws_db_parameter_group.echoflow.name
}

# pgvector requires shared_preload_libraries unset; extension is created at runtime.
resource "aws_db_parameter_group" "echoflow" {
  name   = "echoflow-pg16"
  family = "postgres16"
  parameter { name = "log_min_duration_statement"; value = "1000" } # log slow queries
}
```

After RDS is up, create the extension **once** (the app's `migrate` does not do this — pgvector must be created per database):

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:ACCOUNT:cluster:echoflow-db \
  --secret-arn arn:aws:secretsmanager:us-east-1:ACCOUNT:secret:echoflow-db-pw \
  --sql "CREATE EXTENSION IF NOT EXISTS vector;"
```

> The `migrate` step in the `web` task already runs on container start (`docker-compose.yml:259`; equivalent in ECS is a `command` override or an init container).

### 7.2 ElastiCache Redis 7 (two clusters)

```hcl
resource "aws_elasticache_subnet_group" "echoflow" {
  name       = "echoflow-redis"
  subnet_ids = var.private_subnet_ids
}

resource "aws_elasticache_replication_group" "broker" {
  replication_group_id = "echoflow-broker"
  engine               = "redis"
  engine_version       = "7.0"
  node_type            = "cache.t4g.micro"    # ~$12/mo
  num_cache_clusters   = 1
  parameter_group_name = "default.redis7"
  subnet_group_name    = aws_elasticache_subnet_group.echoflow.name
  security_group_ids   = [aws_security_group.redis.id]
  transit_encryption_enabled = true
  auth_token_arns      = [aws_secretsmanager_secret_version.redis_broker.arn]
}

resource "aws_elasticache_replication_group" "cache" {
  replication_group_id = "echoflow-cache"
  engine               = "redis"
  engine_version       = "7.0"
  node_type            = "cache.t4g.small"    # ~$25/mo
  num_cache_clusters   = 1
  parameter_group_name = "default.redis7"
  subnet_group_name    = aws_elasticache_subnet_group.echoflow.name
  security_group_ids   = [aws_security_group.redis.id]
  transit_encryption_enabled = true
  auth_token_arns      = [aws_secretsmanager_secret_version.redis_cache.arn]
}
```

`REDIS_BROKER_URL` and `REDIS_CACHE_URL` go into Secrets Manager with the format:

```
redis://:AUTH@<replication-group>.clustercfg.<region>.cache.amazonaws.com:6379/0
```

### 7.3 S3 bucket

```hcl
resource "aws_s3_bucket" "media" {
  bucket = "echoflow-media-${var.env}"
  force_destroy = false
}

resource "aws_s3_bucket_versioning" "media" {
  bucket = aws_s3_bucket.media.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_lifecycle_configuration" "media" {
  bucket = aws_s3_bucket.media.id
  rule {
    id     = "hls-intelligent"
    status = "Enabled"
    filter { prefix = "hls/" }
    transition { days = 30; storage_class = "STANDARD_IA" }
    transition { days = 90; storage_class = "GLACIER_IR" }
  }
  rule {
    id     = "uploads-ia"
    status = "Enabled"
    filter { prefix = "uploads/" }
    transition { days = 90; storage_class = "GLACIER_IR" }
  }
}
```

---

## 8. ECS Cluster, Task Definitions, Services

### 8.1 Cluster

```hcl
resource "aws_ecs_cluster" "echoflow" {
  name = "echoflow"
  setting { name = "containerInsights"; value = "enabled" }
}
```

### 8.2 IAM task role (S3 access + ECR pull + Secrets read)

```hcl
data "aws_iam_policy_document" "ecs_task" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = ["arn:aws:s3:::echoflow-media-${var.env}", "arn:aws:s3:::echoflow-media-${var.env}/*"]
  }
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["arn:aws:secretsmanager:*:*:secret:echoflow/*"]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:*:*:*"]
  }
}

resource "aws_iam_role" "ecs_task" {
  name               = "echoflow-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

resource "aws_iam_role_policy" "ecs_task" {
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.ecs_task.json
}
```

### 8.3 Task definitions (one per service)

**`web` task (api image, port 8000):**

```hcl
resource "aws_ecs_task_definition" "web" {
  family                   = "echoflow-web"
  cpu                      = "512"     # 0.5 vCPU
  memory                   = "1024"    # 1 GB
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn
  container_definitions = jsonencode([{
    name      = "web"
    image     = "${aws_ecr_repository.api.repository_url}:1.0.0"
    essential = true
    portMappings = [{ containerPort = 8000 }]
    command = ["sh", "-c", "set -e && python wait_for_db.py && python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn -c gunicorn.conf.py backend.EchoFlow.wsgi:application"]
    environment = [
      { name = "DJANGO_DEBUG"; value = "False" },
      { name = "AWS_S3_REGION_NAME"; value = "us-east-1" },
      { name = "GUNICORN_WORKERS"; value = "2" },
    ]
    secrets = [
      { name = "DJANGO_SECRET_KEY";    valueFrom = "${aws_secretsmanager_secret.django_secret.arn}" },
      { name = "FIELD_ENCRYPTION_KEY"; valueFrom = "${aws_secretsmanager_secret.field_encryption.arn}" },
      { name = "DATABASE_URL";         valueFrom = "${aws_secretsmanager_secret.database_url.arn}" },
      { name = "REDIS_BROKER_URL";     valueFrom = "${aws_secretsmanager_secret.redis_broker_url.arn}" },
      { name = "REDIS_CACHE_URL";      valueFrom = "${aws_secretsmanager_secret.redis_cache_url.arn}" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = "/ecs/echoflow-web"
        "awslogs-region"        = "us-east-1"
        "awslogs-stream-prefix" = "ecs"
        "awslogs-create-group"  = "true"
      }
    }
  }])
}
```

**`celery-media` task (media image, 4 GB / 2 vCPU, Spot):**

```hcl
resource "aws_ecs_task_definition" "celery_media" {
  family                   = "echoflow-celery-media"
  cpu                      = "2048"
  memory                   = "4096"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn
  container_definitions = jsonencode([{
    name      = "celery-media"
    image     = "${aws_ecr_repository.media.repository_url}:1.0.0"
    essential = true
    command   = ["sh", "-c", "set -e && python wait_for_db.py && celery -A backend.EchoFlow worker -Q heavy_media --pool=prefork --concurrency=2 --loglevel=info"]
    environment = [
      { name = "DJANGO_DEBUG"; value = "False" },
      { name = "HF_HOME"; value = "/home/appuser/.cache/huggingface" },
      { name = "HF_HUB_OFFLINE"; value = "1" },
      { name = "TRANSFORMERS_OFFLINE"; value = "1" },
    ]
    secrets = [
      { name = "DJANGO_SECRET_KEY";    valueFrom = "${aws_secretsmanager_secret.django_secret.arn}" },
      { name = "FIELD_ENCRYPTION_KEY"; valueFrom = "${aws_secretsmanager_secret.field_encryption.arn}" },
      { name = "DATABASE_URL";         valueFrom = "${aws_secretsmanager_secret.database_url.arn}" },
      { name = "REDIS_BROKER_URL";     valueFrom = "${aws_secretsmanager_secret.redis_broker_url.arn}" },
      { name = "REDIS_CACHE_URL";      valueFrom = "${aws_secretsmanager_secret.redis_cache_url.arn}" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = { "awslogs-group" = "/ecs/echoflow-celery-media", "awslogs-region" = "us-east-1", "awslogs-stream-prefix" = "ecs", "awslogs-create-group" = "true" }
    }
  }])
}
```

Repeat the pattern for `celery` (api image, 0.5 vCPU / 1 GB), `celery-feed` (api image, 0.5 vCPU / 1 GB, `-Q fast_feed`), and `celery-beat` (api image, 0.25 vCPU / 0.5 GB, **no Spot — exactly 1 task**).

### 8.4 Services (Spot for non-beat workers)

```hcl
resource "aws_ecs_service" "web" {
  name            = "web"
  cluster         = aws_ecs_cluster.echoflow.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 8000
  }
  desired_count = 1
}

# Spot for workers — set capacity_provider_strategy, not launch_type
resource "aws_ecs_capacity_provider" "spot" {
  name = "FARGATE_SPOT"
  capacity_provider_strategy { capacity_provider = "FARGATE"; weight = 0; base = 0 }
  capacity_provider_strategy { capacity_provider = "FARGATE_SPOT"; weight = 1 }
}

resource "aws_ecs_service" "celery_media" {
  name            = "celery-media"
  cluster         = aws_ecs_cluster.echoflow.id
  task_definition = aws_ecs_task_definition.celery_media.arn
  desired_count   = 1
  capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight            = 1
    base              = 1
  }
  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }
}
```

Repeat for `celery`, `celery-feed`. **`celery-beat` MUST be `desired_count=1` on FARGATE (not Spot)** — two beat schedulers would double-fire all periodic tasks (`docs/EXPLAIN/redis-celery/04-task-reliability.md:236`).

### 8.5 Target group health checks

```hcl
resource "aws_lb_target_group" "web" {
  name        = "echoflow-web"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id
  health_check {
    path                = "/health/"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }
}
```

`/health/` (liveness) and `/ready/` (readiness — checks DB) already exist in `backend/EchoFlow/health.py`. `/ready/` is what ALB should call if you use `host-based` health checks; for `path` health check, `/health/` is fine.

---

## 9. Small-Scale / Budget Optimisations

The list below targets the **lowest viable cost** while preserving the managed-service security story. Prices are US-East-1 on-demand; Fargate Spot is ~70% off.

| Resource | Choice | Approx cost/mo |
|---|---|---|
| Compute (web) | 1 × Fargate 0.5 vCPU / 1 GB | ~$10 |
| Compute (celery) | 1 × Fargate Spot 0.5 vCPU / 1 GB | ~$3 |
| Compute (celery-feed) | 1 × Fargate Spot 0.5 vCPU / 1 GB | ~$3 |
| Compute (celery-media) | 1 × Fargate Spot 2 vCPU / 4 GB | ~$20 |
| Compute (celery-beat) | 1 × Fargate 0.25 vCPU / 0.5 GB | ~$5 |
| RDS | db.t4g.micro, 20 GB gp3 | ~$15 |
| ElastiCache broker | cache.t4g.micro, 1 GB | ~$12 |
| ElastiCache cache | cache.t4g.small, 2 GB | ~$25 |
| S3 | 50 GB Standard + lifecycle to IA | ~$1–3 |
| ALB | 1 ALB, low traffic | ~$18 + LCU |
| WAF | 1 Web ACL + 2 managed rule groups | ~$7 |
| Secrets Manager | 5 secrets | ~$2 |
| **Total** | | **~$120–140/mo** |

**Spot caveats:**
- `celery-beat` **must not** be Spot (exactly 1 task required).
- `celery-media` Spot interruption is fine — `process_audio_to_hls` is idempotent; ACKs only after upload to S3.
- `celery-feed` Spot is fine — `refill_user_feed` is idempotent.

**Cost knobs:**
- Drop `celery-media` to 1 vCPU / 2 GB if processing <5 clips/day (Whisper base fits but tight).
- Single-AZ RDS saves the multi-AZ surcharge; **don't** for prod.
- Skip `pgbouncer` (RDS) — `db.t4g.micro` connections are fine at this scale.
- `web` task: 1 replica is enough until 50 RPS sustained; add HPA on `RequestCountPerTarget > 1000`.

---

## 10. Alternative: Single-EC2 Budget Mode (smallest possible)

If ~$120/mo is still too much for a hobby / staging deployment, the **entire stack can run on one `t3.medium` (2 vCPU / 4 GB, ~$30/mo) EC2** with Docker Compose, replacing only the data layer with RDS + ElastiCache (or even SQLite + local Redis on the same box, accepting the risk).

**Tradeoffs:**

| | ECS Fargate (managed) | EC2 + Compose (budget) |
|---|---|---|
| Resilience | Multi-AZ, Spot failover, ALB health checks | Single host, no failover |
| Patching | Image rebuild only | OS patching, Docker upgrades, host kernel |
| Cost at small scale | ~$120/mo | ~$60–80/mo (with managed DB/Redis) |
| TLS / bot resistance | ALB + WAF + Shield (managed) | You run nginx + certbot + fail2ban manually |
| Migration path | Already on Fargate | Re-deploy Compose to ECS later (requires Dockerfile split, which is done) |

**Recommended only for staging / single-developer exploration.** Production should use §1–9.

---

## 11. Migration / Go-Live Checklist

Run through this list the first time you promote EchoFlow from Compose to ECS.

- [ ] RDS created; `pgvector` extension installed via `aws rds-data execute-statement`.
- [ ] ElastiCache broker + cache clusters reachable; auth tokens set in Secrets Manager.
- [ ] S3 bucket created; bucket policy matches §6.4 (`hls/*` public-read, everything else private); BPA on.
- [ ] ECR repos `echoflow/api` and `echoflow/media` created; images built with `--secret id=hf_token` and pushed.
- [ ] Secrets Manager populated: `django_secret`, `field_encryption`, `database_url`, `redis_broker_url`, `redis_cache_url`, optional `sentry_dsn`, `freesound_api_key`.
- [ ] Parameter Store populated: `DJANGO_DEBUG=False`, `DJANGO_ALLOWED_HOSTS=api.echoflow.example`, `DJANGO_CORS_ALLOWED_ORIGINS=https://app.echoflow.example`, `AWS_S3_REGION_NAME`, `AWS_STORAGE_BUCKET_NAME`, `PUBLIC_MEDIA_ENDPOINT_URL`.
- [ ] ACM certificate issued and validated for `api.echoflow.example`.
- [ ] WAF Web ACL created (core + rate-limit rules) and associated to ALB.
- [ ] ECS cluster + 5 services running; `web` shows `RUNNING`; ALB target group `healthy`.
- [ ] `https://api.echoflow.example/health/` returns 200.
- [ ] `https://api.echoflow.example/ready/` returns 200.
- [ ] `curl -X POST https://api.echoflow.example/auth/register/ …` succeeds (DRF throttle respected).
- [ ] Upload a small clip; verify `celery-media` task runs, HLS lands in S3 `hls/`, `master.m3u8` is publicly fetchable.
- [ ] Verify `SECURE_SSL_REDIRECT` is not looping: response includes `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`.
- [ ] Verify WAF logs are flowing to CloudWatch (`aws logs tail /aws/wafv2/echoflow --follow`).
- [ ] `curl -H 'X-Forwarded-Proto: https' https://api.echoflow.example/health/` returns 200 (matches the in-container health check pattern from `docker-compose.yml:283`).

---

## 12. What This Guide Does NOT Cover (follow-ups)

- **CloudFront in front of S3 `hls/`** — adds a CDN cache between browser and S3, removes the S3 cost exposure on viral clips. See `docs/EXPLAIN/storage/01-s3-architecture.md:253` and the open item "CDN + OAC in front of MinIO" in [docs/EXPLAIN/decisions/partial-issues-completion-plan.md](EXPLAIN/decisions/partial-issues-completion-plan.md).
- **Read replica + RDS Proxy** — activates the existing `ReadRouter` (`backend/app/db_routers.py`) by setting `READ_DATABASE_URL`. See [docs/EXPLAIN/database/05-read-replica-design.md](EXPLAIN/database/05-read-replica-design.md).
- **AWS WAF Bot Control paid rule group** — add only if you see sophisticated bots in WAF logs (small-scale deployments usually don't need it).
- **CI/CD pipeline** — CodePipeline / GitHub Actions → ECR → ECS rolling deploy. Out of scope for v1.
- **Multi-region / failover** — single-region is the right answer at small scale.
- **Kubernetes (EKS)** — if traffic outgrows ECS, the Dockerfile split + env-var contracts already done make EKS a one-step migration. See [docs/path_to_k8s_deployment.md](path_to_k8s_deployment.md).

---

## 13. References

- [AGENTS.md](../AGENTS.md) — env var contract, runtime contract, gotchas
- [docs/path_to_k8s_deployment.md](path_to_k8s_deployment.md) — image split (`api` vs `media`), resource budgets
- [docs/EXPLAIN/docker/05-https-tls-termination.md](EXPLAIN/docker/05-https-tls-termination.md) — TLS contract, `X-Forwarded-Proto`
- [docs/EXPLAIN/docker/06-https-production-readiness.md](EXPLAIN/docker/06-https-production-readiness.md) — production hardening checklist
- [docs/EXPLAIN/docker/03-environment-variables.md](EXPLAIN/docker/03-environment-variables.md) — every env var
- [docs/EXPLAIN/storage/01-s3-architecture.md](EXPLAIN/storage/01-s3-architecture.md) — `hls/` vs `uploads/`, S3 setup
- [docs/EXPLAIN/operations/hf-token-rotation.md](EXPLAIN/operations/hf-token-rotation.md) — HF_TOKEN as BuildKit secret
- [docs/EXPLAIN/auth/04-rate-limiting.md](EXPLAIN/auth/04-rate-limiting.md) — DRF throttle scopes
- [docs/EXPLAIN/redis-celery/04-task-reliability.md](EXPLAIN/redis-celery/04-task-reliability.md) — why `celery-beat` must be `desired_count=1`
- [docs/backend-architecture-audit.md](backend-architecture-audit.md) — known bottlenecks (none are deployment blockers at small scale)
