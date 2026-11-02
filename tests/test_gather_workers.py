"""``scripts/gather.py --workers N`` (D4, rollout 2026-11-01): projects side by side write what one at a time writes.

A scripted GitHub answers every call as a pure function of the query and its variables, after a delay drawn from the
call's own hash (so the order in which projects finish changes with N), with four injected faults: a dropped
connection on one page (read again after a wait), a login that resolves only alone, a project whose first page always
fails, and a page too heavy at 100 PRs (read again in pages of 50). The collector's clock is frozen. Runs at N = 1
and at N = 2 or 3 on copies of one data root write the same files: byte-identical when no author is shared between
projects, and identical up to the fields that reusing the user cache across projects may change when authors are
shared (``sha256``, ``data.cost_points``, ``data.rate_limit_remaining``, ``author_records[].eligibility_observed_at``).
"""

from __future__ import annotations

import collections
import contextlib
import hashlib
import importlib.util
import io
import itertools
import json
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest

from phone import httpgate
from porchlamp.collect import fingerprint, net
from porchlamp.collect import github_graphql as gql
from porchlamp.collect.envelope import write_envelope
from tests.helpers.gate import installed, loopback_gate

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)
PROJECTS = [(f"o{k}", f"r{k}") for k in range(1, 10)]
HEAVY = {"o1", "o3"}
ARGS = ["--all", "--skip-ineligible", "--collectors", "author_history"]
REPO_LINE = re.compile(r"^[\w.-]+/[\w.-]+ \[[^\]]*\]: ")
_SEQ = itertools.count()


def _h(*parts: object) -> int:
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "big")


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class FrozenDatetime(datetime):
    """``github_graphql.datetime`` with one instant for ``now``."""

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return NOW if tz is not None else NOW.replace(tzinfo=None)


