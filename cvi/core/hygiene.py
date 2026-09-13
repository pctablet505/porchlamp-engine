"""D-IN-3 author eligibility and the per-author cap (packet W3-1).

Promoted here from ``cvi.collect.github_graphql`` (which introduced it in
W1-4) so every rate parameter's derivation shares the SAME tested D-IN-3
mechanism (R-2: one definition per fact) -- whether at the collector layer
today (``cvi.collect.github_graphql.derive_cohort_fields``) or, once W2-9
wires a resolver, at ``cvi.derive.bundle``'s layer. ``cvi.core`` may import
only ``cvi.core`` plus the standard library (04 section 3 import rule,
enforced by ``tests/core/test_import_graph.py``), so this module is
self-contained: it takes plain strings/ints/datetimes, never a collector's
envelope shape.

D-IN-3 (``docs/v2/02-RANKING-DESIGN.md``): "For rate parameters (merge,
decision, conversion, return) a PR author counts only if the account is
>= 90 days old at PR creation and has >= 5 public events, mirroring
StarScout's throwaway rule. Per-author cap: one author contributes at most
20% of any rate's numerator and denominator (excess PRs from one author
are dropped, most recent first)."
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from cvi.core.constants_v2 import AUTHOR_CAP_FRACTION, MIN_ACCOUNT_AGE_DAYS, MIN_PUBLIC_EVENTS

__all__ = [
    "is_eligible_account",
    "account_eligibility",
    "apply_author_cap",
]


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; ``None`` when absent or unparseable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_eligible_account(account_created_at: Optional[str],
                        pr_created_at: Optional[str],
                        public_event_count: Optional[int]) -> bool:
    """D-IN-3: the account is at least :data:`MIN_ACCOUNT_AGE_DAYS` old at
    PR creation and has at least :data:`MIN_PUBLIC_EVENTS` public events.
    ``False`` whenever a recorded input is missing — an absent input never
    reads as success."""
    created = _parse_iso(account_created_at)
    pr = _parse_iso(pr_created_at)
    if created is None or pr is None or pr < created:
        return False
    if pr - created < timedelta(days=MIN_ACCOUNT_AGE_DAYS):
        return False
    return (public_event_count is not None
            and public_event_count >= MIN_PUBLIC_EVENTS)


def account_eligibility(account_created_at: Optional[str], pr_created_at: Optional[str],
                        public_event_count: Optional[int], *, measured: bool) -> Optional[bool]:
    """Tri-state D-IN-3 eligibility (W1-4 round 3, review B-3): ``None``
    when ``measured`` is ``False`` — an upstream fetch failed for this
    author, the caller's own signal (e.g. a collector's per-author
    ``eligibility_unmeasured``/``history_unmeasured`` flag) — else
    ``True``/``False`` from :func:`is_eligible_account`. A ``None`` here
    must be excluded from a rate's numerator AND denominator by the
    caller, pinned with ``is True`` — never truthiness or ``is not
    False``, both of which read ``None`` as eligible (W1-4 round-3 review
    C3b, mutant R4)."""
    if not measured:
        return None
    return is_eligible_account(account_created_at, pr_created_at, public_event_count)


def apply_author_cap(items: Sequence[tuple[str, datetime]]) -> tuple[int, ...]:
    """D-IN-3 per-author cap: the indices of the items that survive.

    ``items`` are ``(author_login, created_at)`` pairs of one rate's
    population; each author keeps at most
    ``max(1, floor(AUTHOR_CAP_FRACTION * len(items)))`` of them, the most
    recent dropped first (the oldest kept).

    W3-1f (review B-1): a small population -- ``floor(AUTHOR_CAP_FRACTION *
    len(items))`` is 0 below 5 items -- KEEPS EVERYTHING until the cap can
    actually bind, rather than losing every item outright. "The cap can
    bind" means some author's own share exceeds the per-author allowance;
    the ``max(1, ...)`` floor guarantees every author always keeps at
    least their oldest item, so a population of several *independent*
    single-item authors (nobody dominating) is never zeroed just because
    it is small, while a population genuinely dominated by one login (up
    to and including ALL of it) is still capped down to that one
    allowance -- the cap still binds on concentration, it just never
    manufactures a false "no evidence at all" out of an ordinary small,
    undominated sample. (The prior rule -- ``allowed <= 0`` drops
    everything -- read a cohort of 3 unrelated single-PR authors, capped
    to 0 for having no 20%-sized bucket to sit in, as "0 PRs of
    evidence", which is false: D-IN-3's cap bounds one author's SHARE, it
    is not itself a sample-size floor -- that job belongs to each rate's
    own ``PARAM_MIN_N``, applied by the caller against this function's
    OWN return value, not against some other pre-cap count (review B-1).)
    """
    allowed = max(1, int(AUTHOR_CAP_FRACTION * len(items)))
    slots: dict[str, list[int]] = {}
    for idx, (login, _created) in enumerate(items):
        slots.setdefault(login, []).append(idx)
    kept: list[int] = []
    for idxs in slots.values():
        idxs.sort(key=lambda i: items[i][1])  # oldest first
        kept.extend(idxs[:allowed])
    return tuple(sorted(kept))
