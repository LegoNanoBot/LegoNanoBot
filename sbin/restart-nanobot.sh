#!/usr/bin/env bash
# =============================================================================
# restart-nanobot.sh — Restart the current nanobot environment.
#
# Detects which components are running (gateway, supervisor, workers)
# and restarts them with the same configuration.
# =============================================================================
set -euo pipefail

SBIN_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=sbin/nanobot-config.sh
source "${SBIN_DIR}/nanobot-config.sh"

echo "=== nanobot restart ==="
echo ""

restarted=0

# ── Gateway ──────────────────────────────────────────────────────────────────
GATEWAY_PID=$(_pid_file gateway)
if _is_running "$GATEWAY_PID"; then
    echo "[INFO] Gateway is running — restarting ..."
    "${SBIN_DIR}/stop-nanobot.sh"
    sleep 1
    "${SBIN_DIR}/start-nanobot.sh"
    (( restarted++ ))
fi

# ── Supervisor ───────────────────────────────────────────────────────────────
SUPERVISOR_PID=$(_pid_file supervisor)
if _is_running "$SUPERVISOR_PID"; then
    echo "[INFO] Supervisor is running — restarting ..."
    "${SBIN_DIR}/stop-supervisor.sh"
    sleep 1
    "${SBIN_DIR}/start-supervisor.sh"
    (( restarted++ ))
fi

if (( restarted == 0 )); then
    echo "[WARN] No running nanobot components found."
    echo "       Use start-nanobot.sh or start-supervisor.sh to start components."
fi
