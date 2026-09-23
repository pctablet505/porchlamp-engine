"""Acceptance tests for ``porchlamp.derive.bundle``'s two commit-derived bundle-level
sidecar facts -- ``last_non_bot_commit_at`` (D-TIE-1 item 7,
``docs/v2/02-RANKING-DESIGN.md`` section 10) and ``top_email_domain_share``
(the ``ef_domain_mismatch`` detector's input, ``docs/v2/
03-ANTI-MANIPULATION.md`` section 6). Neither is one of
``constants.PARAM_INPUT_IDS``'s 18 scored parameters, so both live as
bundle-instance sidecar attributes (``porchlamp.derive.bundle.
last_non_bot_commit_at``/``top_email_domain_share`` accessors) rather than
``FeatureBundle.inputs`` keys.

``_TOP_DOMAIN_SHARE_MIN_N`` is 30 (``constants.PARAM_MIN_N
["elephant_factor"]``, reused, not a new tunable), so every test that wants
a *Measured* ``top_email_domain_share`` needs at least 30 non-bot commits;
:func:`_rows` generates them.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from _fixtures import KEY, base_document, expected_hash, load, prov, write_envelope

from porchlamp.core import constants
from porchlamp.core.models import Unmeasured
from porchlamp.derive.bundle import (
    LastNonBotCommitMeasured,
    TopEmailDomainShareMeasured,
    last_non_bot_commit_at,
    load_bundle,
    top_email_domain_share,
)

MIN_N = constants.PARAM_MIN_N["elephant_factor"]
assert MIN_N == 30  # anchor: the rest of this file's row counts assume this


def _row(login: str, email: str, date: str, actor_type: str = "User") -> dict[str, Any]:
    return {"author_login": login, "author_email": email, "author_type": actor_type,
            "date": date, "signed_off": False}


def _rows(n: int, domain: str = "corp.com", start_day: int = 1) -> list[dict[str, Any]]:
    """``n`` distinct-author commits on ``domain``, one per day starting
    ``start_day`` of January 2026 (never colliding with a caller's own
    hand-placed rows elsewhere in the same test)."""
    return [
        _row(f"user{i}", f"user{i}@{domain}", f"2026-01-{start_day + i:02d}T00:00:00Z")
        for i in range(n)
    ]


def _doc(*, commits_365d: list[dict[str, Any]] | None = None,
         commits_180d: list[dict[str, Any]] | None = None,
         commits_365d_prov: bool = True, commits_180d_prov: bool = True) -> dict[str, Any]:
    provenance: dict[str, Any] = {}
    if commits_365d is not None and commits_365d_prov:
        provenance["commits_365d"] = prov("commits_365d")
    if commits_180d is not None and commits_180d_prov:
        provenance["commits_180d"] = prov("commits_180d")
    return base_document(provenance=provenance)


def _write(root: Path, collector: str, data: list[dict[str, Any]]) -> None:
    write_envelope(root / "raw", collector, KEY, data)


# --------------------------------------------------------------------------- #
# last_non_bot_commit_at                                                    #
# --------------------------------------------------------------------------- #

def test_last_non_bot_commit_at_measured_from_commits_365d(tmp_path: Path) -> None:
    rows = _rows(30) + [_row("latest", "latest@corp.com", "2026-06-01T00:00:00Z")]
    _write(tmp_path, "commits_365d", rows)
    bundle = load(tmp_path, _doc(commits_365d=rows))

    fact = last_non_bot_commit_at(bundle)
    assert isinstance(fact, LastNonBotCommitMeasured)
    assert fact.value == "2026-06-01T00:00:00Z"


def _collected(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """``rows`` as ``porchlamp.collect.commits.collect_commits_365d`` itself
    records them (its window dict) -- the producer builds the envelope, so a
    change to what it records reaches this reader's tests too."""
    from porchlamp.collect.commits import collect_commits_365d
    raw = [{"commit": {"author": {"email": r["author_email"], "date": r["date"]}, "message": ""},
            "author": {"login": r["author_login"], "type": r["author_type"]}} for r in rows]
    return collect_commits_365d(lambda page, since: raw if page == 1 else [])


