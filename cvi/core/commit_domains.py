"""Commit-actor typing, bot classification and org-key normalisation
(spec 3.2; D-IN-1's typed-actor bot rule) — the stdlib-only home for the
pure classification/parsing logic both ``cvi.collect.commits`` (W1-5) and
``cvi.derive.bundle`` (W1-5f) need.

Relocated out of ``cvi.collect.commits`` by packet W1-5f:
``cvi/derive/bundle.py`` may import only ``cvi.core`` plus the standard
library (``tests/core/test_import_graph.py::
test_derive_bundle_imports_only_core_and_stdlib``; ``docs/v2/
04-ARCHITECTURE.md`` section 3's rule -- "``derive/`` imports ``core.
models`` only"), so :func:`last_non_bot_commit_at` and
:func:`top_domain_share` below (both new, W1-5f -- D-TIE-1 item 7 and the
``ef_domain_mismatch`` detector's ``top_email_domain_share`` input, 03
section 6) could not reuse ``cvi.collect.commits``'s bot/org-key
primitives without moving them somewhere ``cvi.derive.bundle`` can reach.
The same move packet W2-9g made for Pike criticality
(``cvi/core/openssf.py``: "``cvi/openssf.py`` delegates, the mirror is
deleted", `docs/v2/00-README.md` 2026-09-13 row) is repeated here.
``cvi.collect.commits`` re-exports every symbol this module defines
(``CommitActor``, ``CommitRecord``, the three domain tables, ``is_bot``,
``org_key``, ``author_key``, ``records_from_rows``) unchanged, so no
existing caller or test needed to change its import; this module's own
logic is copied verbatim from the landed W1-5 module, not rewritten (the
packet's own "reuse the existing normalisation, do not re-implement it"
instruction).

W1-5f additions: :func:`last_non_bot_commit_date` (D-TIE-1 item 7 -- the max
commit date among non-bot, dated commits) and :func:`top_domain_share`
(the raw ``(share, n, top_domain)`` behind ``top_email_domain_share``).
Both are pure functions over an already-collected ``Sequence[
CommitRecord]``: no window, no ``now`` parameter, no wall clock -- the
records passed in are exactly the envelope window a caller already
collected (D-TIE-1 item 7 and 03 section 6 both read the commits envelope
as-is; neither re-slices it by a fresh cutoff), matching the repo-wide
"no wall clock in derived values" rule even more strictly than
:func:`cvi.collect.commits.elephant_factor` (which does take a ``now``
to re-slice a possibly-oversized envelope to its nominal window).

W1-5f also puts the ``last_non_bot_commit_at`` *bundle-level* sidecar fact
(``LastNonBotCommitMeasured``/:func:`last_non_bot_commit_at`/
:func:`set_last_non_bot_commit_at``) here rather than in
``cvi.derive.bundle`` (where ``cvi.derive.bundle._resolve_commit_facts``
actually builds it, reading the raw commits envelope): ``cvi.core.
pipeline`` -- itself restricted to ``cvi.core`` + stdlib -- needs to read
this fact back for D-TIE-1 item 7's tie-break wiring
(``score_bundle_stage_9_tie_input``) and cannot import ``cvi.derive.
bundle`` to get it. ``top_email_domain_share``'s matching sidecar fact has
no such reader inside ``cvi.core`` (only ``cvi/verify/pipeline_hook.py``,
unrestricted, needs it) and stays defined in ``cvi.derive.bundle`` itself.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence, Union

from cvi.core.models import FeatureBundle, Unmeasured

#: GitHub private-email domain: its local part is an account id (or
#: ``<id>+<login>``), so the domain maps to the author's login, never to an
#: org of its own (spec 3.2).
NOREPLY_DOMAIN: str = "users.noreply.github.com"

#: Personal email providers: each author on one of these domains counts as
#: their own one-person org, not the provider as an org (spec 3.2, the
#: "gmail, outlook, proton, users.noreply..." family; ``github.com`` is
#: included because every ``<login>@github.com`` commit address belongs to
#: one individual). Exact domain match only (no wildcards, no substring
#: matching -- F12).
PERSONAL_DOMAINS: frozenset[str] = frozenset({
    "126.com",
    "163.com",
    "aol.com",
    "att.net",
    "attmail.com",
    "charter.net",
    "fastmail.com",
    "gmx.com",
    "gmx.de",
    "gmx.net",
    "github.com",
    "gmail.com",
    "googlemail.com",
    "hey.com",
    "hotmail.com",
    "icloud.com",
    "live.com",
    "mac.com",
    "mail.com",
    "me.com",
    "msn.com",
    "outlook.com",
    "outlook.es",
    "outlook.fr",
    "pm.me",
    "posteo.de",
    "posteo.net",
    "proton.me",
    "protonmail.com",
    "qq.com",
    "rocketmail.com",
    "sbcglobal.net",
    "sina.cn",
    "sina.com",
    "sohu.com",
    "verizon.net",
    "web.de",
    "yahoo.com",
    "yahoo.co.uk",
    "yahoo.com.au",
    "ymail.com",
    "yandex.com",
    "yandex.ru",
    "zoho.com",
})

#: D-IN-1 exact-match bot logins (compared after lowercasing the recorded
#: login; the ``[bot]`` suffix rule is separate, see :func:`is_bot`).
KNOWN_BOT_LOGINS: frozenset[str] = frozenset({
    "allcontributors",
    "cla-bot",
    "claude",
    "codecov",
    "copilot",
    "dependabot",
    "easycla",
    "github-actions",
    "google-cla",
    "greenkeeper",
    "imgbot",
    "mergify",
    "netlify",
    "pre-commit-ci",
    "renovate",
    "semantic-release",
    "snyk-bot",
    "sonarcloud",
    "stale",
    "vercel",
})


@dataclass(frozen=True)
class CommitActor:
    """One commit author with the three typed fields the collector records.

    ``login`` is the linked GitHub account ("" when the commit has no linked
    account), ``email`` is the git commit-author email, and ``actor_type``
    is the GitHub actor type ("" when unlinked).
    """

    login: str
    email: str
    actor_type: str


@dataclass(frozen=True)
class CommitRecord:
    """One window commit: a typed actor plus its git author date (UTC).

    ``date`` is None when the envelope row recorded no author date.
    """

    actor: CommitActor
    date: Optional[datetime]


def _parse_dt(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 author date; naive values are taken as UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_bot(actor: CommitActor) -> bool:
    """D-IN-1: True when the actor is a bot.

    ``actor_type == "Bot"``, a login ending in ``[bot]``, or an exact-match
    login in :data:`KNOWN_BOT_LOGINS`. No substring matching (F12).
    """
    if actor.actor_type == "Bot":
        return True
    login = actor.login.lower()
    return login.endswith("[bot]") or login in KNOWN_BOT_LOGINS


def org_key(actor: CommitActor) -> Optional[str]:
    """The org a commit author's commits count toward (spec 3.2).

    Corporate domains count as one org per domain.
    ``users.noreply.github.com`` maps to the author's login (the domain is
    never an org of its own; None when the commit has no linked login).
    Personal domains count as one org per author (the login, else the
    lowercased email address). None when no email is recorded.
    """
    email = actor.email.strip().lower()
    if "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1]
    if not domain:
        return None
    if domain == NOREPLY_DOMAIN:
        return actor.login.strip() or None
    if domain in PERSONAL_DOMAINS:
        return actor.login.strip() or email
    return domain


def author_key(actor: CommitActor) -> Optional[str]:
    """The individual an author commit belongs to: the login if recorded,
    else the lowercased email address; None when neither is recorded."""
    login = actor.login.strip()
    if login:
        return login
    email = actor.email.strip().lower()
    return email or None


def records_from_rows(rows: Sequence[Mapping[str, Any]]) -> list[CommitRecord]:
    """Parse ``commits_365d``/``commits_180d`` envelope rows into typed
    :class:`CommitRecord` (a row missing ``author_email``/``author_type``
    -- an older envelope shape some collected corpora still carry --
    yields "" for each, same as an envelope that recorded them empty;
    :func:`is_bot`'s ``[bot]``-suffix/``KNOWN_BOT_LOGINS`` rules still
    apply from ``author_login`` alone)."""
    records: list[CommitRecord] = []
    for row in rows:
        records.append(CommitRecord(
            actor=CommitActor(
                login=str(row.get("author_login") or ""),
                email=str(row.get("author_email") or ""),
                actor_type=str(row.get("author_type") or ""),
            ),
            date=_parse_dt(row.get("date")),
        ))
    return records


def last_non_bot_commit_date(records: Sequence[CommitRecord]) -> Optional[datetime]:
    """W1-5f, D-TIE-1 item 7: the max commit-author date among non-bot
    commits that carry a recorded date, or ``None`` when there is no such
    commit -- the D-MISS-1 F2 ruling's measured-absence outcome
    (``cvi.derive.bundle`` decides how that maps to a Measured/Unmeasured
    bundle-level fact; this function only ever returns a ``datetime`` or
    ``None``, never raises and never guesses a placeholder). Named
    "``_date``", distinct from :func:`last_non_bot_commit_at` below (the
    *bundle*-level accessor, D-TIE-1's own field name) -- this one takes
    the raw records, that one takes a ``FeatureBundle``.

    Comparison is exact regardless of the source records' UTC-offset
    spellings (:func:`_parse_dt` always returns a tz-aware value, so two
    equal instants recorded with different offsets compare equal); this
    function returns the raw ``datetime``, not a formatted string -- the
    caller owns the D-TIE-1 UTC-``Z`` string format."""
    dated = [record.date for record in records if record.date is not None and not is_bot(record.actor)]
    return max(dated) if dated else None


@dataclass(frozen=True)
class TopDomainShare:
    """The raw result behind ``top_email_domain_share`` (03 section 6
    ``ef_domain_mismatch``) -- never Measured/Unmeasured-framed here; that
    D-MISS-1 judgment (insufficient_n, collector_failure, ...) is
    ``cvi.derive.bundle``'s job, not this module's.

    ``n`` is the non-bot commit count (0 when there are none); ``top_
    domain`` is the winning :func:`org_key` value ("" when ``n`` is 0, or
    when no non-bot commit resolves to an org at all -- e.g. every row's
    author has no recorded email; ``cvi.derive.bundle`` distinguishes
    these two zero cases from each other); ``share`` is ``top_domain``'s
    commit count divided by ``n`` (0.0 in either zero case)."""

    share: float
    n: int
    top_domain: str


def top_domain_share(records: Sequence[CommitRecord]) -> TopDomainShare:
    """W1-5f, the ``ef_domain_mismatch`` detector's raw input: the single
    largest :func:`org_key` group's share of the non-bot commits.

    Ties broken by ascending ``org_key`` string -- the exact tie rule
    :func:`cvi.collect.commits.elephant_factor` already uses
    (``sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))``), pinned
    here again so the two facts can never disagree about which entity wins
    a tie. A commit whose author resolves to no org (:func:`org_key`
    returns ``None`` -- no ``@`` in the recorded email, or no email at
    all) still counts in ``n`` but never in any org's numerator, mirroring
    ``elephant_factor``'s own denominator rule."""
    non_bot = [record for record in records if not is_bot(record.actor)]
    n = len(non_bot)
    if n == 0:
        return TopDomainShare(share=0.0, n=0, top_domain="")
    counts: Counter[str] = Counter()
    for record in non_bot:
        org = org_key(record.actor)
        if org is not None:
            counts[org] += 1
    if not counts:
        return TopDomainShare(share=0.0, n=n, top_domain="")
    top_domain, top_count = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return TopDomainShare(share=top_count / n, n=n, top_domain=top_domain)


