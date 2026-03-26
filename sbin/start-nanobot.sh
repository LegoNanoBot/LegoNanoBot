#!/usr/bin/env bash
# =============================================================================
# start-nanobot.sh — Start basic nanobot gateway (single-node mode).
#
# Usage:
#   sbin/start-nanobot.sh [--port PORT]
#
# This is the most common setup: one gateway process handling all channels.
# =============================================================================
set -euo pipefail

SBIN_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=sbin/nanobot-config.sh
source "${SBIN_DIR}/nanobot-config.sh"

# Parse optional arg
PORT="$NANOBOT_GATEWAY_PORT"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

_activate_conda
_ensure_dirs

PIDFILE=$(_pid_file gateway)
LOGFILE="${NANOBOT_LOG_DIR}/nanobot-gateway.log"

CMD="$(_nanobot_cmd) gateway --port ${PORT} $(_config_flag)"

_start_daemon "nanobot-gateway" "$PIDFILE" "$LOGFILE" bash -c "cd ${NANOBOT_HOME} && ${CMD}"
