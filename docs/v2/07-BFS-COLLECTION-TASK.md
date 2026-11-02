# Task: breadth-first data collection for the Porchlamp leaderboard

**Who runs this:** the local data-collection agent (Qwen Flash-Next via Kimi Code CLI).
**Who wrote it:** the lead, 2026-09-16. Questions go to the lead; do not guess.

Read this whole file once before running anything. Then follow the phases in
order. Every command is safe to run again: a rerun resumes where it stopped.

---

## 0. The one idea you must keep in mind

We rank 5,753 GitHub repositories. Each repository needs data from about 20
"collectors" (one collector = one kind of API fetch, e.g. `pr_window`,
`governance`). Some collectors are cheap, some are expensive.

**Breadth-first means: run ONE cheap collector for ALL 5,753 repositories,
rebuild the leaderboard, and only then start the next collector.**

Never do the opposite (all collectors for one repo, then the next repo). The
leaderboard must exist early from shallow data and improve after each layer.

Layers, in order:

| Layer | Collectors to fetch (exact names) | What it unlocks | Cost |
|---|---|---|---|
| L0 | nothing — the cheap layer is **already on disk** (fetched 2026-09-11/12) | first leaderboard, mostly unrated | zero API calls |
| L1 | `pr_window`, then `governance` | responsiveness pillar, then rows 3.1/3.3/4.2 — **`governance` alone unlocks 0.175 of composite coverage, 29 % of the 0.60 rating floor**, and today `data/raw/governance` is empty | GraphQL ~6 points/repo; governance ~45 REST/repo |
| L2 | `repo_object,gfi,dependents,releases,runs` | eligibility gates, newcomer issues, dependents | REST/keyless, 1–3 calls each |
| L3 | `commits_365d,issues_counts` | criticality signals | REST, up to ~15 calls/repo |
| L4 | `author_history` | first-timer / returning-contributor rates | GraphQL, most expensive |

After **every** layer you derive (Step A); once `as-of` has a time (from L3 on), you also build, store and serve (Steps B–D).

---

## 1. Fixed paths and names (copy exactly)

```bash
REPO=/home/pctablet505/Projects/contributor-value-index
PY=/home/pctablet505/Projects/contributor-value-index/.venv/bin/python
RUN=/home/pctablet505/Projects/cvi-scratch/bfs-run
```

- Raw data lives in `$REPO/data/raw/<collector>/<owner>__<name>.json`. One file per (collector, repo). **A file that exists = done.** The gather script skips it.
- The repository list is `$REPO/data/raw/candidates.jsonl` (5,753 lines).
- Derived bundles go to `$REPO/data/derived/<owner>__<name>.json`.
- Everything you create that is not raw data (logs, snapshot directories, databases, the status file) goes under `$RUN`.
- `$REPO/data/cvi.db` is an OLD synthetic database. **Never serve it. Never delete it.**

## 2. Rules

1. **Do not edit any file** under `$REPO/porchlamp`, `$REPO/scripts`, `$REPO/tests`, `$REPO/web`, `$REPO/docs`. You run scripts; you do not change them.
2. **Do not delete anything** under `$REPO/data/raw`. Never run `rm` on it.
3. **Run only ONE `gather.py` process at a time.** The GitHub token is shared; two gatherers double the rate-limit hits.
4. **Do not pass `--force`** to `gather.py` except in Phase 5, where this file says so.
5. If any command prints a Python **`Traceback`**, stop that step, copy the last 30 lines of output into `$RUN/STATUS.md`, and report to the lead. Do not try to fix code.
6. After each step, append one line to `$RUN/STATUS.md` (format in section 9).
7. Rate limits: the scripts sleep between calls on their own. If a log shows many `WARN` lines in a row containing `403` or `429` or `rate limit`, stop, wait 60 minutes, rerun the same command.

## 3. Before you start (once)

```bash
cd /home/pctablet505/Projects/contributor-value-index
git pull --ff-only
gh auth status
.venv/bin/python -c "import porchlamp; print('porchlamp ok')"
wc -l data/raw/candidates.jsonl
ls data/raw/github_meta | wc -l
mkdir -p /home/pctablet505/Projects/cvi-scratch/bfs-run
```

Expected: `gh auth status` says `Logged in to github.com`; `porchlamp ok`; `5753`;
about `5731`. If `gh auth status` does not say logged in, stop and report.

Write the repository list once (every loop below reads this file):

```bash
cd /home/pctablet505/Projects/contributor-value-index
.venv/bin/python - <<'PYEOF'
import json
rows = [json.loads(l) for l in open("data/raw/candidates.jsonl") if l.strip()]
with open("/home/pctablet505/Projects/cvi-scratch/bfs-run/repos.txt", "w") as f:
    for r in rows:
        if r.get("owner"):
            f.write(f"{r['owner']} {r['name']} {r['ecosystem']}\n")
print(len(rows), "repos written")
PYEOF
```