def test_both_facts_read_the_collectors_window_dict(tmp_path: Path) -> None:
    """``collect_commits_365d`` records ``{"count", "cap", "truncated",
    "rows"}``, not a row list; both facts read that as "no list-shaped
    data" -> collector_failure for every repo whose commits had been
    re-collected."""
    rows = _rows(30) + [_row("latest", "latest@corp.com", "2026-06-01T00:00:00Z")]
    window = _collected(rows)
    assert isinstance(window, dict)
    write_envelope(tmp_path / "raw", "commits_365d", KEY, window)
    bundle = load(tmp_path, _doc(commits_365d=rows))

    fact = last_non_bot_commit_at(bundle)
    assert isinstance(fact, LastNonBotCommitMeasured)
    assert fact.value == "2026-06-01T00:00:00Z"
    share = top_email_domain_share(bundle)
    assert isinstance(share, TopEmailDomainShareMeasured)
    assert share.value == 1.0 and share.n == 31  # every row is corp.com
    assert share.top_domain == "corp.com"
    # the hash stays over what the envelope RECORDED (the window dict), not the unwrapped rows:
    assert share.source_hash == expected_hash(window)


def test_last_non_bot_commit_at_resolves_via_raw_root_with_no_provenance_entry(tmp_path: Path) -> None:
    """The real sample-200 corpus's v1 documents key ``provenance`` by meta
    field name (``elephant_factor``, ``active_nonbot_commits_180d``, ...),
    never by the literal collector name ``"commits_365d"``/
    ``"commits_180d"`` -- so a resolver that only looked those two strings
    up in ``provenance`` would never find anything there (empirically
    confirmed against the corpus before writing the fix). The envelope
    must resolve from ``raw_root/commits_365d/<key>.json`` directly,
    with no provenance entry naming it at all."""
    rows = _rows(30) + [_row("latest", "latest@corp.com", "2026-06-01T00:00:00Z")]
    _write(tmp_path, "commits_365d", rows)
    doc = _doc(commits_365d=rows, commits_365d_prov=False)
    assert "commits_365d" not in doc["provenance"]  # sanity: truly no provenance entry
    bundle = load(tmp_path, doc)

    fact = last_non_bot_commit_at(bundle)
    assert isinstance(fact, LastNonBotCommitMeasured)
    assert fact.value == "2026-06-01T00:00:00Z"
    assert fact.source_hash == expected_hash(rows)
    assert fact.method == "measured:commits_365d"
    assert fact.source_hash == expected_hash(rows)
    assert fact.fetched_at


def test_last_non_bot_commit_at_falls_back_to_commits_180d_when_365d_absent(tmp_path: Path) -> None:
    rows = _rows(30) + [_row("latest", "latest@corp.com", "2026-06-01T00:00:00Z")]
    _write(tmp_path, "commits_180d", rows)
    bundle = load(tmp_path, _doc(commits_180d=rows))

    fact = last_non_bot_commit_at(bundle)
    assert isinstance(fact, LastNonBotCommitMeasured)
    assert fact.value == "2026-06-01T00:00:00Z"
    assert fact.method == "measured:commits_180d_fallback"


def test_last_non_bot_commit_at_365d_present_but_empty_is_measured_absence_not_fallback(tmp_path: Path) -> None:
    """An envelope that resolves with ``data: []`` is a real, measured "0
    commits", not a reason to fall back to commits_180d -- even when a
    perfectly good commits_180d envelope also exists."""
    fallback_rows = _rows(30)
    _write(tmp_path, "commits_365d", [])
    _write(tmp_path, "commits_180d", fallback_rows)
    bundle = load(tmp_path, _doc(commits_365d=[], commits_180d=fallback_rows))

    fact = last_non_bot_commit_at(bundle)
    assert isinstance(fact, LastNonBotCommitMeasured)
    assert fact.value is None
    assert fact.method == "measured:no_non_bot_commit_365d"


