"""Covers ``cvi.core.commit_domains`` -- the commit-actor typing/bot/org-key
primitives reachable from ``cvi.derive.bundle``/``cvi.core.pipeline`` -- plus
the two pure facts D-TIE-1 item 7 and the ``ef_domain_mismatch`` detector
need: :func:`last_non_bot_commit_date` and :func:`top_domain_share`. Also
covers the ``last_non_bot_commit_at`` bundle-level sidecar accessor/setter
pair that lives here (not in ``cvi.derive.bundle``) because
``cvi.core.pipeline`` needs to read it back and may import only ``cvi.core``
plus stdlib.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cvi.core.commit_domains import (
    CommitActor,
    CommitRecord,
    LastNonBotCommitMeasured,
    TopDomainShare,
    is_bot,
    last_non_bot_commit_at,
    last_non_bot_commit_date,
    org_key,
    set_last_non_bot_commit_at,
    top_domain_share,
)
from cvi.core.models import FeatureBundle, PRWindow, RepoIdentity, Unmeasured


def _actor(login: str = "alice", email: str = "alice@corp.com", actor_type: str = "User") -> CommitActor:
    return CommitActor(login=login, email=email, actor_type=actor_type)


def _record(login: str = "alice", email: str = "alice@corp.com", actor_type: str = "User",
            date: "datetime | None" = None) -> CommitRecord:
    return CommitRecord(actor=_actor(login, email, actor_type), date=date)


# --------------------------------------------------------------------------- #
# last_non_bot_commit_date (records-level, pure)                            #
# --------------------------------------------------------------------------- #

def test_last_non_bot_commit_date_is_the_max_non_bot_dated_commit() -> None:
    records = [
        _record("alice", date=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        _record("bob", date=datetime(2026, 6, 1, tzinfo=timezone.utc)),
        _record("carol", date=datetime(2026, 3, 1, tzinfo=timezone.utc)),
    ]
    assert last_non_bot_commit_date(records) == datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_last_non_bot_commit_date_excludes_bot_commits() -> None:
    """The most recent commit is a bot's; the answer must be the most recent
    *human* one, not the bot's later date (bots counted -> max->min-date-style
    mutant target)."""
    records = [
        _record("alice", date=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        _record("dependabot[bot]", actor_type="Bot", date=datetime(2026, 9, 1, tzinfo=timezone.utc)),
    ]
    assert last_non_bot_commit_date(records) == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_last_non_bot_commit_date_bot_by_known_login_exact_match() -> None:
    records = [_record("dependabot", actor_type="", date=datetime(2026, 9, 1, tzinfo=timezone.utc))]
    assert last_non_bot_commit_date(records) is None


def test_last_non_bot_commit_date_no_login_non_noreply_email_counts_as_human() -> None:
    """A commit with no linked GitHub account (``login == ""``) but a real,
    non-noreply email is not a bot by any of D-IN-1's three conditions --
    it counts as human under the documented bot rule."""
    records = [_record(login="", email="ghost@example.com", actor_type="",
                        date=datetime(2026, 5, 1, tzinfo=timezone.utc))]
    assert last_non_bot_commit_date(records) == datetime(2026, 5, 1, tzinfo=timezone.utc)


def test_last_non_bot_commit_date_none_when_no_non_bot_commit() -> None:
    assert last_non_bot_commit_date([]) is None
    records = [_record("dependabot[bot]", actor_type="Bot", date=datetime(2026, 1, 1, tzinfo=timezone.utc))]
    assert last_non_bot_commit_date(records) is None


def test_last_non_bot_commit_date_none_when_non_bot_commits_are_undated() -> None:
    records = [_record("alice", date=None)]
    assert last_non_bot_commit_date(records) is None


def test_last_non_bot_commit_date_mixed_timezones_same_instant() -> None:
    """An offset-bearing date and a UTC (``Z``-equivalent) date for the same
    instant must compare equal and pick either interchangeably -- the two
    ``CommitRecord`` lists below differ only in which record carries the
    later-looking but equal instant, and both must return that instant."""
    same_instant_ist = datetime(2026, 6, 1, 5, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    same_instant_utc = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    assert same_instant_ist == same_instant_utc  # sanity: truly the same instant

    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result_a = last_non_bot_commit_date([_record("alice", date=earlier), _record("bob", date=same_instant_ist)])
    result_b = last_non_bot_commit_date([_record("alice", date=earlier), _record("bob", date=same_instant_utc)])
    assert result_a == result_b == same_instant_utc


# --------------------------------------------------------------------------- #
# top_domain_share (records-level, pure)                                    #
# --------------------------------------------------------------------------- #

def test_top_domain_share_corporate_domain_grouped() -> None:
    records = [
        _record("alice", "alice@corp.com"),
        _record("bob", "bob@corp.com"),
        _record("carol", "carol@other.com"),
    ]
    result = top_domain_share(records)
    assert result == TopDomainShare(share=2 / 3, n=3, top_domain="corp.com")


def test_top_domain_share_noreply_addresses_are_distinct_individuals() -> None:
    """Two different GitHub noreply authors must NOT merge into one
    ``users.noreply.github.com`` org (D-PARAM 3.2's own rule, reused
    exactly) -- the "noreply domains merged" mutant target."""
    records = [
        _record("alice", "1+alice@users.noreply.github.com"),
        _record("bob", "2+bob@users.noreply.github.com"),
        _record("carol", "3+carol@users.noreply.github.com"),
    ]
    result = top_domain_share(records)
    # Three distinct individuals, no single one reaching more than 1/3.
    assert result.n == 3
    assert result.share == pytest.approx(1 / 3)
    assert result.top_domain in {"alice", "bob", "carol"}


def test_top_domain_share_personal_domain_addresses_are_distinct_individuals() -> None:
    records = [_record("alice", "alice@gmail.com"), _record("bob", "bob@gmail.com")]
    result = top_domain_share(records)
    assert result.n == 2
    assert result.share == pytest.approx(0.5)
    assert result.top_domain in {"alice", "bob"}


def test_top_domain_share_excludes_bot_commits_from_numerator_and_denominator() -> None:
    """Bots counted mutant target: if bot commits were folded into ``n`` or
    a domain's count, a bot-dominated repo would misreport ``n``/``share``."""
    records = [
        _record("alice", "alice@corp.com"),
        _record("dependabot[bot]", "bot@corp.com", actor_type="Bot"),
        _record("dependabot[bot]", "bot@corp.com", actor_type="Bot"),
    ]
    result = top_domain_share(records)
    assert result == TopDomainShare(share=1.0, n=1, top_domain="corp.com")