# --------------------------------------------------------------------------- #
# last_non_bot_commit_at: the FeatureBundle-level sidecar fact (D-TIE-1     #
# item 7). Not one of constants_v2.PARAM_INPUT_IDS's 18 scored parameters,  #
# so FeatureBundle.inputs (04 section 3: closed to exactly those 18 keys,   #
# enforced by FeatureBundle.validate()) cannot carry it -- it lives as a    #
# bundle-instance attribute instead, the same convention cvi.derive.       #
# bundle's dropped_event_count/_w2_9_dropped_event_count established for a  #
# fact cvi/core/models.py (out of this packet's ownership) has no declared  #
# field for. Defined here, not in cvi.derive.bundle (which is where it is   #
# actually built, in _resolve_commit_facts), so cvi.core.pipeline -- itself #
# restricted to cvi.core + stdlib -- can read it back for D-TIE-1 item 7's  #
# tie-break wiring without importing cvi.derive.bundle.                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LastNonBotCommitMeasured:
    """``last_non_bot_commit_at``, measured (02 section 10 D-TIE-1 item 7).

    ``value`` is the ISO-8601 UTC date/datetime of the max non-bot commit
    (an explicit trailing ``Z`` -- ``cvi.core.tiebreak._commit_epoch`` reads
    it as UTC either way, but ``cvi.derive.bundle`` writes it unambiguously),
    or ``None`` for the measured-absence outcome (docs/v2 D-MISS-1 F2
    ruling: "no non-bot commit in the window" is a measured absence, not an
    ``Unmeasured`` value -- distinct from a missing/failed envelope, which
    is ``Unmeasured(reason="collector_failure")`` instead)."""

    value: Optional[str]
    method: str
    source: str
    source_hash: str
    fetched_at: str