class ScriptedGitHub:
    """``AuthorWindow``, ``AuthorPrsSearchBatch`` and ``AuthorEligibilityBatch`` answered from generated histories.

    Each project has 250-450 PRs over 360 days, newest first. With ``shared`` every project draws its authors from one
    pool of 47 logins; without, each project has its own (and ``u_flaky`` is o4's alone). ``on_call(kind,
    variables)`` runs before each answer."""

    def __init__(self, shared: bool, on_call=None) -> None:
        self.shared = shared
        self.on_call = on_call
        self.prs = {f"{o}/{n}": self._history(o, n) for o, n in PROJECTS}
        self.lock = threading.Lock()
        self.attempts: collections.Counter = collections.Counter()

    def _pools(self, owner: str) -> tuple[list[str], list[str]]:
        prefix = "s" if self.shared else f"{owner}x"
        recent = [f"{prefix}{j:02d}" for j in range(40 if self.shared else 22)]
        old = [f"{prefix}old{j}" for j in range(6)]  # authors seen only long ago: newcomers
        if self.shared or owner == "o4":
            recent.append("u_flaky")
        return recent, old

    def _history(self, owner: str, name: str) -> list[dict]:
        total = 250 + _h("size", owner, name) % 201
        recent, old = self._pools(owner)
        prs = []
        for j in range(total):
            age = 1 + (j * 359) // total
            created = NOW - timedelta(days=age, minutes=_h("minute", owner, j) % 900)
            pool = old if age > 200 and _h("old", owner, name, j) % 2 == 0 else recent
            login = pool[_h("author", owner, name, j) % len(pool)]
            kind = _h("state", owner, name, j) % 3 if age > 6 else 2
            closed = merged = None
            if kind == 0:
                merged = closed = _iso(created + timedelta(days=2))
            elif kind == 1:
                closed = _iso(created + timedelta(days=3))
            prs.append(
                {
                    "number": total - j,
                    "createdAt": _iso(created),
                    "closedAt": closed,
                    "mergedAt": merged,
                    "state": "MERGED" if merged else "CLOSED" if closed else "OPEN",
                    "author": {
                        "__typename": "User",
                        "login": login,
                        "createdAt": "2015-01-01T00:00:00Z",
                    },
                    "authorAssociation": "NONE",
                }
            )
        return prs

    def execute(self, query: str, variables: dict) -> dict:
        if "query AuthorWindow" in query:
            kind = "window"
            key = (
                kind,
                variables["owner"],
                variables["name"],
                variables.get("after"),
                variables["first"],
            )
        elif "query AuthorPrsSearchBatch" in query:
            kind = "search"
            key = (kind, *sorted(variables.items()))
        elif "query AuthorEligibilityBatch" in query:
            kind = "eligibility"
            key = (kind, *sorted(variables.items()))
        else:
            raise AssertionError(f"unscripted query {query[:40]!r}")
        with self.lock:
            self.attempts[key] += 1
            attempt = self.attempts[key]
        if self.on_call is not None:
            self.on_call(kind, variables)
        net.sleep((_h(*key) % 15) / 1000)  # stop-aware, like a request through the gate
        return getattr(self, kind)(variables, attempt)

    def window(self, variables: dict, attempt: int) -> dict:
        owner, name, after, first = (
            variables["owner"],
            variables["name"],
            variables.get("after"),
            variables["first"],
        )
        if owner == "o5" and after is None:
            raise RuntimeError("graphql errors for author window o5/r5: Something went wrong")
        if owner == "o2" and after == "c100" and attempt == 1:
            raise RuntimeError("gh api graphql rc=1: gh: [Errno 104] Connection reset by peer")
        if owner == "o7" and after == "c100" and first == 100 and attempt == 1:
            return {
                "errors": [
                    {
                        "message": "Something went wrong while executing your query. This may be the "
                        "result of a timeout, or it could be a GitHub bug."
                    }
                ]
            }
        prs = self.prs[f"{owner}/{name}"]
        offset = int(after[1:]) if after else 0
        page = prs[offset : offset + first]
        end = offset + len(page)
        return {
            "data": {
                "rateLimit": {"cost": 1, "remaining": 4000 + _h("w", owner, offset) % 900},
                "r": {
                    "nameWithOwner": f"{owner}/{name}",
                    "total": {"totalCount": len(prs)},
                    "pullRequests": {
                        "pageInfo": {"hasNextPage": end < len(prs), "endCursor": f"c{end}"},
                        "nodes": page,
                    },
                },
            }
        }

    def search(self, variables: dict, attempt: int) -> dict:
        data: dict = {}
        for i in range(sum(1 for k in variables if k.startswith("q"))):
            m = re.fullmatch(
                r"repo:(\S+) is:pr(?: author:(\S+) sort:created-asc)?", variables[f"q{i}"]
            )
            assert m, variables[f"q{i}"]
            prs = self.prs[m.group(1)]
            if m.group(2) is None:
                data[f"s{i}"] = {"issueCount": len(prs)}
                continue
            mine = sorted(
                (p for p in prs if p["author"]["login"] == m.group(2)), key=lambda p: p["createdAt"]
            )
            data[f"s{i}"] = {
                "issueCount": len(mine),
                "nodes": [
                    {k: p[k] for k in ("number", "createdAt", "closedAt", "mergedAt", "state")}
                    for p in mine[: variables["first"]]
                ],
            }
        data["rateLimit"] = {
            "cost": 1,
            "remaining": 4000 + _h("s", *sorted(variables.items())) % 900,
        }
        return {"data": data}

    def eligibility(self, variables: dict, attempt: int) -> dict:
        data: dict = {}
        errors = []
        for i in range(len(variables)):
            login = variables[f"l{i}"]
            if login == "u_flaky" and len(variables) > 1:
                data[f"u{i}"] = None
                errors.append(
                    {
                        "path": [f"u{i}"],
                        "message": "Something went wrong while executing your query.",
                    }
                )
                continue
            data[f"u{i}"] = {
                "createdAt": f"20{10 + _h('year', login) % 12:02d}-0{1 + _h('month', login) % 9}-01T00:00:00Z",
                "contributionsCollection": {
                    "contributionCalendar": {"totalContributions": _h("events", login) % 500}
                },
            }
        data["rateLimit"] = {
            "cost": 1,
            "remaining": 4000 + _h("e", *sorted(variables.values())) % 900,
        }
        return {"data": data, **({"errors": errors} if errors else {})}


