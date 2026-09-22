"""``scripts/gather.py --priority-keys``: an ordering, never a filter."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("cvi_gather_priority", ROOT / "scripts" / "gather.py")
gather = importlib.util.module_from_spec(_spec)
sys.modules["cvi_gather_priority"] = gather
_spec.loader.exec_module(gather)

#: The per-run summary the BFS runner (cvi-scratch/bfs-run/gather_layer.sh)
#: reads to decide that a run finished.
RUNNER_DONE_RE = re.compile(r"done: .* clean, .* with failures")

REPOS = [("a", "one", "pypi"), ("b", "two", "npm"), ("c", "three", "crates"), ("d", "four", "maven")]


def _run(tmp_path, monkeypatch, capsys, extra_args):
    cands = tmp_path / "candidates.jsonl"
    cands.write_text("".join(json.dumps({"owner": o, "name": n, "ecosystem": e}) + "\n"
                             for o, n, e in REPOS))
    monkeypatch.setattr(gather, "CANDIDATES", cands)
    seen = []

    def fake_gather_repo(owner, name, eco, **kwargs):
        seen.append(f"{owner}__{name}")
        return {"author_history": "fail" if name == "three" else "ok"}

    monkeypatch.setattr(gather, "gather_repo", fake_gather_repo)
    assert gather.main(["--all", "--collectors", "author_history", *extra_args]) == 0
    return seen, capsys.readouterr().out.splitlines()


def test_listed_repos_go_first_and_every_repo_is_still_gathered(tmp_path, monkeypatch, capsys):
    keys = tmp_path / "priority.txt"
    keys.write_text("# comment\nc__three\n\na__one\nzz__not_a_candidate\n")
    seen, out = _run(tmp_path, monkeypatch, capsys, ["--priority-keys", str(keys)])
    assert seen == ["a__one", "c__three", "b__two", "d__four"]  # candidate order within each segment
    assert sorted(seen) == sorted(f"{o}__{n}" for o, n, _ in REPOS)  # none dropped, none repeated
    assert "priority: 2 of 4 repos first" in out[0]
    assert "1 listed keys are not candidates" in out[0]
    marker = [line for line in out if line.startswith(gather.PRIORITY_DONE_MARKER)]
    assert marker == [f"{gather.PRIORITY_DONE_MARKER}: 2 repos (1 failed)"]
    # printed right after the last priority repo, before the rest
    assert out.index(marker[0]) == out.index("c/three [crates]: FAIL author_history") + 1
    assert out[-1] == "done: 3 clean, 1 with failures"


def test_marker_can_never_pass_for_the_runners_done_line(tmp_path, monkeypatch, capsys):
    """A run killed after the priority segment must not read as finished:
    the BFS runner greps each run's log for its done line."""
    keys = tmp_path / "priority.txt"
    keys.write_text("a__one\n")
    _, out = _run(tmp_path, monkeypatch, capsys, ["--priority-keys", str(keys)])
    matching = [line for line in out if RUNNER_DONE_RE.search(line)]
    assert matching == ["done: 3 clean, 1 with failures"]


def test_without_the_option_order_and_output_are_unchanged(tmp_path, monkeypatch, capsys):
    seen, out = _run(tmp_path, monkeypatch, capsys, [])
    assert seen == ["a__one", "b__two", "c__three", "d__four"]
    assert not any(line.startswith(("priority", gather.PRIORITY_DONE_MARKER)) for line in out)
