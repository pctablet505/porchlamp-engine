#!/bin/bash
# The whole refresh gather. Run ONLY under:
#   systemd-run --user --scope --unit=porchlamp-refresh -p MemoryMax=16G bash <this file>
# Idempotent: a layer with state/refresh/<LAYER>.complete is skipped, a staged layer resumes from its envelopes.
#
# Two streams, disjoint by collector (every collector writes its own raw/<collector>/ folder; envelope and
# user-cache writes are atomic temp+os.replace, so concurrent gather processes cannot tear a file):
#   MAIN  (GitHub budget, MAIN_SHARDS=2 gather processes on disjoint repo shards): L1 pr_window -> L0 meta/REST -> L2 -> L3g governance -> L3c -> L4 author_history
#   KEYLESS (1 gather process; deps.dev, scorecard, registries: no GitHub budget, pypistats alone is 15 s per pypi repo)
# MAIN stops at the first layer it cannot confirm.
set -uo pipefail
# PHONE variant (PHONE-1): the phone's home and engine come from the environment; gather_layer_phone.sh runs the layers.
PHONE_HOME="${PORCHLAMP_PHONE_HOME:-$HOME/porchlamp}"
HERE="$(cd "$(dirname "$0")" && pwd)"
RUN="$PHONE_HOME"
[ -s "$PHONE_HOME/config/github_token" ] && export GH_TOKEN="$(cat "$PHONE_HOME/config/github_token")"
say() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] refresh: $*"; }
mkdir -p "$RUN/logs"

main_chain() {
  local spec LX COLS
  for spec in \
    "L1 pr_window" \
    "L0 github_meta,pr_events,pr_reviews,contributors,actions_runs,tree,commits_180d" \
    "L2 repo_object,gfi,dependents,releases,runs" \
    "L3g governance" \
    "L3c commits_365d,issues_counts" \
    "L4 author_history"; do
    LX=${spec%% *}; COLS=${spec#* }
    say "MAIN layer $LX start ($COLS)"
    # at most 3 gather processes in all: 2 MAIN shards while the keyless stream runs, 3 once it has finished
    n=${MAIN_SHARDS:-1}
    NSHARDS=$n bash "$HERE/gather_layer_phone.sh" "$LX" "$COLS" >> "$RUN/logs/runner-$LX.log" 2>&1
    rc=$?
    say "MAIN layer $LX rc=$rc (0 = two runs agreed on the failure count)"
    [ "$rc" -eq 0 ] || { say "MAIN BLOCKED at $LX"; return 1; }
  done
  say "MAIN all layers gathered"
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$PHONE_HOME/state/refresh/MAIN.done"
}

say "start"
main_chain &
MAIN_PID=$!
# ONLY=MAIN restarts the MAIN stream alone while a keyless stream from an earlier launch is still running (10-08).
if [ "${ONLY:-}" != MAIN ] && [ "${KEYLESS_PARALLEL:-1}" = 1 ]; then
  KEYLESS=1 bash "$HERE/gather_layer_phone.sh" LK depsdev,scorecard,downloads >> "$RUN/logs/runner-LK.log" 2>&1 &
  K_PID=$!
fi
wait "$MAIN_PID"; say "MAIN exited rc=$?"
if [ -n "${K_PID:-}" ]; then wait "$K_PID"; say "KEYLESS exited rc=$?"; fi