def _root(tmp_path: Path, label: str, candidates=PROJECTS) -> Path:
    """A data root: ``candidates.jsonl`` and a minimal ``pr_window`` envelope per project (its ``count`` makes o1 and
    o3 heavy)."""
    root = tmp_path / label
    raw = root / "raw"
    raw.mkdir(parents=True)
    (raw / "candidates.jsonl").write_text(
        "".join(
            json.dumps({"owner": o, "name": n, "ecosystem": "pypi"}) + "\n" for o, n in candidates
        )
    )
    for o, n in dict.fromkeys(candidates):
        write_envelope(
            raw / "pr_window" / f"{o}__{n}.json",
            source="github-graphql:pr-window",
            data={"count": 6000 if o in HEAVY else 300},
            fetched_at=NOW.isoformat(),
        )
    return root


def _gather(monkeypatch: pytest.MonkeyPatch, root: Path) -> ModuleType:
    """``scripts/gather.py`` loaded by path, pointed at ``root``; the eligibility screen finds nothing to prune."""
    name = f"porchlamp_gather_workers_{next(_SEQ)}"
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/gather.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    module.DATA = root / "raw"
    module.CANDIDATES = module.DATA / "candidates.jsonl"
    monkeypatch.setattr(module, "should_skip_ineligible", _screen_nothing)
    return module


def _screen_nothing(*args, **kwargs) -> None:
    """The ineligibility screen, pruning nothing. The real one reads stored envelopes from disk, which lets other
    threads run meanwhile; so does this stub (a 2 ms stop-aware wait)."""
    net.sleep(0.002)


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gql, "datetime", FrozenDatetime)
    monkeypatch.setattr(gql, "_wait", lambda seconds: None)
    monkeypatch.setattr(
        fingerprint, "is_confirmed_black_hole_in_raw", lambda *args, **kwargs: False
    )


def _run(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    workers: int,
    *,
    shared: bool,
    out: io.StringIO | None = None,
    on_call=None,
    module: ModuleType | None = None,
) -> tuple[int, list[str]]:
    script = ScriptedGitHub(shared, on_call)
    monkeypatch.setattr(
        gql.GhCliTransport,
        "execute",
        lambda self, query, variables: script.execute(query, variables),
    )
    g = module or _gather(monkeypatch, root)
    out = out if out is not None else io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = g.main([*ARGS, "--workers", str(workers)])
    return rc, out.getvalue().splitlines()


def _files(folder: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(folder.glob("*.json"))}


def _envelopes(root: Path) -> dict[str, bytes]:
    return _files(root / "raw" / "author_history")


def _masked(blob: bytes) -> dict:
    doc = json.loads(blob)
    doc.pop("sha256")
    doc["data"].pop("cost_points")
    doc["data"].pop("rate_limit_remaining")
    for record in doc["data"]["author_records"]:
        record.pop("eligibility_observed_at", None)
    return doc


EXPECTED_FILES = sorted(f"{o}__{n}.json" for o, n in PROJECTS if o != "o5")


@pytest.mark.parametrize("workers", [2, 3])
def test_identical_envelopes_disjoint_authors(tmp_path, monkeypatch, frozen, workers) -> None:
    """T10, assertions 1 and 2: the same files, o5 (its first page always fails) absent from both, every envelope
    byte-identical; the faults were met (u_flaky resolved alone, o7 read whole after its heavy page)."""
    one, many = _root(tmp_path, "one"), _root(tmp_path, "many")
    assert _run(monkeypatch, one, 1, shared=False)[0] == 0
    assert _run(monkeypatch, many, workers, shared=False)[0] == 0
    a, b = _envelopes(one), _envelopes(many)
    assert sorted(a) == sorted(b) == EXPECTED_FILES
    assert a == b
    o4 = json.loads(a["o4__r4.json"])["data"]
    flaky = [r for r in o4["author_records"] if r["login"] == "u_flaky"]
    assert len(flaky) == 1 and flaky[0]["eligibility_unmeasured"] is False
    assert json.loads(a["o7__r7.json"])["data"]["count"] == len(ScriptedGitHub(False).prs["o7/r7"])


