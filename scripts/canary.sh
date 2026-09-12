#!/usr/bin/env bash
# W3-3 live re-verification canary runner (03-ANTI-MANIPULATION section 5).
#
# Subcommands:
#   run        Run `python -m cvi.verify.live_sample` on the nightly sample
#              (seed = today's UTC date; extra args pass through, e.g.
#              `canary.sh run --max-live 5`).
#   install    Ship the systemd user timer for the nightly run. This is
#              executed by the lead only after the packet is ACCEPTed; the
#              implementer never installs.
#   uninstall  Remove the user timer and the copied unit files.
#   help       This text.
#
# Environment:
#   CVI_ROOT    repository root (default: this script's parent dir)
#   CVI_PY      python interpreter (default: $CVI_ROOT/.venv/bin/python,
#               else python3 on PATH)
#   CVI_DATA_DIR  data dir holding verified/ (default: $CVI_ROOT/data)
#   CVI_DB      store with the rated rows (default: $CVI_DATA_DIR/cvi.db)

set -euo pipefail

CVI_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.."
CVI_PY="${CVI_PY:-$CVI_ROOT/.venv/bin/python}"
[ -x "$CVI_PY" ] || CVI_PY="$(command -v python3)"
CVI_DATA_DIR="${CVI_DATA_DIR:-$CVI_ROOT/data}"
CVI_DB="${CVI_DB:-$CVI_DATA_DIR/cvi.db}"
DEST_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

usage() { sed -n '2,16p' "$0"; }

cmd="${1:-run}"
case "$cmd" in
  run)
    shift
    exec "$CVI_PY" -m cvi.verify.live_sample --live-sample \
      --db "$CVI_DB" --data-dir "$CVI_DATA_DIR" \
      --seed "$(date -u +%Y%m%d)" "$@"
    ;;
  install)
    command -v systemctl >/dev/null 2>&1 || {
      echo "error: systemctl not found; install requires systemd" >&2; exit 1; }
    mkdir -p "$DEST_DIR"
    sed "s|{{CVI_ROOT}}|$CVI_ROOT|g" \
      "$CVI_ROOT/scripts/systemd/cvi-canary.service" > "$DEST_DIR/cvi-canary.service"
    install -m 0644 "$CVI_ROOT/scripts/systemd/cvi-canary.timer" \
      "$DEST_DIR/cvi-canary.timer"
    systemctl --user daemon-reload
    systemctl --user enable --now cvi-canary.timer
    echo "cvi-canary.timer enabled (user scope); units in $DEST_DIR"
    ;;
  uninstall)
    command -v systemctl >/dev/null 2>&1 || {
      echo "error: systemctl not found; uninstall requires systemd" >&2; exit 1; }
    systemctl --user disable --now cvi-canary.timer 2>/dev/null || true
    rm -f "$DEST_DIR/cvi-canary.service" "$DEST_DIR/cvi-canary.timer"
    systemctl --user daemon-reload
    echo "cvi-canary timer removed"
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