def test_top_domain_share_computed_over_non_bot_only_not_all_commits() -> None:
    """"share computed over all commits instead of non-bot" mutant target:
    a repo with 1 human commit and 99 bot commits must report share=1.0 /
    n=1, never share=1/100."""
    records = [_record("alice", "alice@corp.com")] + [
        _record("dependabot[bot]", "bot@corp.com", actor_type="Bot") for _ in range(99)
    ]
    result = top_domain_share(records)
    assert result.n == 1
    assert result.share == 1.0


def test_top_domain_share_tie_breaks_alphabetically_first() -> None:
    """Pinned rule: a tie for the top domain resolves to the
    alphabetically-first org key -- the same tie rule
    ``cvi.collect.commits.elephant_factor`` already uses."""
    records = [
        _record("alice", "alice@zzz.com"),
        _record("bob", "bob@aaa.com"),
    ]
    result = top_domain_share(records)
    assert result.top_domain == "aaa.com"
    assert result.share == pytest.approx(0.5)


def test_top_domain_share_no_records_is_zero_share_zero_n() -> None:
    assert top_domain_share([]) == TopDomainShare(share=0.0, n=0, top_domain="")


def test_top_domain_share_no_attributable_org_despite_commits() -> None:
    """Every non-bot commit lacks a resolvable email (``org_key`` returns
    ``None`` for all of them): ``n`` is still the non-bot count, but
    ``top_domain`` is "" and ``share`` is 0.0 -- distinct from the
    ``n == 0`` case (``cvi.derive.bundle`` maps the two to different
    ``Unmeasured`` reasons)."""
    records = [_record(login="alice", email="", actor_type="User")]
    result = top_domain_share(records)
    assert result == TopDomainShare(share=0.0, n=1, top_domain="")