Expected: `5753 repos written`.

Start the downloads backfill **now, in the background**. It uses a different
website (pypistats, 4 requests/min, about 4 hours) and does not touch the
GitHub token, so it can run alongside everything else:

```bash
cd /home/pctablet505/Projects/contributor-value-index
nohup .venv/bin/python scripts/backfill_downloads.py > /home/pctablet505/Projects/cvi-scratch/bfs-run/backfill.log 2>&1 &
echo $! > /home/pctablet505/Projects/cvi-scratch/bfs-run/backfill.pid
```

Check it later with `tail -3 /home/pctablet505/Projects/cvi-scratch/bfs-run/backfill.log`.

---

## 4. The four steps you repeat after every layer

Replace `LX` with the layer name (`L0`, `L1`, `L2`, `L3`, `L4`).

### Step A — derive (turn raw files into one bundle per repo)

```bash
cd /home/pctablet505/Projects/contributor-value-index
: > /home/pctablet505/Projects/cvi-scratch/bfs-run/derive-LX.log
xargs -P 4 -L 1 sh -c '.venv/bin/python scripts/derive.py "$0" "$1" --ecosystem "$2" --fetch > /dev/null 2>> /home/pctablet505/Projects/cvi-scratch/bfs-run/derive-LX.log || echo "DERIVE FAIL $0/$1" >> /home/pctablet505/Projects/cvi-scratch/bfs-run/derive-LX.log' < /home/pctablet505/Projects/cvi-scratch/bfs-run/repos.txt
ls data/derived | wc -l
grep -c "DERIVE FAIL" /home/pctablet505/Projects/cvi-scratch/bfs-run/derive-LX.log
```

- Measured: 50 repos in 1.1 seconds without network. With `--fetch` the
  first run also downloads a few small text files per repo (cached after
  that), so expect roughly 30–60 minutes the first time and a few minutes
  every later time.
- Expected: `ls data/derived | wc -l` close to `5753`. A few `DERIVE FAIL`
  lines (deleted or renamed repos) are normal; more than 200 is not — report.
- You must re-run derive after every layer: new raw files only reach the
  leaderboard through a new derive.

### Step B — build the snapshot directory

```bash
cd /home/pctablet505/Projects/contributor-value-index
AS_OF="$(.venv/bin/python -m porchlamp.snapshot as-of --data-dir data --print)" \
  && rm -rf /home/pctablet505/Projects/cvi-scratch/bfs-run/snap-LX \
  && .venv/bin/python -m porchlamp.snapshot build \
    --data-dir data \
    --out-dir /home/pctablet505/Projects/cvi-scratch/bfs-run/snap-LX \
    --snapshot-id "$(date -u +%Y-%m-%d)-LX" \
    --as-of "$AS_OF"
```

- `as-of` picks the snapshot's reference time: the newest `pr_window` record's time, rounded up to the minute, and only
  when every guard accepts it (README, "Pick as_of"). Never pass the wall clock: a `releases` or `issues_counts` file
  more than 14 days from `--as-of` silently drops `openssf_criticality` for its project, and no later step refuses it.
- When `as-of` exits 1, nothing is built, and its report (on stderr) says why. After L0 and L1 that is expected: there
  is no valid time until L2 and L3 have fetched `releases` and `issues_counts` after `pr_window`, so skip Steps B–D for
  that layer and go on to the next one. After L3 or L4, a `FAIL` line means stop and report it in STATUS.md.
- Prints three lines: `snapshot <id>`, `bundles <N>`, `out_dir <path>`.
  `bundles` must be close to the derived count. The rated/gated/unrated
  counts come from Step C.
- **`rated 0` after L0 is expected and correct.** The scoring refuses to rate a
  repo whose evidence coverage is below 0.60, and the cheap layer alone is
  below that. The rated count should grow after L1 and later layers.
- If it fails with a traceback that names one file under `data/derived/`,
  move that one file to `$RUN/quarantine/` (`mkdir -p` it first), note the
  file name in STATUS.md, and run Step B again.

### Step C — store (score for real and write the database)

```bash
cd /home/pctablet505/Projects/contributor-value-index
rm -f /home/pctablet505/Projects/cvi-scratch/bfs-run/cvi-LX.db
.venv/bin/python -m porchlamp.snapshot store \
  /home/pctablet505/Projects/cvi-scratch/bfs-run/snap-LX \
  --db /home/pctablet505/Projects/cvi-scratch/bfs-run/cvi-LX.db
```