def test_last_non_bot_commit_at_no_non_bot_commit_is_measured_absence(tmp_path: Path) -> None:
    """D-MISS-1 F2 ruling: every commit in the window is a bot's -> a
    measured absence (``value=None``, a real method), never ``Unmeasured``
    ("absence and failure swapped" mutant target)."""
    rows = [_row("dependabot[bot]", "bot@corp.com", "2026-06-01T00:00:00Z", actor_type="Bot") for _ in range(5)]
    _write(tmp_path, "commits_365d", rows)
    bundle = load(tmp_path, _doc(commits_365d=rows))

    fact = last_non_bot_commit_at(bundle)
    assert isinstance(fact, LastNonBotCommitMeasured)
    assert fact.value is None
    assert fact.method == "measured:no_non_bot_commit_365d"


def test_last_non_bot_commit_at_missing_envelope_is_unmeasured_collector_failure(tmp_path: Path) -> None:
    bundle = load(tmp_path, _doc())  # neither commits_365d nor commits_180d provenance at all

    fact = last_non_bot_commit_at(bundle)
    assert fact == Unmeasured(reason="collector_failure",
                               detail="no commits_365d or commits_180d envelope with a resolvable path and fetched_at")


def test_last_non_bot_commit_at_mixed_timezones_same_instant(tmp_path: Path) -> None:
    """An offset-bearing date and a ``Z`` date recording the same instant
    must serialize to the exact same ``last_non_bot_commit_at`` string."""
    rows_offset = _rows(30) + [_row("latest", "latest@corp.com", "2026-06-01T05:30:00+05:30")]
    rows_z = _rows(30) + [_row("latest", "latest@corp.com", "2026-06-01T00:00:00Z")]
    _write(tmp_path, "commits_365d", rows_offset)
    bundle_offset = load(tmp_path, _doc(commits_365d=rows_offset), key="repo-offset")

    tmp_path_z = tmp_path / "z"
    tmp_path_z.mkdir()
    _write(tmp_path_z, "commits_365d", rows_z)
    bundle_z = load(tmp_path_z, _doc(commits_365d=rows_z), key="repo-z")

    fact_offset = last_non_bot_commit_at(bundle_offset)
    fact_z = last_non_bot_commit_at(bundle_z)
    assert isinstance(fact_offset, LastNonBotCommitMeasured) and isinstance(fact_z, LastNonBotCommitMeasured)
    assert fact_offset.value == fact_z.value == "2026-06-01T00:00:00Z"


def test_last_non_bot_commit_at_excludes_bot_commits(tmp_path: Path) -> None:
    rows = _rows(30) + [_row("dependabot[bot]", "bot@corp.com", "2026-09-01T00:00:00Z", actor_type="Bot")]
    _write(tmp_path, "commits_365d", rows)
    bundle = load(tmp_path, _doc(commits_365d=rows))

    fact = last_non_bot_commit_at(bundle)
    assert isinstance(fact, LastNonBotCommitMeasured)
    # The max human date is the last _rows() row (2026-01-30), not the bot's 09-01.
    assert fact.value == "2026-01-30T00:00:00Z"


def test_last_non_bot_commit_at_no_login_non_noreply_email_counts_as_human(tmp_path: Path) -> None:
    rows = _rows(29) + [_row("", "ghost@example.com", "2026-08-01T00:00:00Z", actor_type="")]
    _write(tmp_path, "commits_365d", rows)
    bundle = load(tmp_path, _doc(commits_365d=rows))

    fact = last_non_bot_commit_at(bundle)
    assert isinstance(fact, LastNonBotCommitMeasured)
    assert fact.value == "2026-08-01T00:00:00Z"


# --------------------------------------------------------------------------- #
# top_email_domain_share                                                    #
# --------------------------------------------------------------------------- #

