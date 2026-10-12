#!/bin/bash
# Budget-guarded gather of ONE layer into the REFRESH data root (adapted from
# /home/pctablet505/Projects/cvi-scratch/bfs-run/gather_layer.sh).
#
# Usage: gather_layer.sh LAYER_NAME COLLECTOR_LIST [MAX_RUNS]
#   env KEYLESS=1  -> no GitHub budget guard or sampling (deps.dev / scorecard / registries only)
#   env NSHARDS=N  -> N gather processes on DISJOINT repo shards (repo i goes to shard i mod N). gather.py has no shard
#                     option and reads <data_root>/raw/candidates.jsonl, so each shard gets its own tiny data root
#                     <data>/shards/<LAYER>-s<k>/ whose raw/ holds a shard candidates.jsonl plus SYMLINKS to the real
#                     collector folders (and state -> real state): envelopes land in the one real tree.
#                     Concurrency safety (read 2026-10-07): envelopes are temp+os.replace atomic and disjoint by repo;
#                     the user cache is per-login files written temp+os.replace (last writer wins, same data class);
#                     fingerprints.jsonl is only used with --dirty (not used here); no other shared file.
#
# Differences from the 2026-09 runner (all deliberate):
#  * FRESHNESS WITHOUT --force. gather.py --force refetches every repo on every run, so a rerun after a
#    rate-limit stop would restart the layer and "two runs agree" could never be reached. Instead, ONCE per
#    layer (marker state/refresh/<LAYER>.staged) the layer's old envelope folders are renamed into
#    <data>/raw-stale-2026-09-23/ (same filesystem, no data written). Then gather runs WITHOUT --force: an
#    envelope on disk = a fresh one = the checkpoint, so every rerun resumes and a failed repo is retried.
#    A repo that never succeeds ends with no envelope (counted as a failure); its old envelope stays in raw-stale.
#  * --graphql-bulk always on. Floors 500 GraphQL points / 500 REST calls (the D-COL-10 reserve).
#  * Completion marker state/refresh/<LAYER>.complete -> a restart skips finished layers.
set -uo pipefail
# PHONE: paths come from the phone's home and engine checkout; set -m gives each gatherer its own process group
# so kill -TERM -"$GPID" still works without setsid.
set -m
WT="${PORCHLAMP_ENGINE:-$HOME/porchlamp-engine}"
DATAROOT="${DATAROOT:-${PORCHLAMP_PHONE_HOME:-$HOME/porchlamp}}"
RUN="$WT/phone"
LOGDIR="${LOGDIR:-$DATAROOT/logs}"
PY=${PY:-python}
export PYTHONPATH="$WT" PORCHLAMP_DATA_ROOT="$DATAROOT" TMPDIR="${PORCHLAMP_TMP:-$DATAROOT/tmp}/porchlamp-refresh" PYTHONDONTWRITEBYTECODE=1
mkdir -p "$TMPDIR" "$LOGDIR" "$DATAROOT/state/refresh" "$DATAROOT/raw-stale-2026-09-23"
LX="$1"
COLS="$2"
MAXRUNS="${3:-60}"
KEYLESS="${KEYLESS:-0}"
NSHARDS="${NSHARDS:-1}"
SAMPLE_SECS=60
FLOOR_GQL="${FLOOR_GQL:-500}" FLOOR_CORE="${FLOOR_CORE:-500}"
RAW="$DATAROOT/raw"
cd "$WT" || exit 1
# L1 (pr_window) runs through gather_pagesize.py: page_size 25 instead of 100 (100 504s on busy repos; see that file).
if [ -z "${GATHER_ENTRY:-}" ]; then GATHER_ENTRY=scripts/gather.py; [ "$LX" = L1 ] && GATHER_ENTRY="$RUN/gather_pagesize.py"; fi
[ "${PHONE_DRY_RUN:-0}" = 1 ] && { echo "GATHER_ENTRY=$GATHER_ENTRY"; exit 0; }

say() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] $LX: $*"; }

if [ -f "$DATAROOT/state/refresh/$LX.complete" ]; then say "already complete (marker) -- skipping"; exit 0; fi

