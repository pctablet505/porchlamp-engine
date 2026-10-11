#!/usr/bin/env bash
# Termux:Boot entry (copy or link this into ~/.termux/boot/). Starts the LAN server and, monthly, the refresh.
set -u
PHONE_HOME="${PORCHLAMP_PHONE_HOME:-$HOME/porchlamp}"
ENGINE="${PORCHLAMP_ENGINE:-$HOME/porchlamp-engine}"
TERMUX_DIR="$ENGINE/phone/termux"

termux-wake-lock 2>/dev/null || true

start_session() {  # start_session NAME COMMAND: one tmux session per job, only if not already running
  local name="$1" cmd="$2"
  if tmux has-session -t "$name" 2>/dev/null; then
    echo "boot: $name already running"
  else
    tmux new-session -d -s "$name" "$cmd"
    echo "boot: started $name"
  fi
}

start_session porchlamp-serve \
  "cd '$ENGINE' && PORCHLAMP_PHONE_HOME='$PHONE_HOME' python -m phone.serve --home '$PHONE_HOME' --port 8765 --bind 0.0.0.0"

# Monthly guard: refresh only when MAIN.done is missing or older than 28 days.
MAIN_DONE="$PHONE_HOME/state/refresh/MAIN.done"
if [ ! -f "$MAIN_DONE" ] || [ -n "$(find "$MAIN_DONE" -mtime +28 2>/dev/null)" ]; then
  start_session porchlamp-refresh \
    "PORCHLAMP_PHONE_HOME='$PHONE_HOME' PORCHLAMP_ENGINE='$ENGINE' bash '$TERMUX_DIR/refresh_phone.sh'"
else
  echo "boot: MAIN.done is recent; no refresh"
fi
