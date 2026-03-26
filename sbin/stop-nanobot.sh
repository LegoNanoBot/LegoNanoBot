#!/usr/bin/env bash
# =============================================================================
# stop-nanobot.sh — Stop the nanobot gateway.
# =============================================================================
set -euo pipefail

SBIN_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=sbin/nanobot-config.sh
source "${SBIN_DIR}/nanobot-config.sh"

PIDFILE=$(_pid_file gateway)
_stop_daemon "nanobot-gateway" "$PIDFILE"
