#!/usr/bin/env python3
"""EchoFlow benchmark system-metrics collector.

Runs BESIDE the load test (started by run_baseline.sh, or standalone) and
appends one CSV row per sample. Standard-library + redis + psycopg2 only
(redis==8.1.0 / psycopg2-binary==2.9.12 are already pinned in
requirements-base.txt; this script must NOT add dependencies).

WHAT IS MEASURED (and what is honestly NOT):

  redis_cache   INFO memory: used_memory, maxmemory; INFO stats:
                evicted_keys (cumulative since Redis start — render_report
                computes the delta over the run window); CONFIG maxmemory-
                policy; dbsize. Source: REDIS_CACHE_URL.
  redis_broker  INFO memory: used_memory/maxmemory (broker runs
                noeviction in compose, docker-compose.yml:452).
  celery queues LLEN per queue on the BROKER redis. Queue names come from
                CELERY_TASK_ROUTES (backend/EchoFlow/settings.py:570-573:
                'heavy_media' for process_audio_to_hls, 'fast_feed' for
                refill_user_feed) plus the implicit default 'celery' queue
                (backend/EchoFlow/celery.py:27-28 names all three: default,
                fast_feed, heavy_media) and the worker -Q flags
                (docker-compose.yml:429 fast_feed, :496 heavy_media).
                Extra queues: BENCH_EXTRA_CELERY_QUEUES (comma-separated).
  unacked       HLEN 'unacked' — tasks delivered to a worker but not yet
                acknowledged (CELERY_TASK_ACKNOWLEDGE_LATE=True,
                settings.py:582). Oldest-age approximation: Kombu keeps
                restore times in the 'unacked_index' zset with score =
                delivery_time + visibility_timeout, so
                  age ~= now - (min_score - visibility_timeout)
                using BENCH_REDIS_VISIBILITY_TIMEOUT (default 3600, the
                redis-broker default). THIS IS AN APPROXIMATION: if the
                broker's visibility_timeout differs from the env value the
                age is systematically off by that difference; a negative
                result is clamped to empty rather than reported as a
                bogus number. True per-task lag needs `celery events`
                instrumentation and is NOT sampled here.
  queue depth   IS DEPTH, NOT LAG. LLEN says how many messages are queued;
                it does not say how long they have waited. Columns are
                named *_depth on purpose. Do not relabel them "lag".
  telemetry     XLEN stream:interaction.events and its DLQ
                (backend/app/services/interactions.py:89-131) and LLEN
                legacy list 'telemetry:queue' (backend/app/tasks.py:658).
                Depth only, same honesty rule.
  postgres      pg_stat_activity grouped by state (client connections
                only), pg_settings.max_connections, and
                pg_stat_database.deadlocks for the current database
                (cumulative since stats reset — delta computed by
                render_report). Read-only, over DATABASE_URL.
                Visibility caveat: a non-superuser only sees activity rows
                it is permitted to see; connection totals may undercount
                other roles' sessions — that is a property of Postgres
                permissions, not a bug in this script.

FAILURE BEHAVIOUR: if a component is unreachable it is logged ONCE, its
columns are written empty for every row, and the collector keeps running.
Stop with SIGTERM/SIGINT (run_baseline.sh does this); exits 0.

Usage:
    BENCH_SAMPLE_INTERVAL=5 \
    REDIS_CACHE_URL=redis://... REDIS_BROKER_URL=redis://... \
    DATABASE_URL=postgres://... \
    python3 collect_system_metrics.py --out results/system_metrics.csv
"""

import argparse
import csv
import datetime
import os
import signal
import sys
import time

try:
    import redis
except ImportError:
    redis = None

try:
    import psycopg2