@pytest.mark.parametrize("workers", [2, 3])
def test_identical_after_masking_shared_authors(tmp_path, monkeypatch, frozen, workers) -> None:
    """T11, assertions 1, 3 and 5: with authors shared across projects the envelopes agree once the fields cache reuse
    may change are masked, and the user cache holds the same logins with the same values."""
    one, many = _root(tmp_path, "one"), _root(tmp_path, "many")
    assert _run(monkeypatch, one, 1, shared=True)[0] == 0
    assert _run(monkeypatch, many, workers, shared=True)[0] == 0
    a, b = _envelopes(one), _envelopes(many)
    assert sorted(a) == sorted(b) == EXPECTED_FILES
    assert {k: _masked(v) for k, v in a.items()} == {k: _masked(v) for k, v in b.items()}
    users_a, users_b = _files(one / "state" / "users"), _files(many / "state" / "users")
    assert len(users_a) == 47 and sorted(users_a) == sorted(users_b)

    def values(blob: bytes) -> tuple:
        doc = json.loads(blob)
        return doc["account_created_at"], doc["public_event_count"]

    assert {k: values(v) for k, v in users_a.items()} == {k: values(v) for k, v in users_b.items()}


@pytest.mark.parametrize("workers", [2, 3])
def test_done_line_and_repo_lines_match(tmp_path, monkeypatch, frozen, workers) -> None:
    """T12, assertion 4: the same ``done:`` line (the runner's completion rule reads it) and the same repo lines, in
    any order. An exception that escapes a project at N > 1 is that project's FAIL line, never an OK."""
    one, many = _root(tmp_path, "one"), _root(tmp_path, "many")
    rc1, lines1 = _run(monkeypatch, one, 1, shared=True)
    rcn, linesn = _run(monkeypatch, many, workers, shared=True)
    assert rc1 == rcn == 0
    done = [line for line in lines1 if line.startswith("done: ")]
    assert (
        done
        == [line for line in linesn if line.startswith("done: ")]
        == ["done: 8 clean, 1 with failures"]
    )
    repo1 = collections.Counter(line for line in lines1 if REPO_LINE.match(line))
    repon = collections.Counter(line for line in linesn if REPO_LINE.match(line))
    assert (
        repo1 == repon
        and sum(repo1.values()) == 9
        and repo1["o5/r5 [pypi]: FAIL author_history"] == 1
    )
    warn1 = collections.Counter(line for line in lines1 if line.startswith("  WARN"))
    assert (
        warn1 == collections.Counter(line for line in linesn if line.startswith("  WARN"))
        and len(warn1) == 1
    )

    escaped = _root(tmp_path, "escaped")
    g = _gather(monkeypatch, escaped)

    def screen(owner, name, **kwargs):
        if owner == "o9":
            raise RuntimeError("the screen broke")
        return None

    monkeypatch.setattr(g, "should_skip_ineligible", screen)
    rc, lines = _run(monkeypatch, escaped, workers, shared=True, module=g)
    assert rc == 0 and "o9/r9 [pypi]: FAIL the screen broke" in lines
    assert [line for line in lines if line.startswith("done: ")] == [
        "done: 7 clean, 2 with failures"
    ]