- Prints, in this order: `snapshot <id>`, `rated N gated N unrated N`,
  `records_hash <hex>`, `constants_hash <hex>`, `snapshot_dump_hash <hex>`.
  Copy the `rated ...` line and the `records_hash` line to STATUS.md.
- Do **not** pass `--previous`. Layers are not separate points in time; the
  cross-snapshot detector would wrongly flag every repo.

### Step D — serve (replace the running website with the new database)

```bash
cd /home/pctablet505/Projects/contributor-value-index
if [ -f /home/pctablet505/Projects/cvi-scratch/bfs-run/serve.pid ]; then kill "$(cat /home/pctablet505/Projects/cvi-scratch/bfs-run/serve.pid)" 2>/dev/null; sleep 2; fi
nohup .venv/bin/python scripts/serve.py --db /home/pctablet505/Projects/cvi-scratch/bfs-run/cvi-LX.db --port 8000 > /home/pctablet505/Projects/cvi-scratch/bfs-run/serve.log 2>&1 &
echo $! > /home/pctablet505/Projects/cvi-scratch/bfs-run/serve.pid
sleep 3
curl -s http://127.0.0.1:8000/healthz
echo
curl -s http://127.0.0.1:8000/v1/porchlamp/snapshot | head -c 600
echo
```

- Expected: `/healthz` answers, and the snapshot JSON shows the same counts
  as Step C. The website is at http://127.0.0.1:8000/ .
- If `serve.log` says the database was refused (hash mismatch), do not retry
  with tricks; report it.

---

## 5. Phase 0 — first leaderboard, zero API calls (do this first, today)

Run Step A, B, C, D with `LX` = `L0`. Nothing to gather: the cheap collectors
(`github_meta pr_events pr_reviews contributors gfi actions_runs tree
commits_180d depsdev scorecard downloads`) already have ~5,730 files each.

Done when the website is up and STATUS.md has the L0 counts.

## 6. Phase 1 — `pr_window` (the layer that makes the leaderboard real)

**Before this phase**, the gather script needs the `--all` and `--collectors`
options. Check:

```bash
cd /home/pctablet505/Projects/contributor-value-index
git pull --ff-only
.venv/bin/python scripts/gather.py --help
```

The help text must list `--all` and `--collectors`. If it does not, stop and
tell the lead ("gather.py has no --collectors yet"); do not proceed with any
other gather command, because without the flag gather fetches every collector
per repo (depth-first), which is exactly what we must not do.

Gather:

```bash
cd /home/pctablet505/Projects/contributor-value-index
.venv/bin/python scripts/gather.py --all --collectors pr_window 2>&1 | tee -a /home/pctablet505/Projects/cvi-scratch/bfs-run/gather-L1.log
ls data/raw/pr_window | wc -l
```

- Progress at any time: `ls data/raw/pr_window | wc -l` (target 5,753).
- The run ends with a line `done: N clean, M with failures`. Run the **same
  command again**. Repos that failed are retried; repos already done are
  skipped instantly. When a run ends with `M` = 0, or two consecutive runs end
  with the same `M`, the layer is complete: the rest cannot be fetched (deleted,
  renamed, private).
- Rough duration: 6–10 hours of API time for all repos (GraphQL budget is
  5,000 points per hour; measured cost is about 6 points per repo).

Then, **in the same layer**, gather governance — it is the largest remaining
coverage unlock and the reason `rated` stays at 0:

```bash
cd /home/pctablet505/Projects/contributor-value-index
.venv/bin/python scripts/gather.py --all --collectors governance 2>&1 | tee -a /home/pctablet505/Projects/cvi-scratch/bfs-run/gather-L1-governance.log
ls data/raw/governance | wc -l
```

This is the most expensive collector per repo (~45 REST calls, roughly 50
hours for the whole universe at 5,000 calls/hour), so expect it to run for
about two days. Run it the same way as `pr_window`: repeat the command until
a run ends with no failure or two consecutive runs end with the same failure
count. Report progress from `ls data/raw/governance | wc -l`.

Then Step A, B, C, D with `LX` = `L1`. The `rated` count should now be above
zero for the first time. Write the before/after counts in STATUS.md — that
number is the single most important measurement in this whole runbook.

## 7. Phase 2, 3, 4 — the same loop with the next collectors

For each row, run gather (twice, as in Phase 1), then Steps A–D:

| Phase | `LX` | gather command |
|---|---|---|
| 2 | `L2` | `.venv/bin/python scripts/gather.py --all --collectors repo_object,gfi,dependents,releases,runs 2>&1 \| tee -a $RUN/gather-L2.log` |
| 3 | `L3` | `.venv/bin/python scripts/gather.py --all --collectors commits_365d,issues_counts 2>&1 \| tee -a $RUN/gather-L3.log` |
| 4 | `L4` | `.venv/bin/python scripts/gather.py --all --collectors author_history 2>&1 \| tee -a $RUN/gather-L4.log` |