except ImportError:
    psycopg2 = None

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
SAMPLE_INTERVAL = float(os.getenv("BENCH_SAMPLE_INTERVAL", "5"))
REDIS_CACHE_URL = os.getenv("REDIS_CACHE_URL", "")
REDIS_BROKER_URL = os.getenv("REDIS_BROKER_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

# From CELERY_TASK_ROUTES (settings.py:570-573) + the implicit default
# queue + worker -Q flags (docker-compose.yml:429, :496). Extend with
# BENCH_EXTRA_CELERY_QUEUES if the deployment adds queues.
CELERY_QUEUES = ["celery", "fast_feed", "heavy_media"]
_extra = [q.strip() for q in os.getenv("BENCH_EXTRA_CELERY_QUEUES", "").split(",") if q.strip()]
for _q in _extra:
    if _q not in CELERY_QUEUES:
        CELERY_QUEUES.append(_q)

REDIS_VISIBILITY_TIMEOUT = float(os.getenv("BENCH_REDIS_VISIBILITY_TIMEOUT", "3600"))

TELEMETRY_STREAM = "stream:interaction.events"
TELEMETRY_DLQ = "stream:interaction.events:dlq"
TELEMETRY_LEGACY_LIST = "telemetry:queue"

_stop = False
_logged_once = set()


def log_once(component, msg):
    if component not in _logged_once:
        _logged_once.add(component)
        print(f"[collector] {component}: {msg}", file=sys.stderr, flush=True)


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def handle_stop(signum, frame):
    global _stop
    _stop = True


# ---------------------------------------------------------------------------
# Redis sampling
# ---------------------------------------------------------------------------
def _redis_client(url):
    if not (redis and url):
        return None
    return redis.Redis.from_url(
        url, socket_connect_timeout=2, socket_timeout=2, decode_responses=True
    )


def sample_redis(client, prefix, row):
    """used_memory / maxmemory / evicted_keys / dbsize for one Redis."""
    if client is None:
        log_once(f"{prefix}-redis", "unavailable (no URL or redis module missing)")
        return
    try:
        mem = client.info(section="memory")
        stats = client.info(section="stats")
        row[f"{prefix}_used_memory_bytes"] = mem.get("used_memory", "")
        row[f"{prefix}_maxmemory_bytes"] = mem.get("maxmemory", "")
        row[f"{prefix}_evicted_keys_total"] = stats.get("evicted_keys", "")
        row[f"{prefix}_dbsize_keys"] = client.dbsize()
        try:
            policy = client.config_get("maxmemory-policy") or {}
            row[f"{prefix}_maxmemory_policy"] = policy.get("maxmemory-policy", "")
        except Exception as exc:
            log_once(f"{prefix}-config", f"CONFIG GET failed: {exc}")
    except Exception as exc:
        log_once(f"{prefix}-redis", f"unreachable, sampling skipped: {exc}")


def sample_broker_queues(client, row):
    """LLEN per Celery queue + unacked count + oldest-unacked age approx.

    Depth only, NOT lag (see header). Unacked age uses the Kombu
    'unacked_index' zset whose score is delivery_time + visibility_timeout.
    """
    if client is None:
        log_once("broker", "unavailable (no URL or redis module missing)")
        return
    try:
        pipe = client.pipeline(transaction=False)
        for q in CELERY_QUEUES:
            pipe.llen(q)
        pipe.hlen("unacked")
        pipe.zrange("unacked_index", 0, 0, withscores=True)
        pipe.xlen(TELEMETRY_STREAM)
        pipe.xlen(TELEMETRY_DLQ)
        pipe.llen(TELEMETRY_LEGACY_LIST)
        lens, unacked, oldest, stream_len, dlq_len, legacy_len = pipe.execute()
    except Exception as exc:
        log_once("broker", f"unreachable, queue sampling skipped: {exc}")
        return
    for q, depth in zip(CELERY_QUEUES, lens):
        row[f"celery_queue_{q}_depth"] = depth
    row["celery_unacked_count"] = unacked
    row["celery_unacked_oldest_age_s"] = ""
    try:
        if oldest:
            restore_at = float(oldest[0][1])
            delivered_at = restore_at - REDIS_VISIBILITY_TIMEOUT
            age = time.time() - delivered_at
            if age >= 0:
                row["celery_unacked_oldest_age_s"] = round(age, 1)
            else:
                log_once(
                    "broker",
                    "oldest-unacked age came out negative — the broker's "
                    "visibility_timeout likely differs from "
                    f"BENCH_REDIS_VISIBILITY_TIMEOUT={REDIS_VISIBILITY_TIMEOUT}; "
                    "value omitted rather than misreported",
                )
    except Exception as exc:
        log_once("broker", f"unacked-age approximation failed: {exc}")
    row["telemetry_stream_depth"] = stream_len
    row["telemetry_stream_dlq_depth"] = dlq_len
    row["telemetry_legacy_list_depth"] = legacy_len


# ---------------------------------------------------------------------------
# Postgres sampling (read-only)
# ---------------------------------------------------------------------------
_PG_STATE_COLS = {
    "active": "pg_active",
    "idle": "pg_idle",
    "idle in transaction": "pg_idle_in_transaction",
    "idle in transaction (aborted)": "pg_idle_in_transaction_aborted",
}


def sample_postgres(row):
    if not (psycopg2 and DATABASE_URL):
        log_once("postgres", "unavailable (no DATABASE_URL or psycopg2 missing)")
        return
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=3)
        conn.autocommit = True
        with conn.cursor() as cur:
            # Guardrail: make the whole session read-only before querying.
            cur.execute("SET default_transaction_read_only = on")
            cur.execute(
                "SELECT state, count(*) FROM pg_stat_activity "
                "WHERE datname IS NOT NULL GROUP BY state"
            )
            counts = dict(cur.fetchall())
            for state, col in _PG_STATE_COLS.items():
                row[col] = counts.get(state, 0)
            row["pg_total_client_connections"] = sum(counts.values())
            cur.execute(
                "SELECT setting::int FROM pg_settings WHERE name = 'max_connections'"
            )
            got = cur.fetchone()
            row["pg_max_connections"] = got[0] if got else ""
            cur.execute(
                "SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()"
            )
            got = cur.fetchone()
            row["pg_deadlocks_total"] = got[0] if got else ""
    except Exception as exc:
        log_once("postgres", f"unreachable or query failed, sampling skipped: {exc}")
        return
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def csv_columns():
    cols = ["timestamp_utc"]
    for prefix in ("redis_cache", "redis_broker"):
        cols += [
            f"{prefix}_used_memory_bytes",
            f"{prefix}_maxmemory_bytes",
            f"{prefix}_maxmemory_policy",
            f"{prefix}_evicted_keys_total",
            f"{prefix}_dbsize_keys",
        ]
    cols += [f"celery_queue_{q}_depth" for q in CELERY_QUEUES]
    cols += ["celery_unacked_count", "celery_unacked_oldest_age_s"]
    cols += [
        "pg_active",
        "pg_idle",
        "pg_idle_in_transaction",
        "pg_idle_in_transaction_aborted",
        "pg_total_client_connections",
        "pg_max_connections",
        "pg_deadlocks_total",
    ]
    cols += [
        "telemetry_stream_depth",
        "telemetry_stream_dlq_depth",
        "telemetry_legacy_list_depth",
    ]
    return cols


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", default=os.getenv("BENCH_OUTPUT_CSV", "results/system_metrics.csv"),
        help="output CSV path (default results/system_metrics.csv)",
    )
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    new_file = not (os.path.exists(args.out) and os.path.getsize(args.out) > 0)

    print(
        f"[collector] interval={SAMPLE_INTERVAL}s out={args.out}\n"
        f"[collector] redis_cache={'set' if REDIS_CACHE_URL else 'NOT SET — columns will be empty'}\n"
        f"[collector] redis_broker={'set' if REDIS_BROKER_URL else 'NOT SET — columns will be empty'}\n"
        f"[collector] database={'set' if DATABASE_URL else 'NOT SET — columns will be empty'}\n"
        f"[collector] celery queues: {', '.join(CELERY_QUEUES)}",
        file=sys.stderr, flush=True,
    )

    cache_client = _redis_client(REDIS_CACHE_URL)
    broker_client = _redis_client(REDIS_BROKER_URL)

    with open(args.out, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=csv_columns())
        if new_file:
            writer.writeheader()
            fh.flush()
        while not _stop:
            started = time.monotonic()
            row = {c: "" for c in csv_columns()}
            row["timestamp_utc"] = utc_now_iso()
            sample_redis(cache_client, "redis_cache", row)
            sample_redis(broker_client, "redis_broker", row)
            sample_broker_queues(broker_client, row)
            sample_postgres(row)
            writer.writerow(row)
            fh.flush()
            elapsed = time.monotonic() - started
            remaining = SAMPLE_INTERVAL - elapsed
            if remaining > 0 and not _stop:
                time.sleep(remaining)
    print("[collector] stopped cleanly", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