def test_heavy_projects_never_overlap(tmp_path, monkeypatch, frozen) -> None:
    """T13: o1 and o3 (stored window of 6,000 PRs) never run at the same time, while light projects fill the other
    slots. A heavy project waits up to 0.5 s inside the collector for another heavy one to join it, so a scheduler
    that let two in would be seen. Then ``_heavy`` itself: the count in the first 4 KiB of the stored window, above
    5,000; a missing file, or a file of 20 MiB or more with no count, is heavy."""
    root = _root(tmp_path, "many")
    cond = threading.Condition()
    open_now: list[str] = []
    peaks = {"heavy": 0, "all": 0}
    real = gql.collect_author_history

    def watched(raw_dir, owner, name, *args, **kwargs):
        with cond:
            open_now.append(owner)
            peaks["heavy"] = max(peaks["heavy"], sum(o in HEAVY for o in open_now))
            peaks["all"] = max(peaks["all"], len(open_now))
            cond.notify_all()
            if owner in HEAVY:
                cond.wait_for(lambda: sum(o in HEAVY for o in open_now) > 1, timeout=0.5)
        try:
            return real(raw_dir, owner, name, *args, **kwargs)
        finally:
            with cond:
                open_now.remove(owner)

    monkeypatch.setattr(gql, "collect_author_history", watched)
    rc, lines = _run(monkeypatch, root, 3, shared=False)
    assert rc == 0 and "done: 8 clean, 1 with failures" in lines
    assert peaks == {"heavy": 1, "all": 3}

    g = _gather(monkeypatch, root)
    assert g._heavy("o1", "r1") and not g._heavy("o2", "r2")
    window = root / "raw" / "pr_window"
    for count, heavy in ((5000, False), (5001, True)):
        write_envelope(
            window / "edge__count.json",
            source="x",
            data={"count": count},
            fetched_at=NOW.isoformat(),
        )
        assert g._heavy("edge", "count") is heavy
    assert g._heavy("never", "fetched")  # no stored window: unknown, run alone
    (window / "small__nocount.json").write_text('{"data": {}}')
    assert not g._heavy("small", "nocount")
    with (window / "big__nocount.json").open("wb") as fh:  # 20 MiB, sparse, no count in its head
        fh.seek(20 * 2**20)
        fh.write(b"}")
    assert g._heavy("big", "nocount")


def test_same_key_never_runs_twice_at_once(tmp_path, monkeypatch, frozen) -> None:
    """T14: a candidate listed twice in a row is never in flight twice: the second waits for the first, then finds its
    envelope and is skipped, exactly as one at a time does. o4 waits up to 0.5 s inside the collector for a second
    o4 to join it, so a scheduler that let it in would be seen."""
    candidates = [*PROJECTS[:4], PROJECTS[3], *PROJECTS[4:]]
    one, many = _root(tmp_path, "one", candidates), _root(tmp_path, "many", candidates)
    cond = threading.Condition()
    open_now: list[str] = []
    peak = {"o4": 0}
    real = gql.collect_author_history

    def watched(raw_dir, owner, name, *args, **kwargs):
        with cond:
            open_now.append(owner)
            peak["o4"] = max(peak["o4"], open_now.count("o4"))
            cond.notify_all()
            if owner == "o4":
                cond.wait_for(lambda: open_now.count("o4") > 1, timeout=0.5)
        try:
            return real(raw_dir, owner, name, *args, **kwargs)
        finally:
            with cond:
                open_now.remove(owner)

    rc1, lines1 = _run(monkeypatch, one, 1, shared=False)
    monkeypatch.setattr(gql, "collect_author_history", watched)
    rcn, linesn = _run(monkeypatch, many, 3, shared=False)
    assert rc1 == rcn == 0 and peak["o4"] == 1
    assert collections.Counter(
        line for line in lines1 if REPO_LINE.match(line)
    ) == collections.Counter(line for line in linesn if REPO_LINE.match(line))
    assert lines1.count("o4/r4 [pypi]: OK") == linesn.count("o4/r4 [pypi]: OK") == 2
    assert "done: 9 clean, 1 with failures" in lines1 and "done: 9 clean, 1 with failures" in linesn
    assert _envelopes(one) == _envelopes(many)