def test_top_email_domain_share_measured(tmp_path: Path) -> None:
    rows = _rows(20, domain="corp.com") + _rows(10, domain="other.com", start_day=21)
    _write(tmp_path, "commits_365d", rows)
    bundle = load(tmp_path, _doc(commits_365d=rows))

    fact = top_email_domain_share(bundle)
    assert isinstance(fact, TopEmailDomainShareMeasured)
    assert fact.value == pytest.approx(20 / 30)
    assert fact.top_domain == "corp.com"
    assert fact.n == 30
    assert fact.method == "measured:commits_365d"
    assert fact.source_hash == expected_hash(rows)


def test_top_email_domain_share_falls_back_to_commits_180d(tmp_path: Path) -> None:
    rows = _rows(30, domain="corp.com")
    _write(tmp_path, "commits_180d", rows)
    bundle = load(tmp_path, _doc(commits_180d=rows))

    fact = top_email_domain_share(bundle)
    assert isinstance(fact, TopEmailDomainShareMeasured)
    assert fact.method == "measured:commits_180d_fallback"


def test_top_email_domain_share_insufficient_n(tmp_path: Path) -> None:
    rows = _rows(5, domain="corp.com")
    _write(tmp_path, "commits_365d", rows)
    bundle = load(tmp_path, _doc(commits_365d=rows))

    fact = top_email_domain_share(bundle)
    assert fact == Unmeasured(reason="insufficient_n", detail=f"5 non-bot commits, minimum {MIN_N}")


def test_top_email_domain_share_no_attributable_org_is_collector_failure_not_zero_share(tmp_path: Path) -> None:
    """30 non-bot commits (clears insufficient_n) but none carry a resolvable
    email (matches the real sample-200 corpus's commits_180d shape,
    ``{"author_login", "date"}`` only): a real collector gap, not a
    falsely-reassuring "no domain dominates" 0.0 share."""
    rows = [{"author_login": f"user{i}", "date": f"2026-01-{i + 1:02d}T00:00:00Z"} for i in range(30)]
    _write(tmp_path, "commits_365d", rows)
    bundle = load(tmp_path, _doc(commits_365d=rows))

    fact = top_email_domain_share(bundle)
    assert isinstance(fact, Unmeasured)
    assert fact.reason == "collector_failure"


def test_top_email_domain_share_empty_365d_is_insufficient_n_not_fallback(tmp_path: Path) -> None:
    fallback_rows = _rows(30)
    _write(tmp_path, "commits_365d", [])
    _write(tmp_path, "commits_180d", fallback_rows)
    bundle = load(tmp_path, _doc(commits_365d=[], commits_180d=fallback_rows))

    fact = top_email_domain_share(bundle)
    assert fact == Unmeasured(reason="insufficient_n", detail=f"0 non-bot commits, minimum {MIN_N}")


def test_top_email_domain_share_missing_envelope_is_unmeasured_collector_failure(tmp_path: Path) -> None:
    bundle = load(tmp_path, _doc())

    fact = top_email_domain_share(bundle)
    assert fact == Unmeasured(reason="collector_failure",
                               detail="no commits_365d or commits_180d envelope with a resolvable path and fetched_at")


def test_top_email_domain_share_excludes_bot_commits(tmp_path: Path) -> None:
    """"share computed over all commits instead of non-bot" mutant target:
    30 human commits on one domain plus 100 bot commits on another must
    report n=30, share=1.0 -- never n=130 or a diluted share."""
    human = _rows(30, domain="corp.com")
    bots = [_row("dependabot[bot]", "bot@evil.com", f"2026-09-{i + 1:02d}T00:00:00Z", actor_type="Bot")
            for i in range(29)]
    rows = human + bots
    _write(tmp_path, "commits_365d", rows)
    bundle = load(tmp_path, _doc(commits_365d=rows))

    fact = top_email_domain_share(bundle)
    assert isinstance(fact, TopEmailDomainShareMeasured)
    assert fact.n == 30
    assert fact.value == 1.0
    assert fact.top_domain == "corp.com"


