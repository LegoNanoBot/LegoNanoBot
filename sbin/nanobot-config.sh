#!/usr/bin/env bash
# =============================================================================
# nanobot-config.sh — Common variables and helpers for all sbin scripts.
#                     Sourced by other scripts; do NOT execute directly.
# =============================================================================

# Resolve project root (one level up from sbin/)
NANOBOT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export NANOBOT_HOME

# ---------- tunables (override via environment) ----------
NANOBOT_PID_DIR="${NANOBOT_PID_DIR:-${NANOBOT_HOME}/pids}"
NANOBOT_LOG_DIR="${NANOBOT_LOG_DIR:-${NANOBOT_HOME}/logs}"
NANOBOT_CONF_DIR="${NANOBOT_CONF_DIR:-}"  # empty → use default ~/.nanobot/config.json
NANOBOT_CONDA_ENV="${NANOBOT_CONDA_ENV:-legonanobot}"

# Gateway
NANOBOT_GATEWAY_PORT="${NANOBOT_GATEWAY_PORT:-18790}"

# Supervisor
NANOBOT_SUPERVISOR_HOST="${NANOBOT_SUPERVISOR_HOST:-127.0.0.1}"
NANOBOT_SUPERVISOR_PORT="${NANOBOT_SUPERVISOR_PORT:-9200}"

# Worker
NANOBOT_WORKER_COUNT="${NANOBOT_WORKER_COUNT:-1}"
NANOBOT_WORKER_POLL_INTERVAL="${NANOBOT_WORKER_POLL_INTERVAL:-3.0}"

# ---------- internal helpers ----------

_ensure_dirs() {
    mkdir -p "$NANOBOT_PID_DIR" "$NANOBOT_LOG_DIR"
}

_config_flag() {
    if [[ -n "$NANOBOT_CONF_DIR" ]]; then
        echo "--config ${NANOBOT_CONF_DIR}"
    fi
}

_activate_conda() {
    # Try activating conda env; skip silently if already active or unavailable
    if [[ "${CONDA_DEFAULT_ENV:-}" == "$NANOBOT_CONDA_ENV" ]]; then
        return 0
    fi
    if command -v conda &>/dev/null; then
        eval "$(conda shell.bash hook 2>/dev/null)" || true
        conda activate "$NANOBOT_CONDA_ENV" 2>/dev/null || true
    fi
}

_pid_file() {
    # Usage: _pid_file <component> [suffix]
    local component="$1"
    local suffix="${2:-}"
    if [[ -n "$suffix" ]]; then
        echo "${NANOBOT_PID_DIR}/nanobot-${component}-${suffix}.pid"
    else
        echo "${NANOBOT_PID_DIR}/nanobot-${component}.pid"
    fi
}

_is_running() {
    local pidfile="$1"
    if [[ -f "$pidfile" ]]; then
        local pid
        pid=$(<"$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        # stale pid file
        rm -f "$pidfile"
    fi
    return 1
}

_start_daemon() {
    # Usage: _start_daemon <label> <pidfile> <logfile> <command...>
    local label="$1"; shift
    local pidfile="$1"; shift
    local logfile="$1"; shift

    if _is_running "$pidfile"; then
        local pid=$(<"$pidfile")
        echo "[WARN] $label is already running (PID $pid). Stop it first."
        return 1
    fi

    echo "[INFO] Starting $label ..."
    nohup "$@" >>"$logfile" 2>&1 &
    local pid=$!
    echo "$pid" > "$pidfile"
    # Brief health check
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
        echo "[OK]   $label started (PID $pid). Log: $logfile"
    else
        echo "[FAIL] $label exited immediately. Check $logfile"
        rm -f "$pidfile"
        return 1
    fi
}

_stop_daemon() {
    # Usage: _stop_daemon <label> <pidfile>
    local label="$1"
    local pidfile="$2"

    if ! _is_running "$pidfile"; then
        echo "[INFO] $label is not running."
        return 0
    fi

    local pid=$(<"$pidfile")
    echo "[INFO] Stopping $label (PID $pid) ..."
    kill "$pid" 2>/dev/null
    # Wait up to 10 seconds for graceful shutdown
    local waited=0
    while kill -0 "$pid" 2>/dev/null && (( waited < 10 )); do
        sleep 1
        (( waited++ ))
    done
    if kill -0 "$pid" 2>/dev/null; then
        echo "[WARN] $label did not stop gracefully, sending SIGKILL ..."
        kill -9 "$pid" 2>/dev/null
        sleep 1
    fi
    rm -f "$pidfile"
    echo "[OK]   $label stopped."
}

_nanobot_cmd() {
    # Return the nanobot command, preferring module invocation
    echo "python -m nanobot"
}