def test_stop_in_one_worker_stops_the_pool_and_reraises(tmp_path, monkeypatch, frozen) -> None:
    """T15: the collector is cancelled (a watchdog restart) while o6 starts. Every worker adopted the collector's
    cancel binding, so each stops at its next request; the pool re-raises ``Stopped`` and prints no ``done:`` line.
    What was written before is whole; a rerun then completes the layer with T10's envelopes."""
    reference = _root(tmp_path, "one")
    assert _run(monkeypatch, reference, 1, shared=False)[0] == 0
    expected = _envelopes(reference)
    root = _root(tmp_path, "many")
    cancel = threading.Event()

    def on_call(kind: str, variables: dict) -> None:
        if kind == "window" and variables["owner"] == "o6":
            cancel.set()

    out = io.StringIO()
    gate = loopback_gate()
    with installed(gate):
        gate.bind_cancel(cancel)
        try:
            with pytest.raises(httpgate.Stopped):
                _run(monkeypatch, root, 3, shared=False, out=out, on_call=on_call)
        finally:
            gate.bind_cancel(None)
    assert not any(line.startswith("done: ") for line in out.getvalue().splitlines())
    partial = _envelopes(root)
    assert 0 < len(partial) < len(expected)
    assert all(blob == expected[name] for name, blob in partial.items())
    rc, lines = _run(monkeypatch, root, 3, shared=False)
    assert rc == 0 and "done: 8 clean, 1 with failures" in lines
    assert _envelopes(root) == expected


def test_thread_lines_keep_lines_whole(tmp_path, monkeypatch) -> None:
    """T16: three threads write their lines in pieces, interleaved by barriers; the stream below receives whole lines
    only, each in one write, and every line arrives intact. A piece left without its newline is written at close."""
    g = _gather(monkeypatch, _root(tmp_path, "lines", []))
    writes: list[str] = []
    lock = threading.Lock()

    class Below:
        def write(self, text: str) -> int:
            with lock:
                writes.append(text)
            return len(text)

        def flush(self) -> None:
            pass

    lines = g._ThreadLines(Below())
    barrier = threading.Barrier(3, timeout=10)

    def worker(tag: str) -> None:
        lines.write(f"{tag}/one [pypi]: ")
        barrier.wait()
        lines.write("OK")
        barrier.wait()
        lines.write(f"\n  WARN {tag} first\n  WARN {tag} sec")
        barrier.wait()
        lines.write("ond\n")
        barrier.wait()
        lines.flush()

    tags = ["a", "b", "c"]
    threads = [threading.Thread(target=worker, args=(tag,)) for tag in tags]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert writes and all(text.endswith("\n") for text in writes)
    expected = [f"{t}/one [pypi]: OK" for t in tags] + [f"  WARN {t} first" for t in tags]
    expected += [f"  WARN {t} second" for t in tags]
    assert collections.Counter("".join(writes).splitlines()) == collections.Counter(expected)
    lines.write("no newline yet")
    assert writes[-1] != "no newline yet"
    lines.close()
    assert writes[-1] == "no newline yet"


REFUSED = [
    (["--dirty"], "--dirty"),
    (["--fingerprint-only"], "--fingerprint-only"),
    (["--repair-replies"], "--repair-replies"),
    (["--force"], "--force"),
    (["--gh-cli"], "--gh-cli"),
]


def test_workers_refused_with_dirty_priority_repair_force(
    tmp_path, monkeypatch, capsys, frozen
) -> None:
    """T17: more than one worker runs one breadth-first layer (``--all --collectors``) and nothing else: the flags
    whose shared files or order assume one project at a time are refused, and so is ``--gh-cli`` (the throttle lives
    in the HTTPS transport), as are 0, 4, and a run without ``--all`` or ``--collectors``."""
    root = _root(tmp_path, "empty", [])
    g = _gather(monkeypatch, root)
    layer = ["--all", "--collectors", "author_history"]
    assert g.main([*layer, "--workers", "2"]) == 0
    assert "done: 0 clean, 0 with failures" in capsys.readouterr().out
    keys = tmp_path / "keys.txt"
    keys.write_text("o1__r1\n")
    for extra, flag in [*REFUSED, (["--priority-keys", str(keys)], "--priority-keys")]:
        with pytest.raises(SystemExit) as info:
            g.main([*layer, "--workers", "2", *extra])
        err = capsys.readouterr().err
        assert info.value.code == 2 and flag in err and "--workers" in err, (flag, err)
    for argv in (
        [*layer, "--workers", "4"],
        [*layer, "--workers", "0"],
        ["--all", "--workers", "2"],
        ["--collectors", "author_history", "--workers", "3"],
    ):
        with pytest.raises(SystemExit) as info:
            g.main(argv)
        assert info.value.code == 2, argv
        assert "--workers" in capsys.readouterr().err
