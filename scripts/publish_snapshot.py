#!/usr/bin/env python3
"""Build, store, export, verify, and stage a Porchlamp static snapshot from a data root.

    python scripts/publish_snapshot.py \\
        --data-dir /tmp/drop-data \\
        --work-dir /tmp/publish-work \\
        --target-site /tmp/porchlamp-site \\
        --snapshot-id 2026-10-28 \\
        --as-of 2026-10-28T04:00:00Z

Runs every offline stage in order and stops before staging if any check fails:
1. ``scripts/derive.py --all --data-dir ... --as-of ...``
2. ``python -m porchlamp.snapshot build ...``
3. ``python -m porchlamp.snapshot store ...``
4. ``python -m porchlamp.snapshot stats ...``
5. ``scripts/export_static.py ...`` (passing ``--previous-site`` when available)
6. ``scripts/verify_static_export.py ...`` (full API parity, mutants, report, chrome, projects, home, crawl)
7. Stages into ``--target-site`` preserving ``.git``, ``CNAME``, ``LICENSE``, root verification files, all historical
   ``black-holes/<id>/`` reports, and at most one immediately prior ``data/<prev_id>/`` directory, then renders
   ``README.md`` and ``CITATION.cff`` deterministically from ``snapshot.json`` and ``report.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:-[a-z0-9]{1,16})?$")
PRESERVED_ROOT_NAMES: frozenset[str] = frozenset({".git", "CNAME", "LICENSE"})
ROOT_VERIFICATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^google[0-9a-f]{16}\.html$"),
    re.compile(r"^[0-9a-f]{32}\.txt$"),
)


class PublishError(Exception):
    """Raised when a build, verification, or staging step fails."""


def _log(message: str) -> None:
    print(message, flush=True)


def _is_preserved_root_entry(name: str) -> bool:
    if name in PRESERVED_ROOT_NAMES:
        return True
    return any(p.fullmatch(name) for p in ROOT_VERIFICATION_PATTERNS)


def validate_snapshot_id(snapshot_id: str) -> str:
    cleaned = snapshot_id.strip()
    if not SNAPSHOT_ID_RE.fullmatch(cleaned):
        raise PublishError(f"invalid --snapshot-id {snapshot_id!r} (expected YYYY-MM-DD)")
    return cleaned


def resolve_as_of(data_dir: Path, explicit_as_of: str | None) -> str:
    """Resolve ISO-8601 ``as_of`` with UTC offset from ``--as-of`` or ``state/refresh/MAIN.done``."""
    raw = explicit_as_of
    if not raw:
        marker = data_dir / "state" / "refresh" / "MAIN.done"
        if marker.is_file():
            raw = marker.read_text(encoding="utf-8").strip()
    if not raw:
        raise PublishError("--as-of is required when state/refresh/MAIN.done is absent")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise PublishError(f"invalid ISO-8601 --as-of timestamp: {raw!r}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def render_readme(snapshot: dict[str, Any], report: dict[str, Any]) -> str:
    """Render ``porchlamp/README.md`` deterministically from ``snapshot.json`` and ``report.json``."""
    sid = snapshot["snapshot_id"]
    as_of = snapshot["as_of"]
    bench = snapshot["benchmark_version"]
    counts = snapshot["counts"]
    pvc = snapshot["persona_visible_counts"]
    vals = report["values"]

    undecided_cut = str(vals.get("undecided_cut") or "0")
    busy_sentence = (
        f" We could not judge {undecided_cut} busy projects yet: we hold only\n"
        f"  the newest {vals['pr_cap']} pull requests of each, and they do not reach back to {vals['window_start']}."
        if undecided_cut != "0"
        else ""
    )

    return (
        '<img src="favicon.svg" width="72" height="72" alt="Porchlamp\'s symbol: a porch lamp with its light on">\n'
        "\n"
        "# Porchlamp\n"
        "\n"
        "**Find the projects that leave the light on.**\n"
        "\n"
        "A porch lamp left on tells a visitor they are expected. Porchlamp ranks\n"
        "open-source projects the same way: by what happens to a stranger's pull\n"
        "request, not by how many stars the project has.\n"
        "\n"
        "**Open the ranking: https://porchlamp.org/**\n"
        "\n"
        "**Read the report, with charts and the method: https://porchlamp.org/black-holes/**\n"
        "\n"
        "## Stars don't review pull requests\n"
        "\n"
        f"Measured on the {sid} snapshot of {vals['corpus_projects']} projects:\n"
        "\n"
        f"- **More than 1 in {vals['one_in']} active projects are black holes.** Of the {vals['judged']} licensed,\n"
        "  maintained software projects with at least ten counted outside pull requests\n"
        f"  in six months, {vals['black_holes']} ({vals['share']}%) left most of them with no human reply, review,\n"
        f"  merge or close within two weeks or since.{busy_sentence}\n"
        f"- **More stars, more silence.** The black-hole share rises with popularity: {vals['band1_share']}%\n"
        f"  of projects with 1k\u201310k stars, {vals['band2_share']}% at 10k\u201350k, and {vals['band3_share']}% at 50k or more.\n"
        f"- **Stars barely predict the ranking.** Across the {vals['ranked']} ranked projects the\n"
        f"  rank correlation between stars and the Porchlamp score is {vals['rho']}, and {vals['top100_overlap']} of the\n"
        "  100 most-starred projects makes the top 100.\n"
        "\n"
        "## Where the numbers come from\n"
        "\n"
        f"We followed {vals['corpus_prs']} pull requests from {vals['corpus_authors']} outside contributors to "
        f"{vals['corpus_projects']}\n"
        "projects over six months: who got an answer, how fast, how many reached a\n"
        "decision, whether first-time contributors came back, and how much other software\n"
        "depends on each project. Black holes, and projects that are archived,\n"
        "unmaintained, unlicensed or not software, are listed but never ranked. The rest\n"
        "are scored on five pillars and re-weighted for five kinds of contributor. Every\n"
        "number traces back to a recorded GitHub response and its content hash.\n"
        "\n"
        "## This repository\n"
        "\n"
        "This repository is the published site and nothing else: a static snapshot\n"
        "served by GitHub Pages. Every ranking, filter and project page is answered\n"
        f"from precomputed JSON under `data/{sid}/` by `api.js` (project records sharded into 256\n"
        f"hash-bucketed files under `data/{sid}/repos/`), so the site needs no\n"
        "server.\n"
        "\n"
        f"- **Snapshot ID:** `{sid}` (measured as of `{as_of}`, benchmark `{bench}`)\n"
        f"- **Universe Counts:** `{counts['rated']:,}` rated (ranked) \u00b7 `{counts['gated']:,}` gated (Stage 0) "
        f"\u00b7 `{counts['unrated']:,}` unrated (`<60%` coverage) \u00b7 `{counts['total']:,}` total\n"
        f"- **Persona Visible Counts:** `balanced: {pvc['balanced']:,}` \u00b7 "
        f"`first_timer: {pvc['first_timer']:,}` \u00b7 `career_capital: {pvc['career_capital']:,}` \u00b7 "
        f"`senior_systems: {pvc['senior_systems']:,}` \u00b7 `ai_assisted: {pvc['ai_assisted']:,}`\n"
        f"- **Verification Hashes:** `constants_hash: {snapshot['constants_hash'][:12]}\u2026` \u00b7 "
        f"`manifest_root: {snapshot['manifest_root'][:12]}\u2026` \u00b7 "
        f"`canonical_dump_hash: {snapshot['canonical_dump_hash'][:12]}\u2026`\n"
        "\n"
        "## License and citation\n"
        "\n"
        'The data under `data/` and the text of this site are licensed under [CC BY 4.0](LICENSE): reuse them freely, '
        'with credit to "Porchlamp rankings" and a link to https://porchlamp.org/. To cite the dataset, use GitHub\'s '
        '"Cite this repository" button, which reads `CITATION.cff`. To cite a finding, link the report frozen for this '
        f"snapshot: https://porchlamp.org/black-holes/{sid}/ . The code in this repository is not covered by this "
        "license, and the engine that collects the data is private.\n"
    )


def render_citation_cff(snapshot: dict[str, Any], report: dict[str, Any]) -> str:
    """Render ``porchlamp/CITATION.cff`` deterministically from ``snapshot.json`` and ``report.json``."""
    sid = snapshot["snapshot_id"]
    release_date = sid[:10]
    vals = report["values"]
    return (
        "cff-version: 1.2.0\n"
        'message: "If you use the Porchlamp rankings or their data, please cite them as below."\n'
        'title: "Porchlamp rankings: open-source projects ranked by what happens to a stranger\'s pull request"\n'
        "type: dataset\n"
        "authors:\n"
        "  - given-names: Rahul\n"
        "    family-names: Kumar\n"
        "    alias: pctablet505\n"
        f'version: "{sid}"\n'
        f"date-released: {release_date}\n"
        'url: "https://porchlamp.org/"\n'
        'repository: "https://github.com/pctablet505/porchlamp"\n'
        "license: CC-BY-4.0\n"
        f'abstract: "A six-month snapshot of {vals["corpus_prs"]} pull requests from {vals["corpus_authors"]} outside '
        f'contributors to {vals["corpus_projects"]} GitHub projects. Each project is ranked by what happens to a '
        "stranger's pull request: whether a human replies, reviews or merges it, and how soon.\"\n"
        "keywords:\n"
        "  - open source\n"
        "  - pull requests\n"
        "  - maintainer responsiveness\n"
        "  - first contribution\n"
    )


def stage_site(
    exported_site: Path,
    target_site: Path,
    snapshot_id: str,
    *,
    keep_previous_data: int = 1,
) -> dict[str, Any]:
    """Stage ``exported_site`` into ``target_site``, preserving operational files, frozen reports, and 1 prior data dir."""
    if (exported_site / ".github").exists() or (target_site / ".github").exists():
        raise PublishError("refusing to stage: .github directory is forbidden in the static site repository")

    snap_file = exported_site / "data" / snapshot_id / "snapshot.json"
    rep_file = exported_site / "data" / snapshot_id / "report.json"
    if not snap_file.is_file():
        raise PublishError(f"exported site is missing data/{snapshot_id}/snapshot.json")

    snapshot = json.loads(snap_file.read_text(encoding="utf-8"))
    report = json.loads(rep_file.read_text(encoding="utf-8")) if rep_file.is_file() else None

    target_site.mkdir(parents=True, exist_ok=True)
    hold_dir = target_site.parent / f".porchlamp-stage-hold-{os.getpid()}"
    if hold_dir.exists():
        shutil.rmtree(hold_dir)
    hold_dir.mkdir(parents=True)

    kept_frozen: list[str] = []
    kept_data: list[str] = []
    try:
        # 1. Preserve historical black-holes/<old_id>/ directories (never overwrite the new snapshot_id).
        old_bh = target_site / "black-holes"
        if old_bh.is_dir():
            (hold_dir / "black-holes").mkdir(parents=True, exist_ok=True)
            for child in sorted(old_bh.iterdir(), key=lambda p: p.name):
                if child.is_dir() and child.name != snapshot_id:
                    shutil.move(str(child), str(hold_dir / "black-holes" / child.name))
                    kept_frozen.append(child.name)

        # 2. Preserve at most `keep_previous_data` newest prior data/<prev_id>/ directories.
        old_data = target_site / "data"
        if old_data.is_dir() and keep_previous_data > 0:
            prior_dirs = sorted(
                (p for p in old_data.iterdir() if p.is_dir() and p.name != snapshot_id),
                key=lambda p: p.name,
            )
            (hold_dir / "data").mkdir(parents=True, exist_ok=True)
            for child in prior_dirs[-keep_previous_data:]:
                shutil.move(str(child), str(hold_dir / "data" / child.name))
                kept_data.append(child.name)

        # 3. Clean non-preserved root entries from target_site.
        for entry in list(target_site.iterdir()):
            if _is_preserved_root_entry(entry.name):
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()

        # 4. Copy newly exported site into target_site.
        for src in sorted(exported_site.iterdir(), key=lambda p: p.name):
            if src.name == ".github":
                raise PublishError("refusing to copy .github into target site")
            dst = target_site / src.name
            if src.is_dir():
                shutil.copytree(src, dst, symlinks=False)
            else:
                shutil.copy2(src, dst)

        # 5. Restore preserved historical black-holes/<old_id>/ and prior data/<prev_id>/.
        if (hold_dir / "black-holes").is_dir():
            (target_site / "black-holes").mkdir(parents=True, exist_ok=True)
            for child in sorted((hold_dir / "black-holes").iterdir(), key=lambda p: p.name):
                dst = target_site / "black-holes" / child.name
                if not dst.exists():
                    shutil.move(str(child), str(dst))

        if (hold_dir / "data").is_dir():
            (target_site / "data").mkdir(parents=True, exist_ok=True)
            for child in sorted((hold_dir / "data").iterdir(), key=lambda p: p.name):
                dst = target_site / "data" / child.name
                if not dst.exists():
                    shutil.move(str(child), str(dst))

        # 6. Render README.md and CITATION.cff when report.json is present.
        if report is not None:
            (target_site / "README.md").write_text(render_readme(snapshot, report), encoding="utf-8")
            (target_site / "CITATION.cff").write_text(render_citation_cff(snapshot, report), encoding="utf-8")
    finally:
        if hold_dir.exists():
            shutil.rmtree(hold_dir, ignore_errors=True)

    return {
        "snapshot_id": snapshot_id,
        "kept_frozen_reports": kept_frozen,
        "kept_previous_data": kept_data,
        "counts": snapshot.get("counts"),
    }


def _run_cmd(cmd: list[str], *, step: str, stdout_file: Path | None = None) -> None:
    _log(f"==> {step}")
    env = dict(os.environ)
    py_path = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{REPO_ROOT}:{py_path}" if py_path else str(REPO_ROOT)
    if stdout_file is not None:
        stdout_file.parent.mkdir(parents=True, exist_ok=True)
        with open(stdout_file, "wb") as out_fh:
            proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env, stdout=out_fh)
    else:
        proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env)
    if proc.returncode != 0:
        raise PublishError(f"{step} failed with exit code {proc.returncode}")


def run_pipeline(
    *,
    data_dir: Path,
    work_dir: Path,
    target_site: Path,
    snapshot_id: str,
    as_of: str,
    workers: int = 4,
    previous_snapshot_dir: Path | None = None,
    quick_verify: bool = False,
    no_mutants: bool = False,
) -> dict[str, Any]:
    """Run derive -> build -> store -> stats -> export_static -> verify_static_export -> stage_site."""
    sid = validate_snapshot_id(snapshot_id)
    resolved_as_of = resolve_as_of(data_dir, as_of)

    work_dir.mkdir(parents=True, exist_ok=True)
    snap_dir = work_dir / "snapshots" / sid
    db_path = work_dir / f"cvi-{sid}.db"
    stats_path = work_dir / f"stats-{sid}.json"
    export_out = work_dir / "exported-site"

    for stale in (snap_dir, db_path, stats_path, export_out):
        if stale.is_dir():
            shutil.rmtree(stale)
        elif stale.exists():
            stale.unlink()

    py = sys.executable
    _run_cmd(
        [
            py,
            str(REPO_ROOT / "scripts" / "derive.py"),
            "--all",
            "--data-dir",
            str(data_dir),
            "--as-of",
            resolved_as_of,
            "--workers",
            str(workers),
        ],
        step="derive bundles",
    )

    _run_cmd(
        [
            py,
            "-m",
            "porchlamp.snapshot",
            "build",
            "--data-dir",
            str(data_dir),
            "--out-dir",
            str(snap_dir),
            "--snapshot-id",
            sid,
            "--as-of",
            resolved_as_of,
            "--workers",
            str(workers),
        ],
        step="build snapshot directory",
    )

    store_cmd = [
        py,
        "-m",
        "porchlamp.snapshot",
        "store",
        str(snap_dir),
        "--db",
        str(db_path),
    ]
    if previous_snapshot_dir is not None:
        store_cmd += ["--previous", str(previous_snapshot_dir)]
    _run_cmd(store_cmd, step="score and store snapshot database")

    _run_cmd(
        [
            py,
            "-m",
            "porchlamp.snapshot",
            "stats",
            str(db_path),
            "--data-dir",
            str(data_dir),
            "--as-of",
            resolved_as_of,
        ],
        step="compute report stats",
        stdout_file=stats_path,
    )

    has_previous_site = (target_site / "projects").is_dir()
    export_cmd = [
        py,
        str(REPO_ROOT / "scripts" / "export_static.py"),
        "--db",
        str(db_path),
        "--out",
        str(export_out),
        "--stats",
        str(stats_path),
        "--snapshot-dir",
        str(snap_dir),
        "--data-dir",
        str(data_dir),
    ]
    if has_previous_site:
        export_cmd += ["--previous-site", str(target_site)]
    _run_cmd(export_cmd, step="export static site")

    verify_cmd = [
        py,
        str(REPO_ROOT / "scripts" / "verify_static_export.py"),
        "--db",
        str(db_path),
        "--site",
        str(export_out),
        "--stats",
        str(stats_path),
        "--snapshot-dir",
        str(snap_dir),
        "--data-dir",
        str(data_dir),
    ]
    if has_previous_site:
        verify_cmd += ["--previous-site", str(target_site)]
    if quick_verify:
        verify_cmd.append("--quick")
    if no_mutants:
        verify_cmd.append("--no-mutants")
    _run_cmd(verify_cmd, step="verify static export")

    staged = stage_site(export_out, target_site, sid)
    _log(f"staged snapshot {sid} into {target_site}")
    return staged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, help="data root holding raw/ and candidates.jsonl")
    parser.add_argument("--work-dir", type=Path, help="scratch directory for snapshot build, SQLite DB, and export")
    parser.add_argument("--target-site", type=Path, required=True, help="checkout of pctablet505/porchlamp to stage")
    parser.add_argument("--snapshot-id", default=None, help="snapshot ID (YYYY-MM-DD; defaults to as-of UTC date)")
    parser.add_argument("--as-of", default=None, help="ISO-8601 reference timestamp (defaults to state/refresh/MAIN.done)")
    parser.add_argument("--workers", type=int, default=max(1, min(8, os.cpu_count() or 2)))
    parser.add_argument("--previous-snapshot-dir", type=Path, default=None)
    parser.add_argument("--stage-only", type=Path, default=None, help="stage an already verified export directory")
    parser.add_argument("--quick-verify", action="store_true", help="pass --quick to verify_static_export.py (tests)")
    parser.add_argument("--no-mutants", action="store_true", help="pass --no-mutants to verify_static_export.py (tests)")
    parser.add_argument("--status-out", type=Path, default=None, help="write JSON staging summary to this file")
    args = parser.parse_args(argv)

    try:
        if args.stage_only is not None:
            if not args.snapshot_id:
                raise PublishError("--stage-only requires --snapshot-id")
            sid = validate_snapshot_id(args.snapshot_id)
            summary = stage_site(args.stage_only.resolve(), args.target_site.resolve(), sid)
        else:
            if args.data_dir is None or args.work_dir is None:
                raise PublishError("--data-dir and --work-dir are required unless --stage-only is used")
            resolved_as_of = resolve_as_of(args.data_dir.resolve(), args.as_of)
            sid = validate_snapshot_id(args.snapshot_id or resolved_as_of[:10])
            summary = run_pipeline(
                data_dir=args.data_dir.resolve(),
                work_dir=args.work_dir.resolve(),
                target_site=args.target_site.resolve(),
                snapshot_id=sid,
                as_of=resolved_as_of,
                workers=max(1, args.workers),
                previous_snapshot_dir=args.previous_snapshot_dir.resolve() if args.previous_snapshot_dir else None,
                quick_verify=args.quick_verify,
                no_mutants=args.no_mutants,
            )
        if args.status_out is not None:
            args.status_out.parent.mkdir(parents=True, exist_ok=True)
            args.status_out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    except PublishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
