#!/usr/bin/env bash
# =============================================================================
# start-supervisor.sh — Start supervisor + N workers (distributed mode).
#
# Usage:
#   sbin/start-supervisor.sh [--workers N] [--port PORT]
#
# This starts:
#   1. One supervisor process (control plane)
#   2. N worker processes (default: 1, controlled by --workers or
#      NANOBOT_WORKER_COUNT env var)
# =============================================================================
set -euo pipefail

SBIN_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=sbin/nanobot-config.sh
source "${SBIN_DIR}/nanobot-config.sh"

# Parse optional args
PORT="$NANOBOT_SUPERVISOR_PORT"
HOST="$NANOBOT_SUPERVISOR_HOST"
WORKER_N="$NANOBOT_WORKER_COUNT"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --workers)  WORKER_N="$2"; shift 2 ;;
        --port)     PORT="$2"; shift 2 ;;
        --host)     HOST="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

_activate_conda
_ensure_dirs

SUPERVISOR_URL="http://${HOST}:${PORT}"
CONFIG_FLAG=$(_config_flag)

# ── Start Supervisor ─────────────────────────────────────────────────────────
PIDFILE=$(_pid_file supervisor)
LOGFILE="${NANOBOT_LOG_DIR}/nanobot-supervisor.log"
CMD="$(_nanobot_cmd) supervisor --host ${HOST} --port ${PORT} ${CONFIG_FLAG}"

_start_daemon "nanobot-supervisor" "$PIDFILE" "$LOGFILE" bash -c "cd ${NANOBOT_HOME} && ${CMD}"

# Wait for supervisor API to be ready
echo "[INFO] Waiting for supervisor API ..."
ready=0
for i in $(seq 1 15); do
    if curl -sf "${SUPERVISOR_URL}/health" >/dev/null 2>&1 || \
       curl -sf "${SUPERVISOR_URL}/api/workers" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done

if (( ready == 0 )); then
    echo "[WARN] Supervisor API not responsive yet; starting workers anyway."
fi

# ── Start Workers ────────────────────────────────────────────────────────────
echo "[INFO] Starting ${WORKER_N} worker(s) ..."
for i in $(seq 1 "$WORKER_N"); do
    W_PIDFILE=$(_pid_file worker "$i")
    W_LOGFILE="${NANOBOT_LOG_DIR}/nanobot-worker-${i}.log"
    W_NAME="worker-${i}"
    W_CMD="$(_nanobot_cmd) worker --supervisor ${SUPERVISOR_URL} --name ${W_NAME} --poll-interval ${NANOBOT_WORKER_POLL_INTERVAL} ${CONFIG_FLAG}"

    _start_daemon "nanobot-${W_NAME}" "$W_PIDFILE" "$W_LOGFILE" bash -c "cd ${NANOBOT_HOME} && ${W_CMD}"
done

echo ""
echo "=== Supervisor cluster started ==="
echo "  Supervisor : ${SUPERVISOR_URL}"
echo "  Workers    : ${WORKER_N}"
echo "  PID dir    : ${NANOBOT_PID_DIR}"
echo "  Log dir    : ${NANOBOT_LOG_DIR}"
