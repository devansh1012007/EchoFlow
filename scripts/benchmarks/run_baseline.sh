#!/bin/bash
# =============================================================================
# EchoFlow — Baseline Benchmark Orchestrator
#
# Runs ONE reproducible baseline measurement against a DEPLOYED stack:
#
#   1. Waits for $BENCH_BASE_URL/health/ to answer (liveness, health.py:9).
#   2. Starts collect_system_metrics.py in the background (Redis / Celery
#      queue / Postgres samples every BENCH_SAMPLE_INTERVAL seconds).
#   3. Runs locust headless with the default baseline profile:
#          -u 50 -r 5 -t 2m   (users / ramp rate / duration)
#      writing CSVs to scripts/benchmarks/results/baseline*.
#   4. Stops the collector, then renders results/baseline.md via
#      render_report.py.
#
# NOTHING ELSE. This script does not compute any metric itself and contains
# no expected values. Every number in the report comes from the CSVs the
# run produced. The p95<=300ms @50 RPS / <1% 5xx figure is a TARGET, not a
# result; the report states it as such and evaluates it only from measured
# data. Do not quote any number from a run you did not run.
#
# Usage:
#   BENCH_BASE_URL=https://echoflow.example \
#   REDIS_CACHE_URL=... REDIS_BROKER_URL=... DATABASE_URL=... \
#   BENCH_TOKEN=... \
#   bash scripts/benchmarks/run_baseline.sh
#
# Prerequisites and the full env-var table: scripts/benchmarks/README.md
#
# To stop early: Ctrl-C (trap stops the collector).
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="${SCRIPT_DIR}/results"

# Profile knobs (defaults are the declared baseline profile).
BENCH_USERS="${BENCH_USERS:-50}"
BENCH_RAMP_RATE="${BENCH_RAMP_RATE:-5}"
BENCH_RUN_TIME="${BENCH_RUN_TIME:-2m}"
BENCH_READY_TIMEOUT="${BENCH_READY_TIMEOUT:-120}"
BENCH_SAMPLE_INTERVAL="${BENCH_SAMPLE_INTERVAL:-5}"
LOCUST_CSV_PREFIX="${RESULTS_DIR}/baseline"
METRICS_CSV="${RESULTS_DIR}/system_metrics.csv"
REPORT_MD="${RESULTS_DIR}/baseline.md"

if [ -z "${BENCH_BASE_URL:-}" ]; then
    echo "ERROR: BENCH_BASE_URL is required (e.g. https://echoflow.example)" >&2
    exit 1
fi
if ! command -v locust >/dev/null 2>&1; then
    echo "ERROR: locust not found. It is a dev-only dependency (not in" >&2
    echo "requirements*.txt): install it on the bench machine with" >&2
    echo "  pip install locust" >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found" >&2
    exit 1
fi

# Tokens/credentials must not be echoed.
if [ -z "${BENCH_TOKEN:-}" ] && { [ -z "${BENCH_USERNAME:-}" ] || [ -z "${BENCH_PASSWORD:-}" ]; }; then
    echo "ERROR: set BENCH_TOKEN (pre-minted access JWT) or BENCH_USERNAME+BENCH_PASSWORD." >&2
    echo "NOTE: /auth/login/ is throttled to 10/min per IP (settings.py:966); token" >&2
    echo "mode is strongly recommended for the ${BENCH_USERS}-user profile." >&2
    exit 1
fi

# -----------------------------------------------------------------------------
# 1. Wait for the deployed stack to answer its liveness probe.
# -----------------------------------------------------------------------------
echo "Waiting for ${BENCH_BASE_URL}/health/ (timeout ${BENCH_READY_TIMEOUT}s)..."
deadline=$(( $(date +%s) + BENCH_READY_TIMEOUT ))
until curl -fsS --max-time 5 "${BENCH_BASE_URL%/}/health/" >/dev/null 2>&1; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "ERROR: ${BENCH_BASE_URL}/health/ did not answer within ${BENCH_READY_TIMEOUT}s" >&2
        exit 1
    fi
    sleep 2
done
echo "Stack is up."

mkdir -p "${RESULTS_DIR}"

# -----------------------------------------------------------------------------
# 2. Start the system-metrics collector beside the load test.
# -----------------------------------------------------------------------------
echo "Starting collector (interval ${BENCH_SAMPLE_INTERVAL}s) -> ${METRICS_CSV}"
BENCH_SAMPLE_INTERVAL="${BENCH_SAMPLE_INTERVAL}" \
BENCH_OUTPUT_CSV="${METRICS_CSV}" \
python3 "${SCRIPT_DIR}/collect_system_metrics.py" &
COLLECTOR_PID=$!

cleanup() {
    # Stop the collector gracefully; do not leave orphans behind.
    if kill -0 "${COLLECTOR_PID}" 2>/dev/null; then
        kill -TERM "${COLLECTOR_PID}" 2>/dev/null || true
        for _ in 1 2 3 4 5; do
            kill -0 "${COLLECTOR_PID}" 2>/dev/null || break
            sleep 1
        done
        kill -0 "${COLLECTOR_PID}" 2>/dev/null && kill -KILL "${COLLECTOR_PID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# -----------------------------------------------------------------------------
# 3. Load test: 50 users, ramp 5/s, 2 minutes, CSV output.
#    (collect_system_metrics.py samples Redis/queues/DB while this runs.)
# -----------------------------------------------------------------------------
echo "Running locust: -u ${BENCH_USERS} -r ${BENCH_RAMP_RATE} -t ${BENCH_RUN_TIME}"
# --exit-code-on-error 0: a run WITH failures must still produce its report —
# a FAIL verdict from render_report.py is a valid, publishable outcome.
# The verdict logic is the gate, not this script's exit code.
locust --headless \
    -f "${SCRIPT_DIR}/locustfile.py" \
    --host "${BENCH_BASE_URL}" \
    --exit-code-on-error 0 \
    -u "${BENCH_USERS}" \
    -r "${BENCH_RAMP_RATE}" \
    -t "${BENCH_RUN_TIME}" \
    --csv "${LOCUST_CSV_PREFIX}" \
    --stop-timeout 30

# -----------------------------------------------------------------------------
# 4. Stop collector (trap also covers Ctrl-C) and render the report.
# -----------------------------------------------------------------------------
kill -TERM "${COLLECTOR_PID}" 2>/dev/null || true
wait "${COLLECTOR_PID}" 2>/dev/null || true

echo "Rendering report -> ${REPORT_MD}"
python3 "${SCRIPT_DIR}/render_report.py" \
    --stats "${LOCUST_CSV_PREFIX}_stats.csv" \
    --failures "${LOCUST_CSV_PREFIX}_failures.csv" \
    --metrics "${METRICS_CSV}" \
    --out "${REPORT_MD}" \
    --users "${BENCH_USERS}" \
    --ramp "${BENCH_RAMP_RATE}" \
    --duration "${BENCH_RUN_TIME}" \
    --base-url "${BENCH_BASE_URL}"

echo ""
echo "Baseline complete. Outputs:"
echo "  locust CSVs : ${LOCUST_CSV_PREFIX}_stats.csv, ${LOCUST_CSV_PREFIX}_failures.csv"
echo "  system CSV  : ${METRICS_CSV}"
echo "  report      : ${REPORT_MD}"
echo ""
echo "REMINDER: the report's target line is a TARGET, not a result. Only the"
echo "measured numbers above may be published, and only with this run's"
echo "profile metadata attached (see scripts/benchmarks/README.md)."
