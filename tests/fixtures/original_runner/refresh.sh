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
RUN=$HOME/Projects/porchlamp-work/refresh-run
say() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] refresh: $*"; }
mkdir -p "$RUN/logs" /tmp/porchlamp-refresh

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
    n=${MAIN_SHARDS:-2}; [ -f $HOME/Projects/porchlamp-data/refresh-2026-10/state/refresh/LK.complete ] && n=3
    NSHARDS=$n bash "$RUN/gather_layer.sh" "$LX" "$COLS" >> "$RUN/logs/runner-$LX.log" 2>&1
    rc=$?
    say "MAIN layer $LX rc=$rc (0 = two runs agreed on the failure count)"
    [ "$rc" -eq 0 ] || { say "MAIN BLOCKED at $LX"; return 1; }
  done
  say "MAIN all layers gathered"
  date -u '+%Y-%m-%dT%H:%M:%SZ' > $HOME/Projects/porchlamp-data/refresh-2026-10/state/refresh/MAIN.done
}

say "start (scope $(cat /proc/self/cgroup | tail -1))"
main_chain &
MAIN_PID=$!
# ONLY=MAIN restarts the MAIN stream alone while a keyless stream from an earlier launch is still running (10-08).
if [ "${ONLY:-}" != MAIN ]; then
  KEYLESS=1 bash "$RUN/gather_layer.sh" LK depsdev,scorecard,downloads >> "$RUN/logs/runner-LK.log" 2>&1 &
  K_PID=$!
fi
wait "$MAIN_PID"; say "MAIN exited rc=$?"
if [ -n "${K_PID:-}" ]; then wait "$K_PID"; say "KEYLESS exited rc=$?"; fi
