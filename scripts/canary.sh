#!/usr/bin/env bash
# Live re-verification canary runner (03-ANTI-MANIPULATION section 5).
#
# Subcommands:
#   run        Run `python -m porchlamp.verify.live_sample` on the nightly sample
#              (seed = today's UTC date; extra args pass through, e.g.
#              `canary.sh run --max-live 5`).
#   install    Ship the systemd user timer for the nightly run.
#   uninstall  Remove the user timer and the copied unit files.
#   help       This text.
#
# Environment:
#   PORCHLAMP_ROOT    repository root (default: this script's parent dir)
#   PORCHLAMP_PY      python interpreter (default: $PORCHLAMP_ROOT/.venv/bin/python,
#               else python3 on PATH)
#   PORCHLAMP_DATA_DIR  data dir holding verified/ (default: $PORCHLAMP_ROOT/data)
#   PORCHLAMP_DB      store with the rated rows (default: $PORCHLAMP_DATA_DIR/porchlamp.db)

set -euo pipefail

PORCHLAMP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.."
PORCHLAMP_PY="${PORCHLAMP_PY:-$PORCHLAMP_ROOT/.venv/bin/python}"
[ -x "$PORCHLAMP_PY" ] || PORCHLAMP_PY="$(command -v python3)"
PORCHLAMP_DATA_DIR="${PORCHLAMP_DATA_DIR:-$PORCHLAMP_ROOT/data}"
PORCHLAMP_DB="${PORCHLAMP_DB:-$PORCHLAMP_DATA_DIR/porchlamp.db}"
DEST_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

usage() { sed -n '2,16p' "$0"; }

cmd="${1:-run}"
case "$cmd" in
  run)
    shift
    exec "$PORCHLAMP_PY" -m porchlamp.verify.live_sample --live-sample \
      --db "$PORCHLAMP_DB" --data-dir "$PORCHLAMP_DATA_DIR" \
      --seed "$(date -u +%Y%m%d)" "$@"
    ;;
  install)
    command -v systemctl >/dev/null 2>&1 || {
      echo "error: systemctl not found; install requires systemd" >&2; exit 1; }
    mkdir -p "$DEST_DIR"
    sed "s|{{PORCHLAMP_ROOT}}|$PORCHLAMP_ROOT|g" \
      "$PORCHLAMP_ROOT/scripts/systemd/porchlamp-canary.service" > "$DEST_DIR/porchlamp-canary.service"
    install -m 0644 "$PORCHLAMP_ROOT/scripts/systemd/porchlamp-canary.timer" \
      "$DEST_DIR/porchlamp-canary.timer"
    systemctl --user daemon-reload
    systemctl --user enable --now porchlamp-canary.timer
    echo "porchlamp-canary.timer enabled (user scope); units in $DEST_DIR"
    ;;
  uninstall)
    command -v systemctl >/dev/null 2>&1 || {
      echo "error: systemctl not found; uninstall requires systemd" >&2; exit 1; }
    systemctl --user disable --now porchlamp-canary.timer 2>/dev/null || true
    rm -f "$DEST_DIR/porchlamp-canary.service" "$DEST_DIR/porchlamp-canary.timer"
    systemctl --user daemon-reload
    echo "porchlamp-canary timer removed"
    ;;
  help|--help|-h)
    usage
    ;;
  *)
    echo "unknown subcommand: $cmd" >&2
    usage
    exit 2
    ;;
esac