# --------------------------------------------------------------------------- #
# is_bot / org_key sanity (also covered via cvi.collect.commits, restated   #
# here once as an anti-vacuity check)                                       #
# --------------------------------------------------------------------------- #

def test_is_bot_and_org_key_still_behave_after_relocation() -> None:
    assert is_bot(_actor("dependabot[bot]", actor_type="")) is True
    assert is_bot(_actor("alice", actor_type="User")) is False
    assert org_key(_actor("alice", "alice@corp.com")) == "corp.com"
    assert org_key(_actor("alice", "")) is None


def test_org_key_folds_email_case_for_corporate_personal_and_noreply_domains() -> None:
    """Pins the ``.lower()`` case fold ``org_key`` applies to the recorded
    email before matching ``NOREPLY_DOMAIN``/``PERSONAL_DOMAINS`` (both
    stored lowercase). Without it, an upper-case domain never matches
    either table and falls through to the generic "return the literal
    domain" branch: 40 distinct authors on
    ``...@USERS.NOREPLY.GITHUB.COM`` would collapse into one org named
    ``"USERS.NOREPLY.GITHUB.COM"`` instead of staying 40 distinct
    individuals, moving ``top_email_domain_share`` from 0.025 to 1.000 and
    firing ``ef_domain_mismatch`` on a repo with no dominant contributor at
    all."""
    # Corporate: the returned domain itself must be lower-cased, not the
    # verbatim recorded case.
    assert org_key(_actor("Alice", "Alice@CORP.COM")) == "corp.com"
    # Personal: an upper-case gmail.com must still match PERSONAL_DOMAINS
    # (stored lowercase) and resolve to the login, not fall through to the
    # literal upper-case domain string.
    assert org_key(_actor("Bob", "Bob@GMAIL.COM")) == "Bob"
    # Noreply: an upper-case users.noreply.github.com must still match
    # NOREPLY_DOMAIN and resolve to the login, never the literal domain.
    assert org_key(_actor("Carol", "1+Carol@USERS.NOREPLY.GITHUB.COM")) == "Carol"


# --------------------------------------------------------------------------- #
# last_non_bot_commit_at bundle-level sidecar (accessor/setter)             #
# --------------------------------------------------------------------------- #

def _bundle() -> FeatureBundle:
    repo = RepoIdentity(
        owner="acme", name="widget", ecosystem="pypi", archived=False,
        mirror_url=None, fork=False, license_spdx=None, default_branch=None,
    )
    from cvi.core.models import Cohorts
    return FeatureBundle(
        schema_version=2, repo=repo, inputs={}, pr_window=PRWindow(None, None, False, ()),
        cohorts=Cohorts((), ()), provenance_hash="0" * 64,
    )


def test_last_non_bot_commit_at_default_is_unmeasured_collector_failure() -> None:
    bundle = _bundle()
    fact = last_non_bot_commit_at(bundle)
    assert isinstance(fact, Unmeasured)
    assert fact.reason == "collector_failure"


def test_set_and_read_last_non_bot_commit_at() -> None:
    bundle = _bundle()
    fact = LastNonBotCommitMeasured(
        value="2026-06-01T00:00:00Z", method="measured:commits_365d",
        source="raw/commits_365d/acme__widget.json", source_hash="a" * 64, fetched_at="2026-09-01T00:00:00Z",
    )
    set_last_non_bot_commit_at(bundle, fact)
    assert last_non_bot_commit_at(bundle) is fact


def test_last_non_bot_commit_measured_absence_value_is_none_not_unmeasured() -> None:
    """D-MISS-1 F2 ruling: a measured absence is ``LastNonBotCommitMeasured
    (value=None, ...)``, never ``Unmeasured`` -- pinning the distinction
    ("absence and failure swapped" mutant target)."""
    fact = LastNonBotCommitMeasured(
        value=None, method="measured:no_non_bot_commit_365d",
        source="raw/commits_365d/acme__widget.json", source_hash="a" * 64, fetched_at="2026-09-01T00:00:00Z",
    )
    assert isinstance(fact, LastNonBotCommitMeasured)
    assert fact.value is None
    assert fact.method == "measured:no_non_bot_commit_365d"