def test_top_email_domain_share_noreply_addresses_are_distinct_individuals(tmp_path: Path) -> None:
    """30 distinct per-user GitHub noreply authors, one commit each: if
    noreply addresses were (wrongly) merged into one
    ``users.noreply.github.com`` org, this would report share=1.0 for that
    literal domain string. The correct answer is 30 distinct individuals,
    each at 1/30."""
    rows = [
        _row(f"user{i}", f"{1000 + i}+user{i}@users.noreply.github.com", f"2026-01-{i + 1:02d}T00:00:00Z")
        for i in range(30)
    ]
    _write(tmp_path, "commits_365d", rows)
    bundle = load(tmp_path, _doc(commits_365d=rows))

    fact = top_email_domain_share(bundle)
    assert isinstance(fact, TopEmailDomainShareMeasured)
    assert fact.n == 30
    assert fact.value == pytest.approx(1 / 30)
    assert fact.top_domain != "users.noreply.github.com"
    assert fact.top_domain in {f"user{i}" for i in range(30)}


def test_top_email_domain_share_tie_breaks_alphabetically_first(tmp_path: Path) -> None:
    rows = _rows(15, domain="zzz.com") + _rows(15, domain="aaa.com", start_day=16)
    _write(tmp_path, "commits_365d", rows)
    bundle = load(tmp_path, _doc(commits_365d=rows))

    fact = top_email_domain_share(bundle)
    assert isinstance(fact, TopEmailDomainShareMeasured)
    assert fact.top_domain == "aaa.com"
    assert fact.value == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# Round-trip: both sidecar facts survive dump_bundle -> write_bundle ->     #
# load_bundle (validate_bundle's own docstring explains why this matters -- #
# last_non_bot_commit_at feeds tie_key, so losing it on reload would        #
# silently change records_hash between the snapshot that produced a frozen  #
# fixture's expected.json and any later reproduction of it from the         #
# committed bundle files, e.g. tests/fixtures/snapshot_50).                #
# --------------------------------------------------------------------------- #

def test_both_sidecar_facts_survive_a_dump_reload_round_trip(tmp_path: Path) -> None:
    from porchlamp.derive.bundle import write_bundle

    rows = _rows(20, domain="corp.com") + _rows(10, domain="other.com", start_day=21) + [
        _row("latest", "latest@corp.com", "2026-06-01T00:00:00Z"),
    ]
    _write(tmp_path, "commits_365d", rows)
    bundle = load(tmp_path, _doc(commits_365d=rows))
    original_last_commit = last_non_bot_commit_at(bundle)
    original_domain_share = top_email_domain_share(bundle)
    assert isinstance(original_last_commit, LastNonBotCommitMeasured)
    assert isinstance(original_domain_share, TopEmailDomainShareMeasured)

    dumped_path = write_bundle(bundle, tmp_path / "dumped.json")
    reloaded = load_bundle(dumped_path)

    assert last_non_bot_commit_at(reloaded) == original_last_commit
    assert top_email_domain_share(reloaded) == original_domain_share


def test_sidecar_facts_default_when_v2_document_has_neither_optional_key(tmp_path: Path) -> None:
    """A v2 document with no ``last_non_bot_commit_at``/
    ``top_email_domain_share`` key at all is still valid -- both facts fall back
    to their unset default, never a validation error."""
    import json as _json

    from porchlamp.derive.bundle import dump_bundle

    rows = _rows(30)
    _write(tmp_path, "commits_365d", rows)
    bundle = load(tmp_path, _doc(commits_365d=rows))
    doc = dump_bundle(bundle)
    del doc["last_non_bot_commit_at"]
    del doc["top_email_domain_share"]
    path = tmp_path / "no_sidecar.json"
    path.write_text(_json.dumps(doc), encoding="utf-8")

    reloaded = load_bundle(path)
    assert last_non_bot_commit_at(reloaded) == Unmeasured(
        reason="collector_failure",
        detail="no commits_365d or commits_180d envelope resolvable for this bundle",
    )
    assert top_email_domain_share(reloaded) == Unmeasured(
        reason="collector_failure",
        detail="no commits_365d or commits_180d envelope resolvable for this bundle",
    )
