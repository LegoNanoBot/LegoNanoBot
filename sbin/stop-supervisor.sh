#!/usr/bin/env bash
# =============================================================================
# stop-supervisor.sh — Stop supervisor and all its workers.
# =============================================================================
set -euo pipefail

SBIN_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=sbin/nanobot-config.sh
source "${SBIN_DIR}/nanobot-config.sh"

_ensure_dirs

echo "=== Stopping supervisor cluster ==="

# ── Stop Workers first (graceful draining) ───────────────────────────────────
for pidfile in "${NANOBOT_PID_DIR}"/nanobot-worker-*.pid; do
    [[ -f "$pidfile" ]] || continue
    name=$(basename "$pidfile" .pid | sed 's/^nanobot-//')
    _stop_daemon "$name" "$pidfile"
done

# ── Stop Supervisor ──────────────────────────────────────────────────────────
PIDFILE=$(_pid_file supervisor)
_stop_daemon "nanobot-supervisor" "$PIDFILE"

echo ""
echo "[OK] Supervisor cluster stopped."
