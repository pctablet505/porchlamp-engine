"""Tests for :mod:`porchlamp.snapshot.asof` and ``python -m porchlamp.snapshot as-of`` (D2-A, D2-E).

The as_of is the earliest instant every guard accepts: the newest ``pr_window`` ``window_anchor``
(rounded up to the whole minute), moved later only when a this-cycle ``releases`` or
``issues_counts`` anchor is more than 14 days after it. The tests build small data roots with
:func:`porchlamp.collect.envelope.write_envelope`; the module under test is imported inside each
test so a missing module fails each test by name, not the whole file at collection.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import pytest

from porchlamp.collect.envelope import write_envelope
from porchlamp.derive.gate_evidence import pr_window_coverage_in
from porchlamp.snapshot.cli import main

UTC = timezone.utc
#: The newest pull-request record's anchor on the 2026-10-31 build; its minute rounds up to ``A_UP``.
A = datetime(2026, 10, 24, 18, 9, 24, 886000, tzinfo=UTC)
A_UP = datetime(2026, 10, 24, 18, 10, 0, tzinfo=UTC)
DAY = timedelta(days=1)
REPO_ROOT = Path(__file__).resolve().parents[2]


def _asof() -> Any:
    return importlib.import_module("porchlamp.snapshot.asof")


def _derive_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "derive_for_asof_tests", REPO_ROOT / "scripts" / "derive.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pr_data(
    anchor: datetime, *, truncated: bool = False, cap: int = 2000, oldest: Optional[datetime] = None
) -> dict[str, Any]:
    created = oldest if oldest is not None else anchor - DAY
    prs = [{"number": 1, "createdAt": created.isoformat(), "state": "MERGED"}]
    return {
        "window_anchor": anchor.isoformat(),
        "window_days": 180,
        "cap": cap,
        "count": len(prs),
        "truncated": truncated,
        "prs": prs,
    }


def _releases_data(anchor: datetime) -> dict[str, Any]:
    return {
        "window_days": 365,
        "window_anchor": anchor.isoformat(),
        "source": "releases",
        "releases": [],
        "releases_truncated": False,
        "tags": None,
        "recent_count": 0,
    }


def _issues_data(anchor: datetime) -> dict[str, Any]:
    return {
        "window_days": 90,
        "window_anchor": anchor.isoformat(),
        "closed_90d": 1,
        "updated_90d": 1,
        "sample_size": 1,
        "sample_total": 1,
        "sample": [],
        "comments_per_issue": 1.0,
    }


def _history_data(anchor: datetime, band: Optional[datetime] = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "window_anchor": anchor.isoformat(),
        "window_days": 365,
        "cap": 500,
        "count": 0,
        "truncated": False,
        "prs": [],
        "sampled_authors": [],
        "author_records": [],
    }
    if band is not None:
        data["dr90_band_anchor"] = band.isoformat()
    return data


def _put(root: Path, layer: str, key: str, data: Mapping[str, Any], at: datetime) -> None:
    write_envelope(root / "raw" / layer / f"{key}.json", f"test:{layer}", data, at.isoformat())


def _root(
    tmp_path: Path,
    repos: Mapping[str, Mapping[str, Any]],
    *,
    candidates: Optional[Sequence[str]] = None,
    name: str = "root",
) -> Path:
    """A data root. ``repos`` maps ``owner__name`` to what to write: ``pr`` (an anchor), ``truncated``,
    ``cap``, ``oldest`` (the oldest recorded PR), ``releases``, ``issues``, ``history`` (anchors) and ``band``.
    ``candidates`` defaults to every key in ``repos``."""
    root = tmp_path / name
    for key, spec in repos.items():
        if "pr" in spec:
            _put(
                root,
                "pr_window",
                key,
                _pr_data(
                    spec["pr"],
                    truncated=spec.get("truncated", False),
                    cap=spec.get("cap", 2000),
                    oldest=spec.get("oldest"),
                ),
                spec["pr"],
            )
        if "releases" in spec:
            _put(root, "releases", key, _releases_data(spec["releases"]), spec["releases"])
        if "issues" in spec:
            _put(root, "issues_counts", key, _issues_data(spec["issues"]), spec["issues"])
        if "history" in spec:
            _put(
                root,
                "author_history",
                key,
                _history_data(spec["history"], spec.get("band")),
                spec["history"],
            )
    keys = list(repos) if candidates is None else list(candidates)
    lines = []
    for key in keys:
        owner, _, repo = key.partition("__")
        lines.append(json.dumps({"owner": owner, "name": repo, "ecosystem": "pypi"}))
    (root / "raw").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "candidates.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def _standard(tmp_path: Path, **extra: Mapping[str, Any]) -> Path:
    """Three repos of one cycle: pull requests at ``A``, releases a couple of days later, issues later still."""
    repos: dict[str, Mapping[str, Any]] = {
        "acme__alpha": {
            "pr": A,
            "releases": A + 2 * DAY,
            "issues": A + 4 * DAY,
            "history": A + 6 * DAY,
        },
        "acme__beta": {
            "pr": A - 2 * DAY,
            "releases": A + 2 * DAY + timedelta(hours=1),
            "issues": A + 4 * DAY + timedelta(hours=1),
            "history": A + 6 * DAY,
        },
        "acme__gamma": {
            "pr": A - 5 * DAY,
            "releases": A + 3 * DAY,
            "issues": A + 5 * DAY,
            "history": A + 7 * DAY,
        },
    }
    repos.update(extra)
    return _root(tmp_path, repos)


def _by_guard(results: Sequence[Any]) -> dict[str, Any]:
    return {result.guard: result for result in results}


def test_t1_as_of_is_the_newest_pr_window_anchor_rounded_up_to_the_minute(tmp_path: Path) -> None:
    asof = _asof()
    root = _standard(tmp_path)
    assert asof.earliest_valid_as_of(root) == A_UP
    assert asof.earliest_valid_as_of(root).isoformat() == "2026-10-24T18:10:00+00:00"
    # An anchor already on a whole minute is kept as it is, not pushed on a minute.
    whole = datetime(2026, 10, 24, 18, 9, 0, tzinfo=UTC)
    root2 = _root(
        tmp_path,
        {
            "acme__alpha": {"pr": whole, "releases": whole + DAY, "issues": whole + 2 * DAY},
            "acme__beta": {
                "pr": whole - 3 * DAY,
                "releases": whole + DAY,
                "issues": whole + 2 * DAY,
            },
        },
        name="whole",
    )
    assert asof.earliest_valid_as_of(root2) == whole


def test_t2_issues_anchor_after_the_window_moves_as_of_to_issues_minus_14_days(
    tmp_path: Path,
) -> None:
    asof = _asof()
    root = _root(
        tmp_path,
        {
            "acme__alpha": {"pr": A, "releases": A + DAY, "issues": A + 15 * DAY},
            "acme__beta": {"pr": A - DAY, "releases": A + DAY, "issues": A + 3 * DAY},
        },
    )
    as_of = asof.earliest_valid_as_of(root)
    assert as_of == datetime(2026, 10, 25, 18, 10, 0, tzinfo=UTC)  # (A + 15 d) - 14 d, rounded up
    results = asof.check_as_of(root, as_of)
    assert not [r for r in results if r.status == "FAIL"]
    assert _by_guard(results)["issues_counts"].status == "PASS"


def test_t3_empty_interval_gives_none_and_the_cli_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    asof = _asof()
    root = _root(
        tmp_path,
        {
            "acme__alpha": {"pr": A, "releases": A + DAY // 2, "issues": A + 30 * DAY},
            "acme__beta": {"pr": A - DAY, "releases": A + DAY // 2, "issues": A + 30 * DAY},
        },
    )
    assert asof.earliest_valid_as_of(root) is None
    assert main(["as-of", "--data-dir", str(root)]) == 1
    assert "no valid as_of" in capsys.readouterr().out


def test_missing_this_cycle_layer_gives_none_and_the_check_fails(tmp_path: Path) -> None:
    """A pull taken before the commits-and-issues step finished: no this-cycle issues_counts file, so no as_of."""
    asof = _asof()
    root = _root(
        tmp_path,
        {
            "acme__alpha": {"pr": A, "releases": A + 2 * DAY, "issues": A - 14 * DAY},
            "acme__beta": {"pr": A - DAY, "releases": A + 2 * DAY, "issues": A - 13 * DAY},
        },
    )
    assert asof.earliest_valid_as_of(root) is None
    results = _by_guard(asof.check_as_of(root, A_UP))
    assert results["issues_counts"].status == "FAIL"
    assert results["releases"].status == "PASS"


def test_t4_check_one_minute_before_the_newest_anchor_fails_the_cut_guard(tmp_path: Path) -> None:
    asof = _asof()
    root = _standard(tmp_path)
    as_of = asof.earliest_valid_as_of(root)
    assert _by_guard(asof.check_as_of(root, as_of))["cut"].status == "PASS"
    cut = _by_guard(asof.check_as_of(root, as_of - timedelta(minutes=1)))["cut"]
    assert cut.status == "FAIL"
    assert cut.caps == (None,)  # an uncut record that is cut has no cap: the export refuses it
    assert any("acme/alpha" in item for item in cut.items)


def test_t5_one_truncated_cap_passes_and_two_caps_fail(tmp_path: Path) -> None:
    asof = _asof()
    cap_a = {
        "pr": A - 3 * DAY,
        "truncated": True,
        "cap": 500,
        "oldest": A - 20 * DAY,
        "releases": A + 2 * DAY,
        "issues": A + 4 * DAY,
    }
    cap_b = {
        "pr": A - 4 * DAY,
        "truncated": True,
        "cap": 2000,
        "oldest": A - 30 * DAY,
        "releases": A + 2 * DAY,
        "issues": A + 4 * DAY,
    }
    one = _standard(tmp_path, **{"acme__capa": cap_a})
    cut = _by_guard(asof.check_as_of(one, asof.earliest_valid_as_of(one)))["cut"]
    assert (cut.status, cut.caps) == ("PASS", (500,))
    two = _root(
        tmp_path,
        {
            "acme__alpha": {"pr": A, "releases": A + 2 * DAY, "issues": A + 4 * DAY},
            "acme__capa": cap_a,
            "acme__capb": cap_b,
        },
        name="two",
    )
    cut = _by_guard(asof.check_as_of(two, asof.earliest_valid_as_of(two)))["cut"]
    assert (cut.status, cut.caps) == ("FAIL", (500, 2000))


@pytest.mark.parametrize(
    "offset, expected",
    [
        pytest.param(timedelta(days=14), "PASS", id="this-cycle-exactly-14d-old"),
        pytest.param(timedelta(days=14, seconds=1), "FAIL", id="this-cycle-14d-and-1s-old"),
        pytest.param(timedelta(days=-14), "PASS", id="this-cycle-exactly-14d-newer"),
        pytest.param(timedelta(days=-14, seconds=-1), "FAIL", id="this-cycle-14d-and-1s-newer"),
    ],
)
def test_t6_this_cycle_releases_use_the_engines_two_sided_greater_than(
    tmp_path: Path, offset: timedelta, expected: str
) -> None:
    asof = _asof()
    root = _root(
        tmp_path,
        {
            "acme__new": {"pr": A, "releases": A + DAY, "issues": A + 2 * DAY},
            "acme__old": {"pr": A - 3 * DAY, "releases": A - 20 * DAY, "issues": A + 2 * DAY},
        },
    )
    results = _by_guard(asof.check_as_of(root, A + DAY + offset))
    assert results["releases"].status == expected


def test_t6_older_cycle_stale_files_warn_and_never_fail(tmp_path: Path) -> None:
    asof = _asof()
    root = _root(
        tmp_path,
        {
            "acme__new": {"pr": A, "releases": A + DAY, "issues": A + 2 * DAY},
            "acme__old": {"pr": A - 3 * DAY, "releases": A - 20 * DAY, "issues": A + 2 * DAY},
        },
    )
    results = _by_guard(asof.check_as_of(root, asof.earliest_valid_as_of(root)))
    assert results["releases"].status == "PASS"
    fallback = results["releases fallback"]
    assert fallback.status == "WARN" and fallback.counts["stale"] == 1
    assert fallback.items and fallback.items[0].startswith("acme/old ")
    assert not [r for r in results.values() if r.status == "FAIL"]


def test_t7_a_history_band_anchor_that_differs_from_its_pr_window_anchor_is_counted(
    tmp_path: Path,
) -> None:
    asof = _asof()
    root = _root(
        tmp_path,
        {
            "acme__alpha": {
                "pr": A,
                "releases": A + DAY,
                "issues": A + 2 * DAY,
                "history": A + 6 * DAY,
                "band": A,
            },
            "acme__beta": {
                "pr": A - DAY,
                "releases": A + DAY,
                "issues": A + 2 * DAY,
                "history": A + 7 * DAY,
                "band": A,
            },
            "acme__gamma": {
                "pr": A - 2 * DAY,
                "releases": A + DAY,
                "issues": A + 2 * DAY,
                "history": A + 5 * DAY,
            },
        },
    )
    history = _by_guard(asof.check_as_of(root, asof.earliest_valid_as_of(root)))["author_history"]
    assert history.status == "INFO"
    assert history.counts == {"files": 3, "with_band_anchor": 2, "band_anchor_differs": 1}
    assert (A + 5 * DAY).isoformat() in history.summary and (
        A + 7 * DAY
    ).isoformat() in history.summary


def test_t8_the_check_calls_stale_exactly_the_repos_derive_marks_collector_failure(
    tmp_path: Path,
) -> None:
    """Agreement with derive: at the computed as_of, ``openssf_signals`` is ``collector_failure`` exactly where
    the check lists a stale releases or issues_counts file."""
    asof = _asof()
    derive = _derive_module()
    root = _root(
        tmp_path,
        {
            "acme__fresh": {"pr": A, "releases": A + DAY, "issues": A + 2 * DAY},
            "acme__oldrel": {"pr": A - DAY, "releases": A - 30 * DAY, "issues": A + 2 * DAY},
            "acme__oldiss": {"pr": A - 2 * DAY, "releases": A + DAY, "issues": A - 30 * DAY},
        },
    )
    as_of = asof.earliest_valid_as_of(root)
    results = asof.check_as_of(root, as_of)
    assert not [r for r in results if r.status == "FAIL"]
    stale = {
        item.split()[0].replace("/", "__")
        for r in results
        if r.guard.endswith(" fallback")
        for item in r.items
    }
    failed = set()
    for key in ("acme__fresh", "acme__oldrel", "acme__oldiss"):
        owner, _, name = key.partition("__")
        bundle = derive.derive_repo(
            owner, name, derive.Ecosystem.PYPI, root, fetch=False, now=as_of
        )
        if bundle["confidence"].get("openssf_signals") == "collector_failure":
            failed.add(key)
    assert stale == {"acme__oldrel", "acme__oldiss"}
    assert failed == stale


def test_t9_a_non_candidate_file_anchored_after_the_newest_one_leaves_as_of_at_it(
    tmp_path: Path,
) -> None:
    asof = _asof()
    repos: dict[str, Mapping[str, Any]] = {
        "acme__alpha": {"pr": A, "releases": A + 2 * DAY, "issues": A + 4 * DAY},
        "acme__beta": {"pr": A - 2 * DAY, "releases": A + 2 * DAY, "issues": A + 4 * DAY},
        "acme__ghost": {"pr": A + 3 * DAY, "releases": A + 3 * DAY, "issues": A + 3 * DAY},
    }
    root = _root(tmp_path, repos, candidates=["acme__alpha", "acme__beta"])
    assert asof.earliest_valid_as_of(root) == A_UP


def test_t10_cli_print_and_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = _standard(
        tmp_path,
        **{"acme__older": {"pr": A - 4 * DAY, "releases": A - 20 * DAY, "issues": A + 4 * DAY}},
    )
    capsys.readouterr()
    assert main(["as-of", "--data-dir", str(good), "--print"]) == 0
    assert capsys.readouterr().out == "2026-10-24T18:10:00+00:00\n"

    assert (
        main(["as-of", "--data-dir", str(good)]) == 0
    )  # a WARN for an older-cycle file does not fail
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "as-of 2026-10-24T18:10:00+00:00"
    assert "PASS cut" in out and "PASS releases" in out and "PASS issues_counts" in out
    assert "WARN releases fallback" in out and "INFO author_history" in out and "FAIL" not in out

    early = "2026-10-24T18:09:00+00:00"
    assert main(["as-of", "--data-dir", str(good), "--as-of", early]) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"as-of {early} (given)"
    assert "FAIL cut" in out and "earliest valid as-of 2026-10-24T18:10:00+00:00" in out

    assert main(["as-of", "--data-dir", str(good), "--as-of", early, "--print"]) == 1
    assert capsys.readouterr().out == ""
    assert (
        main(["as-of", "--data-dir", str(good), "--as-of", "2026-10-24T18:10:00Z", "--print"]) == 0
    )
    assert capsys.readouterr().out == "2026-10-24T18:10:00+00:00\n"

    assert main(["as-of", "--data-dir", str(tmp_path / "missing")]) == 2
    assert main(["as-of", "--data-dir", str(good), "--as-of", "yesterday"]) == 2
    nolist = tmp_path / "nolist"
    (nolist / "raw").mkdir(parents=True)
    assert main(["as-of", "--data-dir", str(nolist)]) == 2
    capsys.readouterr()
    infeasible = _root(
        tmp_path,
        {"acme__alpha": {"pr": A, "releases": A + DAY // 2, "issues": A + 30 * DAY}},
        name="infeasible",
    )
    assert main(["as-of", "--data-dir", str(infeasible), "--print"]) == 1
    assert capsys.readouterr().out == ""


def test_an_unreadable_envelope_is_warned_about_and_does_not_move_as_of(tmp_path: Path) -> None:
    asof = _asof()
    root = _standard(tmp_path)
    broken = root / "raw" / "pr_window" / "acme__zeta.json"
    broken.write_text("{not json", encoding="utf-8")
    lines = (root / "raw" / "candidates.jsonl").read_text(encoding="utf-8")
    (root / "raw" / "candidates.jsonl").write_text(
        lines + json.dumps({"owner": "acme", "name": "zeta", "ecosystem": "pypi"}) + "\n",
        encoding="utf-8",
    )
    assert asof.earliest_valid_as_of(root) == A_UP
    unreadable = _by_guard(asof.check_as_of(root, A_UP))["unreadable"]
    assert unreadable.status == "WARN" and unreadable.counts == {"pr_window": 1}


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(
            {
                "window_anchor": A.isoformat(),
                "window_days": 180,
                "truncated": False,
                "prs": [{"x": 1}],
            },
            id="uncut",
        ),
        pytest.param(
            {
                "window_anchor": A.isoformat(),
                "window_days": 180,
                "truncated": True,
                "cap": 500,
                "prs": [
                    {"createdAt": (A - 20 * DAY).isoformat(), "big": "x" * 50},
                    {"createdAt": (A - 3 * DAY).isoformat()},
                    {"number": 3},
                ],
            },
            id="cut-at-cap",
        ),
        pytest.param(
            {"window_anchor": A.isoformat(), "truncated": None, "prs": []}, id="cut-not-stated"
        ),
        pytest.param(
            {"window_anchor": "garbage", "window_days": 180, "truncated": False, "prs": []},
            id="bad-anchor",
        ),
        pytest.param(
            {"window_anchor": A.isoformat(), "window_days": 180, "truncated": False},
            id="no-prs-list",
        ),
        pytest.param(
            {"truncated": False, "window_days": 180, "prs": []}, id="no-anchor-uses-fetched-at"
        ),
    ],
)
def test_the_reduced_record_covers_exactly_as_the_full_record_does(data: dict[str, Any]) -> None:
    """The scan keeps a reduced copy of each pr_window record (eleven gigabytes do not fit in memory); the
    engine's coverage rule must answer the same for it as for the whole record."""
    asof = _asof()
    document = {"source": "test", "fetched_at": (A - DAY).isoformat(), "data": data}
    reduced = asof._reduced_window_document(document)
    for shift in (timedelta(days=-30), timedelta(0), timedelta(minutes=1), timedelta(days=40)):
        assert pr_window_coverage_in(reduced, A + shift) == pr_window_coverage_in(
            document, A + shift
        )