#: A W1-5f last-commit fact is either the purpose-built "measured" shape
#: above (``Measured`` itself cannot represent it -- its ``method`` field
#: is pinned to "primary"/"proxy:<name>", ``cvi.core.models.
#: measured_problems``, and would reject both a ``None`` value and this
#: packet's "measured:no_non_bot_commit_365d" method sentinel), or
#: ``Unmeasured`` (a missing/failed envelope -- ``Unmeasured`` needed no
#: such replacement; it has no constraint this outcome trips).
LastNonBotCommitValue = Union[LastNonBotCommitMeasured, Unmeasured]

_LAST_NON_BOT_COMMIT_ATTR = "_w1_5f_last_non_bot_commit_at"

#: getattr default for a bundle ``cvi.derive.bundle`` never set the sidecar
#: attribute on (a native v2 document, e.g. -- these facts are only sourced
#: from the v1-style raw provenance ``cvi.derive.bundle._load_v1`` reads).
_NO_COMMIT_FACT: Unmeasured = Unmeasured(
    reason="collector_failure",
    detail="no commits_365d or commits_180d envelope resolvable for this bundle",
)


def last_non_bot_commit_at(bundle: FeatureBundle) -> LastNonBotCommitValue:
    """D-TIE-1 item 7's fact for one bundle (W1-5f). ``Unmeasured(reason=
    "collector_failure")`` (:data:`_NO_COMMIT_FACT`) for a bundle
    ``cvi.derive.bundle`` never set the sidecar attribute on."""
    return getattr(bundle, _LAST_NON_BOT_COMMIT_ATTR, _NO_COMMIT_FACT)


def set_last_non_bot_commit_at(bundle: FeatureBundle, value: LastNonBotCommitValue) -> None:
    """``cvi.derive.bundle._resolve_commit_facts`` (W1-5f) is this
    setter's only caller. Defined here rather than as a same-module
    private helper because :func:`last_non_bot_commit_at` above must live
    in ``cvi.core`` (see the module docstring); a setter with no matching
    reader in the same module would be an odd asymmetry, so both live
    together."""
    setattr(bundle, _LAST_NON_BOT_COMMIT_ATTR, value)