Notes per phase:

- **L2 `gfi`**: this rewrites the existing `gfi` files (the old ones used a
  wrong label query). The file count will not change; that is fine. Progress
  for L2 is best read from `ls data/raw/repo_object | wc -l` and
  `ls data/raw/releases | wc -l`.
- **L2 `dependents`**: only `pypi`, `npm`, `crates` and `maven` repos get a
  file. `go` and `systems` are skipped by design (`skip` in the log is not a
  failure). Expect about 2,700 files, not 5,753.
- **L3 `issues_counts`** uses the GitHub *search* API (30 calls/min). This
  phase is slow — roughly a day. Leave it running; check progress with
  `ls data/raw/issues_counts | wc -l`.
- **L4 `author_history`** is the most expensive. Only start it after L3's
  leaderboard is served.
- **Projects side by side (D4, 2026-11-02).** `gather.py --workers N` (1-3; it
  needs `--all` and `--collectors`, and is refused with `--dirty`,
  `--fingerprint-only`, `--priority-keys`, `--repair-replies`, `--force` and
  `--gh-cli`) fetches N projects at once in one process, each computed exactly as
  one at a time, so rule 3 (one `gather.py` process) still holds. GitHub's
  requests in flight are capped by `PORCHLAMP_GH_INFLIGHT` (1-3, unset 1) and
  GraphQL by `PORCHLAMP_GH_PPM` points a minute. On the phone, L4 runs at the
  `l4_workers` setting (1-3, default 1) and L0, L3g and L3c at `github_workers`
  (1-2, default 1); L1, L2 and LK always run one at a time. The back-off: on any
  GitHub secondary-limit signal (a 429, a 403 with `retry-after` or a
  "secondary rate limit" message, a GraphQL error naming one) every request waits
  max(`retry-after`, 60 s), doubling with each signal in 24 h up to 1 h, then
  goes one request at a time for 30 min (doubling up to 4 h); six GitHub
  502/504 answers in 5 minutes also give one at a time for 30 min; the first
  secondary limit taken while several requests may be in flight (requests
  refused together count once) pins one at a time for the rest of the cycle
  (`state/refresh/concurrency.pinned`, retired when the next cycle starts). The
  runner sets `PORCHLAMP_GH_INFLIGHT` for each step: its worker count where it
  runs side by side, 1 everywhere else. To
  go back to one at a time by hand, set `l4_workers` to 1 with the app
  force-stopped: settings are read once, at start, so a running app ignores an
  adb edit of `settings.json`, and its next settings save overwrites it.

## 8. Phase 5 — refresh the cheap layer (only if the calendar says so)

The cheap layer was fetched on 2026-09-11/12. Windows older than 14 days go
stale. **If today is 2026-09-26 or later** when you finish Phase 2, refresh
the cheap layer before the next build (this is the only place `--force` is
allowed):

```bash
cd /home/pctablet505/Projects/contributor-value-index
.venv/bin/python scripts/gather.py --all --force --collectors github_meta,contributors,gfi,actions_runs,tree,commits_180d,depsdev,scorecard 2>&1 | tee -a /home/pctablet505/Projects/cvi-scratch/bfs-run/gather-refresh.log
```

Then Steps A–D with `LX` = the phase you are on (e.g. `L3`).

## 9. STATUS.md — one line per completed step

File: `/home/pctablet505/Projects/cvi-scratch/bfs-run/STATUS.md`. Append lines
like these (UTC date, what, the numbers the command printed):

```
2026-09-16 13:05 L0 derive   derived=5741 fail=12
2026-09-16 13:40 L0 build    bundles=5741
2026-09-16 13:52 L0 store    rated 0 gated 1912 unrated 3829 ; records_hash <paste>
2026-09-16 13:53 L0 serve    healthz=ok port=8000
2026-09-16 14:00 L1 gather   run1 done: 5100 clean, 653 with failures ; files=5100
2026-09-17 01:10 L1 gather   run2 done: 5720 clean, 33 with failures ; files=5720 -> complete
```

When you stop for any reason (traceback, rate limit wait, waiting for the
lead), write a line starting with `BLOCKED` and the reason.

## 10. What "done" looks like

- L0–L4 gathered (a run with no failure, or two consecutive runs with the same failure count, each).
- Website serving `cvi-L4.db`, STATUS.md showing the rated count after every layer.
- `backfill.log` ends with its summary line and no `pending` items for pypi.

Report to the lead with the path of STATUS.md. Do not start anything not
listed in this file.
