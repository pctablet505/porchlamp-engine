"""``scripts/derive.py --all``: the batch derivation a snapshot rebuild runs.

Every repo the data root's candidates file lists is derived at one pinned ``--as-of``, with the
ecosystem its line names, on ``--workers`` processes. A repo that cannot be derived is reported
by name and skipped, the others are written, and the exit status is 1 when any failed. Bundles
are written atomically, and the bytes written do not depend on the number of workers.

The batch runs as the command a rebuild runs (a subprocess) wherever worker processes or the
process's own limits are involved; the rest call ``main`` in this process.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "derive.py"
_spec = importlib.util.spec_from_file_location("derive_batch_cli", SCRIPT)
derive = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(derive)

ENVELOPES = ROOT / "tests" / "fixtures" / "numpy-envelopes"
AS_OF = "2026-09-12T00:00:00+00:00"
SUBPROCESS_ENV = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
REPOS = (("numpy", "numpy", "pypi"), ("acme", "widget", "npm"),
         ("rust-lang", "hashbrown", "crates"), ("golang", "tools", "go"))

#: Runs scripts/derive.py with the kernel refusing to grow any file past 4 KiB (RLIMIT_FSIZE), as
#: a full disk would: a bundle write fails part-way, after its first 4 KiB reached the file.
_FILE_SIZE_LIMITED = (
    "import resource, runpy, signal, sys\n"
    "signal.signal(signal.SIGXFSZ, signal.SIG_IGN)\n"
    "resource.setrlimit(resource.RLIMIT_FSIZE, (4096, resource.getrlimit(resource.RLIMIT_FSIZE)[1]))\n"
    "sys.argv = sys.argv[1:]\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)


def _data_dir(tmp_path: Path, lines: list[str] | None = None, repos=REPOS) -> Path:
    """A data root holding the numpy fixture's envelopes under each repo's key, and a candidates
    file listing ``repos`` (or holding ``lines`` verbatim).

    Each repo also gets a pull-request window: its reported window ends at the derivation's
    reference time, so every bundle depends on the time it was derived at."""
    data = tmp_path / "data"
    for owner, name, eco in repos:
        for src in sorted(ENVELOPES.glob("*/*.json")):
            key = f"{eco}__{name}" if src.parent.name == "downloads" else f"{owner}__{name}"
            dest = data / "raw" / src.parent.name / f"{key}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
        window = data / "raw" / "pr_window" / f"{owner}__{name}.json"
        window.parent.mkdir(parents=True, exist_ok=True)
        window.write_text(json.dumps({"fetched_at": "2026-09-11T00:00:00Z", "source": "test", "data": {
            "prs": [], "window_anchor": "2026-09-11T00:00:00Z"}}), encoding="utf-8")
    if lines is None:
        lines = [json.dumps({"owner": o, "name": n, "ecosystem": e}) for o, n, e in repos]
    (data / "raw").mkdir(parents=True, exist_ok=True)
    (data / "raw" / "candidates.jsonl").write_text("".join(f"{ln}\n" for ln in lines), encoding="utf-8")
    return data


def _written(data: Path) -> list[str]:
    derived = data / "derived"
    return sorted(p.name for p in derived.iterdir()) if derived.is_dir() else []


def _batch(data: Path, *extra: str, wrapper: str | None = None) -> subprocess.CompletedProcess:
    prefix = [sys.executable, "-c", wrapper, str(SCRIPT)] if wrapper else [sys.executable, str(SCRIPT)]
    return subprocess.run([*prefix, "--all", "--as-of", AS_OF, "--data-dir", str(data), *extra],
                          capture_output=True, text=True, timeout=180, env=SUBPROCESS_ENV, cwd=ROOT)


@pytest.mark.parametrize("workers", [1, 3])
def test_a_repo_that_cannot_be_derived_is_reported_and_the_others_are_written(tmp_path, workers):
    data = _data_dir(tmp_path)
    # bytes that are not UTF-8: reading this repo's envelope raises, and nothing else is wrong
    (data / "raw" / "github_meta" / "acme__widget.json").write_bytes(b"\xff\xfe corrupt \x00")
    run = _batch(data, "--workers", str(workers))
    assert run.returncode == 1, run.stdout + run.stderr
    failures = [ln for ln in run.stderr.splitlines() if ln.startswith("derive failed: ")]
    assert len(failures) == 1 and failures[0].startswith("derive failed: acme/widget: UnicodeDecodeError: ")
    assert _written(data) == ["golang__tools.json", "numpy__numpy.json", "rust-lang__hashbrown.json"]
    assert run.stdout.splitlines()[-1] == "derived 3 of 4 repos; 1 failed"


def test_a_missing_or_unknown_ecosystem_is_refused_by_name_never_derived_as_pypi(tmp_path, capsys):
    data = _data_dir(tmp_path, lines=[
        json.dumps({"owner": "numpy", "name": "numpy", "ecosystem": "pypi"}),
        json.dumps({"owner": "acme", "name": "widget", "ecosystem": "rubygems"}),
        json.dumps({"owner": "golang", "name": "tools"}),
    ])
    code = derive.main(["--all", "--as-of", AS_OF, "--data-dir", str(data)])
    out = capsys.readouterr()
    assert code == 1
    assert "derive failed: acme/widget: unknown ecosystem 'rubygems'" in out.err
    assert "derive failed: golang/tools: no ecosystem" in out.err
    assert _written(data) == ["numpy__numpy.json"]
    assert out.out.splitlines()[-1] == "derived 1 of 3 repos; 2 failed"


def test_a_candidates_line_that_names_no_repo_is_refused_by_its_line_number(tmp_path, capsys):
    data = _data_dir(tmp_path, lines=[
        json.dumps({"owner": "numpy", "name": "numpy", "ecosystem": "pypi"}),
        '{"owner": "acme", "name": "wid',
        "",
        json.dumps(["golang", "tools", "go"]),
        json.dumps({"owner": "rust-lang", "ecosystem": "crates"}),
    ])
    code = derive.main(["--all", "--as-of", AS_OF, "--data-dir", str(data)])
    out = capsys.readouterr()
    assert code == 1
    refused = [ln for ln in out.err.splitlines() if ln.startswith("derive failed: ")]
    assert refused == [f"derive failed: candidates.jsonl line {n}: not a JSON object naming owner and name"
                       for n in (2, 4, 5)]
    assert _written(data) == ["numpy__numpy.json"]
    assert out.out.splitlines()[-1] == "derived 1 of 4 repos; 3 failed"


def test_without_a_candidates_file_the_batch_is_refused_rather_than_derived_as_pypi(tmp_path, capsys):
    data = _data_dir(tmp_path)
    (data / "raw" / "candidates.jsonl").unlink()
    with pytest.raises(SystemExit) as stop:
        derive.main(["--all", "--as-of", AS_OF, "--data-dir", str(data)])
    assert stop.value.code == 2
    assert "candidates.jsonl" in capsys.readouterr().err
    assert _written(data) == []


@pytest.mark.parametrize("argv, message", [
    (["--all", "--ecosystem", "npm"], "--ecosystem"),
    (["--all", "--workers", "0"], "--workers"),
])
def test_a_batch_flag_that_would_be_ignored_or_cannot_run_is_refused(tmp_path, capsys, argv, message):
    data = _data_dir(tmp_path)
    with pytest.raises(SystemExit) as stop:
        derive.main([*argv, "--as-of", AS_OF, "--data-dir", str(data)])
    assert stop.value.code == 2
    assert message in capsys.readouterr().err
    assert _written(data) == []


@pytest.mark.parametrize("form", [["numpy", "numpy", "--ecosystem", "pypi"], ["--all"]])
def test_a_bundle_write_that_fails_part_way_keeps_the_previous_bundle_whole(tmp_path, form):
    data = _data_dir(tmp_path, repos=REPOS[:1])
    previous = data / "derived" / "numpy__numpy.json"
    previous.parent.mkdir(parents=True)
    previous.write_bytes(b'{"previous": "bundle"}')
    run = subprocess.run(
        [sys.executable, "-c", _FILE_SIZE_LIMITED, str(SCRIPT), *form,
         "--as-of", AS_OF, "--data-dir", str(data)],
        capture_output=True, text=True, timeout=180, env=SUBPROCESS_ENV, cwd=ROOT)
    assert run.returncode == 1, run.stdout + run.stderr
    assert "File too large" in run.stderr  # the injected failure did reach the bundle write
    assert previous.read_bytes() == b'{"previous": "bundle"}'
    assert _written(data) == ["numpy__numpy.json"]  # and no temporary file is left beside it


def test_the_bundles_written_do_not_depend_on_the_number_of_worker_processes(tmp_path):
    one = _data_dir(tmp_path / "one")
    three = _data_dir(tmp_path / "three")
    runs = [_batch(one, "--workers", "1"), _batch(three, "--workers", "3")]
    for run in runs:
        assert run.returncode == 0, run.stdout + run.stderr
        assert run.stdout.splitlines()[-1] == "derived 4 of 4 repos; 0 failed"
    names = [f"{o}__{n}.json" for o, n, _ in REPOS]
    assert _written(one) == _written(three) == sorted(names)
    now = datetime.fromisoformat(AS_OF)
    for (owner, name, eco), fname in zip(REPOS, names, strict=True):
        written = (one / "derived" / fname).read_bytes()
        assert (three / "derived" / fname).read_bytes() == written
        # the bytes a direct derive_repo call serializes to, at the same reference time
        expected = derive.derive_repo(owner, name, derive.Ecosystem(eco), one, now=now)
        assert written == json.dumps(expected, indent=2, sort_keys=True).encode("utf-8")
        # control: at another reference time the bytes differ, so a worker that lost the pinned
        # time would be caught here
        later = derive.derive_repo(owner, name, derive.Ecosystem(eco), one, now=now + timedelta(days=1))
        assert written != json.dumps(later, indent=2, sort_keys=True).encode("utf-8")


_NOT_ONE_SEGMENT = "owner/name is not a single path segment"
_HOSTILE_LINES = [
    json.dumps({"owner": "../../escape", "name": "x", "ecosystem": "npm"}),
    json.dumps({"owner": "ok", "name": "../../../e2", "ecosystem": "npm"}),
]


def test_a_candidate_whose_owner_or_name_is_not_one_path_segment_is_refused(tmp_path):
    path = tmp_path / "candidates.jsonl"
    path.write_text("".join(f"{ln}\n" for ln in [
        *_HOSTILE_LINES, json.dumps({"owner": "981011512", "name": "--", "ecosystem": "npm"})]),
        encoding="utf-8")
    first, second, real = derive._read_candidates(path)
    assert (first.label, first.repo, first.refused) == ("candidates.jsonl line 1", None, _NOT_ONE_SEGMENT)
    assert (second.label, second.repo, second.refused) == ("candidates.jsonl line 2", None, _NOT_ONE_SEGMENT)
    assert real.refused is None and real.repo == ("981011512", "--", derive.Ecosystem.NPM)


@pytest.mark.parametrize("owner, name", [
    ("a/b", "x"), ("a", "b/c"), ("a\\b", "x"), ("a", "b\\c"), (".", "x"), ("a", ".."), ("..", "x"),
    ("a\0b", "x"), ("a", "b\nc"), ("a", "b\x1fc"), ("a\x7f", "x"), ("a", "b\tc"),
])
def test_each_character_that_would_leave_a_path_segment_is_refused(tmp_path, owner, name):
    path = tmp_path / "candidates.jsonl"
    path.write_text(json.dumps({"owner": owner, "name": name, "ecosystem": "npm"}) + "\n", encoding="utf-8")
    (entry,) = derive._read_candidates(path)
    assert (entry.repo, entry.refused) == (None, _NOT_ONE_SEGMENT)


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def test_a_hostile_candidates_line_writes_no_file_outside_the_derived_directory(tmp_path, capsys):
    data = _data_dir(tmp_path, lines=[
        *_HOSTILE_LINES, json.dumps({"owner": "numpy", "name": "numpy", "ecosystem": "pypi"})],
        repos=REPOS[:1])
    before = _tree(tmp_path)
    code = derive.main(["--all", "--as-of", "2026-10-24T18:10:00+00:00", "--data-dir", str(data),
                        "--workers", "1"])
    err = capsys.readouterr().err
    created = _tree(tmp_path) - before
    assert created == {"data/derived", "data/derived/numpy__numpy.json"}
    assert code == 1
    assert err.count(_NOT_ONE_SEGMENT) == 2
