#!/usr/bin/env bash
# =============================================================================
# nanobot-status.sh — Show running status of all nanobot components.
# =============================================================================
set -euo pipefail

SBIN_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=sbin/nanobot-config.sh
source "${SBIN_DIR}/nanobot-config.sh"

_ensure_dirs

echo "=== nanobot component status ==="
echo ""

any_running=0

for pidfile in "${NANOBOT_PID_DIR}"/nanobot-*.pid; do
    [[ -f "$pidfile" ]] || continue
    name=$(basename "$pidfile" .pid | sed 's/^nanobot-//')
    pid=$(<"$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
        echo "  [RUNNING]  ${name}  (PID ${pid})"
        any_running=1
    else
        echo "  [DEAD]     ${name}  (stale PID ${pid})"
        rm -f "$pidfile"
    fi
done

if (( any_running == 0 )); then
    echo "  No nanobot components running."
fi

echo ""
echo "PID dir: ${NANOBOT_PID_DIR}"
echo "Log dir: ${NANOBOT_LOG_DIR}"
