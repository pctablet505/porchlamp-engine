"""CVI v2 stage 9: tie-break key, strict ranks and tie groups (D-TIE-1/2,
`02` section 10; `04` section 3 ``ScoreRecord.tie_key``/``tie_group``/
``rank``, section 6's SQL ``ORDER BY``).

D-TIE-1 order for a *rated* record (``composite is not None``):

1. composite, 2 decimals, descending;
2. coverage descending;
3. responsiveness pillar score descending;
4. friction ascending;
5. leverage descending;
6. TTFHR p75 (hours) ascending;
7. date of the last non-bot commit descending;
8. lowercase ``owner/name`` ascending (deterministic last resort).

At every level 3-7, an unmeasured (``None``) value sorts **after** a
measured one, regardless of that level's own direction: ``_measured_desc``
and ``_measured_asc`` below both encode "unmeasured" as a leading ``1``
against a measured value's leading ``0``, so plain ascending tuple
comparison puts every measured row ahead of every unmeasured row at that
level before either direction is even considered. Composite and coverage
never take this path: :class:`~cvi.core.models.ScoreRecord` types them
``float`` / ``float | None`` but a *rated* record (the only kind
:func:`tie_key` accepts) always has both measured by construction
(``cvi.core.aggregate.coverage_status`` only rates a coverage that already
cleared D-EVID-1, and a composite is exactly what "rated" means).

Stars, forks and downloads are never read by this module at all (D-TIE-1:
"never tie-breakers -- vanity and gameable"); the acceptance tests attach
adversarial values for them to :class:`TieBreakInput` to prove it.

Ownership request -> W2-8 (recorded here and in the handoff). ``ScoreRecord``
reserves ``tie_key``/``tie_group``/``rank`` for this packet to populate, but
the record itself carries none of the following, so this module cannot
implement ``tie_key(record: ScoreRecord) -> tuple`` as a single-argument
function over the stored contract alone:

- repo identity (``owner``/``name``): `04` section 3 puts these on
  ``FeatureBundle.repo``, never on ``ScoreRecord``. :class:`TieBreakInput`
  below carries them alongside the record; a caller that already has the
  bundle in hand (as ``cvi.core.engine.score`` does) reads them from
  ``bundle.repo.owner`` / ``bundle.repo.name``.
- TTFHR p75: not one of the 18 scored parameters (1.1 TTFHR is the
  *median*, D-TIE-1 item 6 explicitly wants the p75 "for consistency, not
  just the median") and not carried by ``ScoreRecord`` or
  ``FeatureBundle.inputs``. It *is* computable from evidence the bundle
  already carries -- :func:`ttfhr_p75_from_pr_window` below, applied to
  ``FeatureBundle.pr_window`` -- so a caller with the bundle can supply a
  real value today; this module does not block on W2-8 for this one.
- date of the last non-bot commit: not derivable from anything in
  ``FeatureBundle`` or ``GateEvidence`` -- `04` section 3 has no commit-list
  or commit-date field anywhere, and W1-5's vitality evidence
  (``GateEvidence.vitality_commits_180d``) is a trailing-180-day *count*,
  not a date. A caller has no source for this field until a future packet
  (W2-8, or whichever packet owns the commit-collection evidence) wires a
  dated field through the bundle or the record. Until then every caller
  passes ``None`` here, which is the correct "unmeasured" value under this
  module's own rule, not a guess.

Import-graph note (`04` section 3, ``tests/core/test_import_graph.py``):
this module imports only ``cvi.core`` plus the standard library.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Sequence

from cvi.core.models import PRWindow, ScoreRecord

#: D-TIE-1 item 3: the pillar name whose score is the tie-break input.
_RESPONSIVENESS_PILLAR = "responsiveness"


@dataclass(frozen=True)
class TieBreakInput:
    """One rated-or-not record plus the D-TIE-1 evidence
    :class:`~cvi.core.models.ScoreRecord` does not itself carry (see the
    module docstring's ownership request): the repo identity, TTFHR p75 and
    the date of the last non-bot commit. ``stars``/``forks``/``downloads``
    are accepted and stored for parity with a real repository row but are
    never read by :func:`tie_key` or :func:`assign_ranks` (D-TIE-1: never
    tie-breakers).

    ``owner``/``name`` are required: every repo in the universe has both.
    ``ttfhr_p75_hours`` and ``last_non_bot_commit_at`` default to ``None``
    (unmeasured) because no caller can supply the second one yet (see the
    ownership request); ``None`` is the correct value, not a placeholder to
    fix later in this module.
    """

    record: ScoreRecord
    owner: str
    name: str
    ttfhr_p75_hours: float | None = None
    last_non_bot_commit_at: str | None = None
    stars: int | None = None
    forks: int | None = None
    downloads: int | None = None

    def __post_init__(self) -> None:
        problems: list[str] = []
        if not isinstance(self.record, ScoreRecord):
            problems.append(f"record must be a ScoreRecord, got {self.record!r}")
        if not isinstance(self.owner, str) or not self.owner:
            problems.append(f"owner must be a non-empty string, got {self.owner!r}")
        if not isinstance(self.name, str) or not self.name:
            problems.append(f"name must be a non-empty string, got {self.name!r}")
        if problems:
            raise ValueError("; ".join(problems))


def ttfhr_p75_from_pr_window(pr_window: PRWindow) -> float | None:
    """Nearest-rank p75 (``ceil(0.75 * N)``, 1-indexed -- the same method
    `02` section 4's 1.5 TTM_p90 uses) of hours from PR creation to the
    first substantive human response, over external
    :class:`~cvi.core.models.PRWindowEvent` entries that received one.

    This is a statistic this module defines for tie-breaking only (D-TIE-1
    item 6): it does not reuse or replace 1.1 TTFHR, which is the *median*
    of a different, already-filtered ``ttfhr_hours`` bundle input
    (`cvi.core.params.responsiveness.ttfhr`), not a computation over raw
    PR-window events. Returns ``None`` (unmeasured) when no external event
    in the window has a recorded first human response -- never ``0.0`` or
    another guessed placeholder."""
    hours = sorted(
        _hours_between(event.created_at, event.first_human_review_at)
        for event in pr_window.events
        if event.is_external and event.first_human_review_at is not None
    )
    if not hours:
        return None
    index = math.ceil(0.75 * len(hours))
    return hours[index - 1]


def _hours_between(start: str, end: str) -> float:
    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    return (end_dt - start_dt).total_seconds() / 3600.0


def _commit_epoch(value: str | None) -> float | None:
    """ISO date/datetime string to a comparable epoch value; ``None`` stays
    ``None`` (unmeasured). No wall clock is read -- this only parses a
    string a caller supplied."""
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _measured_desc(value: float | None) -> tuple[int, float]:
    """Encode one D-TIE-1 *descending* level so plain ascending tuple
    comparison sorts it correctly: unmeasured (``None``) always compares as
    ``(1, 0.0)``, after every measured ``(0, -value)`` -- the negation is
    what turns ascending comparison into a descending one for the measured
    group."""
    if value is None:
        return (1, 0.0)
    return (0, -value)


def _measured_asc(value: float | None) -> tuple[int, float]:
    """Same encoding as :func:`_measured_desc` for an *ascending* D-TIE-1
    level: unmeasured still sorts after measured, but the measured group
    keeps its value un-negated."""
    if value is None:
        return (1, 0.0)
    return (0, value)


def tie_key(entry: TieBreakInput) -> tuple:
    """D-TIE-1 sort key for one *rated* entry (``entry.record.composite is
    not None``). Ascending comparison of the returned tuples reproduces the
    full D-TIE-1 order (composite/coverage/leverage descending and
    friction/TTFHR-p75 ascending are both folded into "ascending on this
    tuple" by :func:`_measured_desc` / :func:`_measured_asc`; composite and
    coverage use the same negation directly since they are never
    unmeasured for a rated record).

    Raises ``ValueError`` if ``entry.record.composite`` is ``None``: this
    function is for rated records only (D-TIE-2 -- gated and unrated
    records are ordered by :func:`assign_ranks` separately, by name, and
    never call this function)."""
    if entry.record.composite is None:
        raise ValueError("tie_key requires a rated record (composite is not None)")
    pillar = entry.record.pillars.get(_RESPONSIVENESS_PILLAR)
    responsiveness_score = pillar.score if pillar is not None else None
    return (
        -round(entry.record.composite, 2),
        -entry.record.coverage,
        _measured_desc(responsiveness_score),
        _measured_asc(entry.record.friction),
        _measured_desc(entry.record.leverage),
        _measured_asc(entry.ttfhr_p75_hours),
        _measured_desc(_commit_epoch(entry.last_non_bot_commit_at)),
        (entry.owner.lower(), entry.name.lower()),
    )


def _name_key(entry: TieBreakInput) -> tuple[str, str]:
    """D-TIE-2's ordering for gated/unrated rows: lowercase ``owner/name``,
    the same final level :func:`tie_key` falls back to for rated rows."""
    return (entry.owner.lower(), entry.name.lower())


def assign_ranks(
    entries: Sequence[TieBreakInput],
) -> list[tuple[ScoreRecord, int | None, int | None]]:
    """D-TIE-2: strict ranks ``1..N`` over rated entries only, tie groups
    shared by rows tying on the (2-decimal) composite, and gated/unrated
    entries listed after every rated one, unranked, ordered by name.

    Returns one ``(record, rank, tie_group)`` triple per input entry, in
    final display order; ``record`` is ``entry.record`` with ``tie_key``,
    ``tie_group`` and ``rank`` filled in (``dataclasses.replace`` --
    ``ScoreRecord`` is frozen), so the two other tuple positions are a
    convenience, not the only place the values live. A rated row whose
    composite does not tie with any other rated row's gets ``tie_group =
    None`` (nothing to share); every rated row gets a non-``None``
    ``rank``. Gated/unrated rows get ``rank = None``, ``tie_group = None``
    and ``tie_key = ()`` (D-TIE-2: unranked).

    Ordering is total and deterministic regardless of ``entries``' input
    order: entries are partitioned, then each partition is sorted by its
    own key (:func:`tie_key` / :func:`_name_key`, both strict total orders
    for distinct repositories -- the last level of each is the lowercase
    ``owner/name`` pair, unique per repo), and tie-group ids are assigned
    by walking the *sorted* result, never the input order."""
    rated = [entry for entry in entries if entry.record.composite is not None]
    unrated = [entry for entry in entries if entry.record.composite is None]

    rated_sorted = sorted(rated, key=tie_key)

    composite_counts: dict[float, int] = {}
    for entry in rated_sorted:
        composite = round(entry.record.composite, 2)  # type: ignore[arg-type]
        composite_counts[composite] = composite_counts.get(composite, 0) + 1

    result: list[tuple[ScoreRecord, int | None, int | None]] = []
    group_ids: dict[float, int] = {}
    next_group_id = 1
    for rank, entry in enumerate(rated_sorted, start=1):
        composite = round(entry.record.composite, 2)  # type: ignore[arg-type]
        tie_group: int | None
        if composite_counts[composite] > 1:
            if composite not in group_ids:
                group_ids[composite] = next_group_id
                next_group_id += 1
            tie_group = group_ids[composite]
        else:
            tie_group = None
        key = tie_key(entry)
        updated = replace(entry.record, tie_key=key, tie_group=tie_group, rank=rank)
        result.append((updated, rank, tie_group))

    for entry in sorted(unrated, key=_name_key):
        updated = replace(entry.record, tie_key=(), tie_group=None, rank=None)
        result.append((updated, None, None))

    return result
