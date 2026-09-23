"""D-IN-5 StarScout universe-entry filter.

``scripts/build_universe.py`` audits the star velocity of every candidate
before writing it into ``data/raw/candidates.jsonl``, and the
``star_burst`` detector (``docs/v2/03-ANTI-MANIPULATION.md`` section 6)
reads the evidence this module records. The testable core of that step:

* :func:`collect_star_history` fetches one repo's recent stargazer events
  and its fork delta through an injectable :data:`Transport` and writes one
  raw envelope (``data/raw/star_history/<owner>__<name>.json``) -- the
  provenance chain's anchor.
* :func:`star_velocity_evidence` reads that envelope back, runs
  ``porchlamp.starscout.audit_star_velocity`` over it, and returns a
  :class:`StarVelocityRecord`: ``star_velocity_flag`` (whether the
  lockstep penalty fired), ``stars_audited`` (the de-biased count) and
  ``star_velocity`` (the raw trailing-window count plus its window), each
  carrying the envelope's path/sha256/fetched_at as provenance. A failed
  fetch never becomes a silent ``False``/``0`` -- the whole record is
  ``Unmeasured(collector_failure)`` (D-MISS-1); this check has no
  third-party-gap or insufficient-n case of its own (a reachable GitHub
  always answers this query), so ``collector_failure`` is the only reason
  a universe-entry audit can fail.
* :data:`default_star_history_transport` has no live implementation: a
  genuine per-account "total platform events" audit
  (``STARS_SCOUT_MIN_EVENTS``'s throwaway-account check) needs one extra
  REST call per stargazer login. It
  raises rather than fabricating a value, so a real run of
  ``scripts/build_universe.py`` reports every candidate
  ``Unmeasured(collector_failure)`` (an honest gap) instead of a wrong
  ``Measured`` value -- the same D-MISS-1 principle applied to the
  collector itself, not just its callers. A live transport is wired in at
  one call site (``scripts/build_universe.py``'s
  ``STAR_HISTORY_TRANSPORT``).

``scripts/build_universe.py`` applies this to every candidate at entry;
``porchlamp/derive/bundle.py``'s ``UNIVERSE_META_MAPPINGS`` carries
``star_velocity_flag``/``stars_audited`` onward from the per-repo derived
document that ``scripts/derive.py``'s universe hook copies them into.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from porchlamp.collect.envelope import read_envelope, write_envelope
from porchlamp.core import constants
from porchlamp.models import StargazerEvent
from porchlamp.starscout import audit_star_velocity

__all__ = [
    "STAR_HISTORY_COLLECTOR",
    "StarVelocityRecord",
    "Transport",
    "collect_star_history",
    "default_star_history_transport",
    "parse_stargazer_events",
    "star_velocity_evidence",
    "unmeasured_star_velocity",
]

#: gather-style transport: fetch(owner, name) -> {"events": [...],
#: "repo_forks_delta_90d": int}, raising on any transport failure.
Transport = Callable[[str, str], Mapping[str, Any]]

#: Envelope collector name (``data/raw/star_history/<owner>__<name>.json``).
STAR_HISTORY_COLLECTOR = "star_history"

#: Per-repo durable record of the D-IN-5 audit result
#: (``data/raw/universe_entry/<owner>__<name>.json``) -- what
#: ``scripts/derive.py``'s universe hook reads via the same
#: ``_load(data_dir, "universe_entry", key)`` convention every other v1
#: field uses (mirrors ``github_meta`` for ``stars``/``forks``). Its own
#: provenance is distinct from the record's *content*: the record's
#: ``provenance`` field always names the ``star_history`` envelope (what
#: was actually measured); this collector name is only the wrapper
#: envelope's own location on disk.
UNIVERSE_ENTRY_COLLECTOR = "universe_entry"


@dataclass(frozen=True)
class StarVelocityRecord:
    """One universe-entry star-velocity record (D-IN-5), JSON-native.

    Does not reuse ``porchlamp.core.models.Measured``: that type's ``value``
    field explicitly rejects ``bool`` (``measured_problems``, D-VER-2's
    int/float/str contract) and ``star_velocity_flag`` is a bool -- a
    second value encoding would be needed just to work around that, more
    confusing than this dedicated shape. ``measured`` mirrors D-MISS-1's
    Measured/Unmeasured split for a fact this collector cannot always
    produce (the star-history fetch can fail): when ``False`` every other
    field is ``None`` and ``reason`` is one of
    ``porchlamp.core.constants.UNMEASURED_REASONS`` (in practice always
    ``"collector_failure"`` -- see the module docstring for why this check
    has no other failure mode); the JSON on disk spells this exactly like
    ``porchlamp.core.models.Unmeasured`` for a reader that already knows that
    vocabulary.
    """

    measured: bool
    star_velocity_flag: Optional[bool] = None
    stars_audited: Optional[int] = None
    star_velocity: Optional[dict[str, int]] = None
    provenance: Optional[dict[str, str]] = None
    reason: Optional[str] = None
    detail: str = ""

    def __post_init__(self) -> None:
        measured_fields = (self.star_velocity_flag, self.stars_audited,
                           self.star_velocity, self.provenance)
        if self.measured:
            if any(field is None for field in measured_fields):
                raise ValueError(
                    "a measured StarVelocityRecord needs star_velocity_flag, "
                    "stars_audited, star_velocity and provenance, got "
                    f"{measured_fields!r}"
                )
            if self.reason is not None:
                raise ValueError("a measured StarVelocityRecord must not carry a reason")
        else:
            if any(field is not None for field in measured_fields):
                raise ValueError(
                    "an unmeasured StarVelocityRecord must not carry star_velocity_flag, "
                    f"stars_audited, star_velocity or provenance, got {measured_fields!r}"
                )
            if self.reason not in constants.UNMEASURED_REASONS:
                raise ValueError(
                    f"reason must be one of {constants.UNMEASURED_REASONS}, got {self.reason!r}"
                )

    def to_json(self) -> dict[str, Any]:
        """The JSON shape written to the universe manifest / per-repo envelope."""
        if self.measured:
            return {
                "measured": True,
                "star_velocity_flag": self.star_velocity_flag,
                "stars_audited": self.stars_audited,
                "star_velocity": dict(self.star_velocity or {}),
                "provenance": dict(self.provenance or {}),
            }
        return {"measured": False, "reason": self.reason, "detail": self.detail}

    @classmethod
    def from_json(cls, document: Mapping[str, Any]) -> "StarVelocityRecord":
        """Inverse of :meth:`to_json`; raises ``ValueError`` (via
        ``__post_init__``) on a malformed document rather than silently
        accepting one, exactly as ``porchlamp.core.models.Measured``/``Unmeasured``
        already reject a malformed shape at construction."""
        if document.get("measured"):
            return cls(
                measured=True,
                star_velocity_flag=bool(document["star_velocity_flag"]),
                stars_audited=int(document["stars_audited"]),
                star_velocity=dict(document["star_velocity"]),
                provenance=dict(document["provenance"]),
            )
        return cls(measured=False, reason=document.get("reason"), detail=document.get("detail", ""))


def unmeasured_star_velocity(
    reason: str = "collector_failure", detail: str = ""
) -> StarVelocityRecord:
    """The universe-entry record for a repo whose star-history fetch failed
    (D-MISS-1): never a silent ``False``/``0`` on any of the three fields."""
    return StarVelocityRecord(measured=False, reason=reason, detail=detail)


def _parse_iso(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; ``None`` on anything else. Naive
    timestamps are assumed UTC so a parsed ``StargazerEvent.starred_at`` is
    always comparable to the caller's timezone-aware ``now``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_stargazer_events(raw_events: Iterable[Mapping[str, Any]]) -> list[StargazerEvent]:
    """Raw envelope ``events`` rows -> :class:`~porchlamp.models.StargazerEvent`
    list, dropping any row missing a valid ``user_login``/``starred_at``/
    ``user_total_platform_events`` rather than raising -- a malformed row
    is this collector's own defect, not a reason to lose every other valid
    row in the same fetch."""
    events: list[StargazerEvent] = []
    for row in raw_events:
        if not isinstance(row, Mapping):
            continue
        login = row.get("user_login")
        starred_at = _parse_iso(row.get("starred_at"))
        total = row.get("user_total_platform_events")
        total_ok = isinstance(total, int) and not isinstance(total, bool)
        if not isinstance(login, str) or not login or starred_at is None or not total_ok:
            continue
        events.append(StargazerEvent(
            user_login=login, starred_at=starred_at, user_total_platform_events=total,
        ))
    return events


def default_star_history_transport(owner: str, name: str) -> dict[str, Any]:
    """No live implementation: raises rather than fabricating the
    per-account throwaway audit ``STARS_SCOUT_MIN_EVENTS`` needs, so
    callers must pass ``fetch=`` explicitly (every test does; a real run of
    ``scripts/build_universe.py`` reports every candidate
    ``Unmeasured(collector_failure)`` while this placeholder is in use)."""
    raise NotImplementedError(
        "porchlamp.collect.universe has no live star-history transport yet: "
        f"pass fetch= explicitly (requested for {owner}/{name})"
    )


def collect_star_history(
    raw_dir: Path, owner: str, name: str, fetch: Transport, fetched_at: str,
    *, force: bool = False,
) -> str:
    """Fetch one repo's recent stargazer events + fork delta via ``fetch``
    and write one envelope; returns ``"ok"`` or ``"fail"`` -- never raises.
    Existence of the envelope is the checkpoint (``scripts/gather.py``'s own
    convention): a prior successful collection is reused unless
    ``force=True``. A transport failure (any exception, or a malformed
    payload) is exactly what this collector exists to catch and report,
    same convention as ``porchlamp.collect.repo_object.collect_repo_object``.
    """
    path = raw_dir / STAR_HISTORY_COLLECTOR / f"{owner}__{name}.json"
    if path.is_file() and not force:
        return "ok"
    try:
        payload = fetch(owner, name)
    except Exception:  # review-ok: gate-network -- transport failure -> "fail"
        return "fail"
    if not isinstance(payload, Mapping):
        return "fail"
    events = payload.get("events")
    forks_delta = payload.get("repo_forks_delta_90d")
    forks_delta_ok = isinstance(forks_delta, int) and not isinstance(forks_delta, bool)
    if not isinstance(events, list) or not forks_delta_ok:
        return "fail"
    write_envelope(
        path,
        source=f"github:stargazers:{owner}/{name}",
        data={"events": events, "repo_forks_delta_90d": forks_delta},
        fetched_at=fetched_at,
    )
    return "ok"


def star_velocity_evidence(
    raw_dir: Path, owner: str, name: str, now: datetime, fetch: Transport, fetched_at: str,
    *, force: bool = False,
) -> StarVelocityRecord:
    """D-IN-5 universe-entry star-velocity check for one candidate repo.

    Collects the star-history envelope (or reuses one already on disk),
    runs :func:`porchlamp.starscout.audit_star_velocity` over it, and returns the
    full evidence record with provenance. Never raises: a transport
    failure becomes :func:`unmeasured_star_velocity`, never a silent
    ``False``/``0``.
    """
    path = raw_dir / STAR_HISTORY_COLLECTOR / f"{owner}__{name}.json"
    status = collect_star_history(raw_dir, owner, name, fetch, fetched_at, force=force)
    if status != "ok":
        record = unmeasured_star_velocity(
            detail=f"star-history fetch failed for {owner}/{name}"
        )
    else:
        envelope = read_envelope(path)
        data = envelope.data if isinstance(envelope.data, Mapping) else {}
        events = parse_stargazer_events(data.get("events") or [])
        forks_delta = data.get("repo_forks_delta_90d")
        forks_delta = (forks_delta if isinstance(forks_delta, int)
                       and not isinstance(forks_delta, bool) else 0)
        audit = audit_star_velocity(events, forks_delta, now)
        record = StarVelocityRecord(
            measured=True,
            star_velocity_flag=audit.penalty_applied,
            stars_audited=audit.valid_stars,
            star_velocity={
                "recent_stars": audit.recent_stars,
                "window_days": constants.STARS_SCOUT_WINDOW_DAYS,
            },
            provenance={
                # The canonical relative envelope path,
                # ``raw/<collector>/<key>.json`` -- never ``str(path)``. This
                # dict is written into a hashed envelope body, and an absolute
                # path names the machine that collected it, so the record
                # would neither resolve nor verify anywhere else
                # (docs/v2/00-README.md non-negotiables). It is built from the
                # collector and key rather than from ``raw_dir`` so the
                # recorded form does not depend on how the caller spelled the
                # directory.
                "path": f"raw/{STAR_HISTORY_COLLECTOR}/{owner}__{name}.json",
                "sha256": envelope.sha256,
                "fetched_at": envelope.fetched_at,
            },
        )
    write_envelope(
        raw_dir / UNIVERSE_ENTRY_COLLECTOR / f"{owner}__{name}.json",
        source="porchlamp.collect.universe:star_velocity_filter",
        data=record.to_json(),
        fetched_at=fetched_at,
    )
    return record