# ---- stage the stale envelopes once ----
if [ ! -f "$DATAROOT/state/refresh/$LX.staged" ]; then
  for c in ${COLS//,/ }; do
    if [ -d "$RAW/$c" ]; then
      if [ -e "$DATAROOT/raw-stale-2026-09-23/$c" ]; then say "BLOCKED: raw-stale-2026-09-23/$c already exists"; exit 5; fi
      mv "$RAW/$c" "$DATAROOT/raw-stale-2026-09-23/$c" && say "staged old $c -> raw-stale-2026-09-23/$c"
    fi
  done
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$DATAROOT/state/refresh/$LX.staged"
fi

for c in ${COLS//,/ }; do mkdir -p "$RAW/$c"; done

is_num() { case "$1" in ('' | ''|*[!0-9]*) return 1 ;; (*) return 0 ;; esac; }
counts() { for c in ${COLS//,/ }; do printf '%s=%s ' "$c" "$(ls "$RAW/$c" 2>/dev/null | wc -l)"; done; }

# GET /rate_limit LIES for this token (docs/v2/08, measured 2026-09-20); the X-Ratelimit-* headers of a real call
# are honest. Cost: 1 core call + 1 graphql point per sample.
sample() {  # -> "core_remaining gql_remaining search reset_epoch"
  local hdr core reset gql
  hdr=$(gh api user --include 2>/dev/null | tr -d '\r')
  core=$(printf '%s\n' "$hdr" | awk 'tolower($1)=="x-ratelimit-remaining:"{print $2; exit}')
  reset=$(printf '%s\n' "$hdr" | awk 'tolower($1)=="x-ratelimit-reset:"{print $2; exit}')
  gql=$(gh api graphql -f query='{ rateLimit { remaining } }' --jq '.data.rateLimit.remaining' 2>/dev/null)
  printf '%s\t%s\t%s\t%s\n' "$core" "$gql" "30" "$reset"
}

wait_for_budget() {
  [ "$KEYLESS" = 1 ] && return 0
  local line core gql search reset now sleep_s
  line=$(sample); read -r core gql search reset <<< "$line"
  if ! is_num "$gql" && ! is_num "$core"; then say "budget: both probes failed -- proceeding"; return 0; fi
  is_num "$core" || core=$FLOOR_CORE
  is_num "$gql"  || gql=$FLOOR_GQL
  is_num "$reset" || reset=$(( $(date +%s) + 300 ))
  say "budget: core=$core graphql=$gql reset=$(date -u -d "@$reset" '+%H:%M' 2>/dev/null || echo "$reset")"
  if [ "$gql" -ge "$FLOOR_GQL" ] && [ "$core" -ge "$FLOOR_CORE" ]; then return 0; fi
  now=$(date +%s); sleep_s=$(( reset - now + 15 )); [ "$sleep_s" -lt 10 ] && sleep_s=10
  say "budget too low (floors gql>$FLOOR_GQL core>$FLOOR_CORE) -> sleeping ${sleep_s}s to the hourly reset"
  sleep "$sleep_s"
}

warn_storm() {  # warn_storm LOGFILE: >=8 recent WARN lines that mention 403/429/rate limit
  local n
  n=$(tail -60 "$1" 2>/dev/null | grep -Eic 'WARN.*(403|429|rate.?limit)' || true)
  [ "${n:-0}" -ge 8 ]
}

disk_ok() {  # stop rule: free disk under 5 GB (phone)
  local free_kb; free_kb=$(df --output=avail -k "$DATAROOT" | tail -1)
  [ "${free_kb:-0}" -ge $((5*1024*1024)) ]
}

run_shard() {  # run_shard SHARD_INDEX  (uses globals; runs in a background subshell when NSHARDS>1)
  local sh="$1" GROOT LOG SAMPLES prev_fail="" run before GPID i stopped line core gql search reset summary fail rc
  if [ "$NSHARDS" -gt 1 ]; then
    GROOT="$DATAROOT/shards/$LX-s$sh"; LOG="$LOGDIR/gather-$LX-s$sh.log"; SAMPLES="$LOGDIR/ratelimit-$LX-s$sh.log"
  else
    GROOT="$DATAROOT"; LOG="$LOGDIR/gather-$LX.log"; SAMPLES="$LOGDIR/ratelimit-$LX.log"
  fi
  local tag="$LX${NSHARDS:+/s$sh}"
  say2() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] $tag: $*"; }
  for run in $(seq 1 "$MAXRUNS"); do
    say2 "=== gather run $run of $MAXRUNS (collectors: $COLS) ==="
    disk_ok || { say2 "BLOCKED: free disk under 5 GB"; return 6; }
    wait_for_budget
    before=$( { wc -l < "$LOG"; } 2>/dev/null || echo 0)
    PORCHLAMP_DATA_ROOT="$GROOT" nohup "$PY" -u "$GATHER_ENTRY" --all --graphql-bulk --collectors "$COLS" >> "$LOG" 2>&1 < /dev/null &
    GPID=$!
    say2 "gather pid=$GPID -> log $LOG"
    for i in $(seq 1 6); do
      sleep 5
      kill -0 "$GPID" 2>/dev/null && break
      if tail -n +"$((before + 1))" "$LOG" 2>/dev/null | grep -aq 'done: .* clean, .* with failures'; then break; fi
      if [ "$i" -eq 6 ]; then
        wait "$GPID" 2>/dev/null; rc=$?
        say2 "BLOCKED: gatherer exited within 30 s rc=$rc and no done line -- environment/network. Last lines:"
        tail -12 "$LOG"; return 4
      fi
    done
    stopped=""
    while kill -0 "$GPID" 2>/dev/null; do
      sleep "$SAMPLE_SECS"
      disk_ok || { say2 "BLOCKED: free disk under 5 GB -- stopping gatherer"; kill -TERM -"$GPID" 2>/dev/null; return 6; }
      if [ "$KEYLESS" = 1 ]; then
        echo "$(date -u '+%Y-%m-%d %H:%M:%S') run=$run keyless $(counts)" >> "$SAMPLES"
      else
        line=$(sample); read -r core gql search reset <<< "$line"
        echo "$(date -u '+%Y-%m-%d %H:%M:%S') run=$run core=${core:-?} graphql=${gql:-?} $(counts)" >> "$SAMPLES"
        if is_num "${gql:-x}" && is_num "${core:-x}"; then
          [ "$gql" -lt "$FLOOR_GQL" ] && stopped="graphql budget ${gql}"
          [ "$core" -lt "$FLOOR_CORE" ] && stopped="core budget ${core}"
        fi
        warn_storm "$LOG" && stopped="${stopped:-WARN 403/429 streak in log}"
      fi
      if [ -n "$stopped" ] && kill -0 "$GPID" 2>/dev/null; then
        say2 "rate limit reached ($stopped) -> stopping gatherer, will resume after the reset"
        kill -TERM -"$GPID" 2>/dev/null; sleep 3; kill -KILL -"$GPID" 2>/dev/null
        break
      fi
      if tail -n +"$((before + 1))" "$LOG" 2>/dev/null | grep -q "^Traceback (most recent call last)"; then
        say2 "BLOCKED: Traceback in $LOG (a code bug) -- stopping this layer"
        kill -TERM -"$GPID" 2>/dev/null
        return 3
      fi
    done
    wait "$GPID" 2>/dev/null
    # This run's own done line only (the 2026-09-21 L3g false-COMPLETE came from reading the whole log).
    summary=$(tail -n +"$((before + 1))" "$LOG" 2>/dev/null | grep -a 'done: .* clean, .* with failures' | tail -1)
    say2 "run $run summary: ${summary:-<NO done line for THIS run>} files: $(counts)"
    fail=$(echo "$summary" | sed -n 's/.*clean, \([0-9]*\) with failures.*/\1/p')
    if [ -z "$stopped" ] && [ -n "$fail" ] && [ "$fail" = "$prev_fail" ]; then
      say2 "COMPLETE: two consecutive runs with the same failure count ($fail). Shard done."
      echo "failures=$fail" > "$DATAROOT/state/refresh/$LX.n$NSHARDS.shard$sh.complete"
      return 0
    fi
    prev_fail="$fail"
  done
  say2 "NOT confirmed complete after $MAXRUNS runs (failures still moving or budget-bound); rerun the runner."
  return 2
}

# ---- build the shard roots (candidates are unchanged between restarts, so the split is stable) ----
if [ "$NSHARDS" -gt 1 ]; then
  for ((k=0; k<NSHARDS; k++)); do
    S="$DATAROOT/shards/$LX-s$k"; mkdir -p "$S/raw"
    awk -v n="$NSHARDS" -v k="$k" 'NF && ((++i-1) % n == k)' "$RAW/candidates.jsonl" > "$S/raw/candidates.jsonl"
    for e in "$RAW"/*; do
      b=$(basename "$e")
      case "$b" in candidates.jsonl|universe_checkpoint.json) continue;; esac
      ln -sfn "$e" "$S/raw/$b"
    done
    ln -sfn "$DATAROOT/state" "$S/state"
    say "shard $k: $(wc -l < "$S/raw/candidates.jsonl") repos -> $S"
  done
fi

pids=(); for ((k=0; k<NSHARDS; k++)); do
  rm -f "$DATAROOT/state/refresh/$LX.n$NSHARDS.shard$k.complete.stale"
  if [ -f "$DATAROOT/state/refresh/$LX.n$NSHARDS.shard$k.complete" ]; then say "shard $k already complete"; continue; fi
  run_shard "$k" & pids+=("$!")
  sleep 2
done
rc=0; for p in "${pids[@]}"; do wait "$p" || rc=$?; done
if [ "$rc" -eq 0 ]; then
  say "COMPLETE: all $NSHARDS shard(s) confirmed. Layer done."
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$DATAROOT/state/refresh/$LX.complete"
fi
exit "$rc"
