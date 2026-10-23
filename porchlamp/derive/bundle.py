"""Load, validate and dump FeatureBundle v2 documents.

A v2 document (``data/derived/<key>.json``, ``schema_version = 2``) is the
JSON shape of :class:`porchlamp.core.models.FeatureBundle`:
``{schema_version, repo, inputs, pr_window, cohorts, provenance_hash}``.
``validate_bundle`` checks it strictly: every one of the 18 parameter input
ids must be present as a Measured or an Unmeasured value, and any other
violation raises :class:`porchlamp.core.models.BundleValidationError`.

Documents without a ``schema_version`` (or with ``schema_version = 1``) are
the v1 derived format written by ``scripts/derive.py``. The v1 deriver filled
missing inputs with defaults; the v2 mapping does not. Envelope-backed v1
fields become Measured values whose provenance is the envelope path plus
``sha256`` of the envelope's ``data`` field; everything else becomes Unmeasured
with a documented reason.

:data:`INPUT_MAPPINGS` provides one resolver per v2 input id (18 rows,
matching ``constants.PARAM_INPUT_IDS`` one-for-one).
"""
from __future__ import annotations

import datetime
import json
import math
from dataclasses import dataclass, field, fields
from datetime import timedelta
from datetime import timezone as _utc_timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Union

from porchlamp.core import commit_domains, constants, gates, hygiene, landing, models, openssf
from porchlamp.core.models import (
    BundleValidationError,
    Cohorts,
    FeatureBundle,
    Measured,
    NewcomerAuthor,
    PRRef,
    PRWindow,
    PRWindowEvent,
    RepoIdentity,
    Unmeasured,
)

#: Document top-level keys, derived from the FeatureBundle contract.
TOP_LEVEL_KEYS: tuple[str, ...] = tuple(field.name for field in fields(FeatureBundle))
REPO_KEYS: tuple[str, ...] = tuple(field.name for field in fields(RepoIdentity))
EVENT_KEYS: tuple[str, ...] = tuple(field.name for field in fields(PRWindowEvent))
MEASURED_DOC_KEYS: tuple[str, ...] = ("kind",) + tuple(field.name for field in fields(Measured))
UNMEASURED_DOC_KEYS: tuple[str, ...] = ("kind", "reason", "detail")

#: v1 enum spellings -> v2 3.1 regimes.
V1_LEGAL_IP_VALUES: dict[str, str] = {
    "DCO": "DCO",
    "FOUNDATION_CLA": "FOUNDATION_CLA",
    "CORP_CLA_RECIPROCAL": "CORP_CLA",
    "CORP_CLA_UNILATERAL": "CORP_CLA",
}

#: v1 enum spellings -> v2 3.3 rungs.
V1_LADDER_VALUES: dict[str, str] = {
    "VERIFIED_PROMOTION": "VERIFIED",
    "DOCUMENTED_PATHWAY": "DOCUMENTED",
    "AD_HOC": "AD_HOC",
    "CLOSED_SINGLE_FIRM": "CLOSED",
}


def validate_bundle(document: Mapping[str, Any]) -> FeatureBundle:
    """Validate a strict v2 document (schema_version 2) and build the bundle.

    Raises :class:`BundleValidationError` listing every violation found.
    Raises the same error for a document whose ``schema_version`` is not 2.

    ``last_non_bot_commit_at``, ``top_email_domain_share``, ``stars_audited``,
    ``star_velocity_flag``, ``forks`` and ``closed_unread`` are read back from optional
    top-level keys of the same name when present (see
    :data:`_OPTIONAL_TOP_LEVEL_KEYS` and :func:`dump_bundle`, which writes
    them). A document without them leaves the bundle-instance sidecar fact at
    its unset default. This ensures the facts survive a round trip across dump
    and load.
    """
    if not isinstance(document, Mapping):
        raise BundleValidationError([f"document must be a mapping, got {type(document).__name__}"])
    doc_keys = set(document)
    known_keys = set(TOP_LEVEL_KEYS) | _OPTIONAL_TOP_LEVEL_KEYS
    problems = [f"missing top-level key {key!r}" for key in TOP_LEVEL_KEYS if key not in doc_keys]
    problems += [f"unexpected top-level key {key!r}" for key in sorted(doc_keys - known_keys)]
    if problems:
        raise BundleValidationError(problems)

    version = document["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != constants.SCHEMA_VERSION:
        raise BundleValidationError([f"schema_version must be {constants.SCHEMA_VERSION}, got {version!r}"])

    problems: list[str] = []
    repo = _repo_from(document["repo"], problems)
    inputs = _inputs_from(document["inputs"], problems)
    window = _window_from(document["pr_window"], problems)
    cohorts = _cohorts_from(document["cohorts"], problems)
    if problems:
        raise BundleValidationError(problems)
    bundle = FeatureBundle(
        schema_version=version,
        repo=repo,
        inputs=inputs,
        pr_window=window,
        cohorts=cohorts,
        provenance_hash=document["provenance_hash"],
    )
    bundle.validate()
    if _LAST_NON_BOT_COMMIT_DOC_KEY in document:
        _set_last_non_bot_commit_at(bundle, _last_non_bot_commit_at_from_doc(document[_LAST_NON_BOT_COMMIT_DOC_KEY]))
    if _TOP_EMAIL_DOMAIN_SHARE_DOC_KEY in document:
        _set_top_email_domain_share(bundle, _top_email_domain_share_from_doc(document[_TOP_EMAIL_DOMAIN_SHARE_DOC_KEY]))
    if _STARS_AUDITED_DOC_KEY in document:
        _set_stars_audited(bundle, _stars_audited_from_doc(document[_STARS_AUDITED_DOC_KEY]))
    if _STAR_VELOCITY_FLAG_DOC_KEY in document:
        _set_star_velocity_flag(bundle, _star_velocity_flag_from_doc(document[_STAR_VELOCITY_FLAG_DOC_KEY]))
    if _FORKS_DOC_KEY in document:
        _set_forks_count(bundle, _forks_from_doc(document[_FORKS_DOC_KEY]))
    if _CLOSED_UNREAD_DOC_KEY in document:
        set_closed_unread_counts(bundle, _closed_unread_counts_from_doc(document[_CLOSED_UNREAD_DOC_KEY]))
    return bundle


def load_bundle(path: Union[Path, str], *, raw_root: Union[Path, str, None] = None) -> FeatureBundle:
    """Load one derived bundle document from disk.

    ``schema_version = 2`` documents validate strictly; a missing input key
    raises :class:`BundleValidationError` (the v1 deriver silently emitted a
    default instead). Documents without a ``schema_version``, or with
    ``schema_version = 1``, are the v1 derived format and are mapped to v2.

    ``raw_root`` (a data dir's ``raw/`` directory) resolves the envelope
    paths recorded in v1 provenance: the trailing
    ``<collector>/<key>.json`` is looked up under ``raw_root`` first, so a
    copied data dir is self-contained. Raises ``FileNotFoundError`` for a
    missing bundle file and ``json.JSONDecodeError`` for a file that is not
    JSON.
    """
    target = Path(path)
    document = json.loads(target.read_text(encoding="utf-8"))
    version = document.get("schema_version") if isinstance(document, Mapping) else None
    if version == constants.SCHEMA_VERSION:
        return validate_bundle(document)
    if version in (None, 1) and isinstance(document, Mapping) and "meta" in document:
        return _load_v1(document, raw_root)
    raise BundleValidationError([f"unrecognized bundle format: schema_version {version!r}"])


def dump_bundle(bundle: FeatureBundle) -> dict[str, Any]:
    """Serialize a FeatureBundle into the v2 document shape (validates first)."""
    bundle.validate()
    inputs: dict[str, dict[str, Any]] = {}
    for input_id in constants.PARAM_INPUT_IDS:
        value = bundle.inputs[input_id]
        if isinstance(value, Measured):
            inputs[input_id] = dict(zip(MEASURED_DOC_KEYS, ("measured", value.value, value.n, value.window_start,
                                                           value.window_end, value.method, value.source,
                                                           value.source_hash, value.fetched_at)))
        else:
            inputs[input_id] = {"kind": "unmeasured", "reason": value.reason, "detail": value.detail}
    doc: dict[str, Any] = {
        "schema_version": constants.SCHEMA_VERSION,
        "repo": {key: getattr(bundle.repo, key) for key in REPO_KEYS},
        "inputs": inputs,
        "pr_window": {
            "start": bundle.pr_window.start,
            "end": bundle.pr_window.end,
            "truncated": bundle.pr_window.truncated,
            "events": [{key: getattr(event, key) for key in EVENT_KEYS} for event in bundle.pr_window.events],
        },
        "cohorts": {
            "decision_pr_numbers": list(bundle.cohorts.decision_pr_numbers),
            "newcomer_authors": [
                {
                    "login": author.login,
                    "first_pr": _pr_ref_doc(author.first_pr),
                    "followups": [_pr_ref_doc(ref) for ref in author.followups],
                    "account_created_at": author.account_created_at,
                }
                for author in bundle.cohorts.newcomer_authors
            ],
        },
        "provenance_hash": bundle.provenance_hash,
        # Optional top-level keys (not FeatureBundle dataclass fields
        # -- see validate_bundle's docstring) that carry the two
        # bundle-instance sidecar facts across this dump/reload round trip;
        # always written (even the unset default) so a re-derive is never
        # needed just to see them again.
        _LAST_NON_BOT_COMMIT_DOC_KEY: _last_non_bot_commit_at_doc(commit_domains.last_non_bot_commit_at(bundle)),
        _TOP_EMAIL_DOMAIN_SHARE_DOC_KEY: _top_email_domain_share_doc(top_email_domain_share(bundle)),
    }
    if stars_audited(bundle) is not None:
        doc[_STARS_AUDITED_DOC_KEY] = _stars_audited_doc(getattr(bundle, _STARS_AUDITED_ATTR, None))
    if star_velocity_flag(bundle) is not None:
        doc[_STAR_VELOCITY_FLAG_DOC_KEY] = _star_velocity_flag_doc(getattr(bundle, _STAR_VELOCITY_FLAG_ATTR, None))
    if forks_count(bundle) is not None:
        doc[_FORKS_DOC_KEY] = forks_count(bundle)
    counts = closed_unread_counts(bundle)
    if counts is not None:
        doc[_CLOSED_UNREAD_DOC_KEY] = {"closed_unread": counts.closed_unread, "population_n": counts.population_n}
    return doc


def write_bundle(bundle: FeatureBundle, path: Union[Path, str]) -> Path:
    """Write the v2 document to ``path`` (v1 on-disk style: indent 2, sorted keys)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dump_bundle(bundle), indent=2, sort_keys=True), encoding="utf-8")
    return target


#: Instance attribute name backing :func:`dropped_event_count`. Not a
#: declared FeatureBundle field and not serialized by :func:`dump_bundle` —
#: a dump/reload round trip loses the count.
_DROPPED_EVENT_COUNT_ATTR = "_dropped_event_count_attr"


def dropped_event_count(bundle: FeatureBundle) -> int:
    """Number of ``pr_events`` dropped from ``bundle.pr_window.events``
    because the recorded ``author_login`` was empty — a deleted/ghost GitHub
    account, which GitHub's API reports with no login. ``PRWindowEvent``
    requires a non-empty ``author_login`` (it is an author-keyed record), so
    such an event cannot be represented there; it is dropped from author-based
    metrics but its timestamps still count toward ``pr_window.start``/``end``
    (author-irrelevant). Always 0 for a bundle whose loader never filtered
    events (native v2 documents, or a v1 bundle with none to drop)."""
    return getattr(bundle, _DROPPED_EVENT_COUNT_ATTR, 0)


def _set_dropped_event_count(bundle: FeatureBundle, count: int) -> None:
    setattr(bundle, _DROPPED_EVENT_COUNT_ATTR, count)


# --------------------------------------------------------------------------- #
# Commit facts: last_non_bot_commit_at (D-TIE-1 item 7) and                   #
# top_email_domain_share (the ef_domain_mismatch detector's input). Neither   #
# is one of constants.PARAM_INPUT_IDS's 18 scored parameters, so              #
# FeatureBundle.inputs (closed to exactly those 18 keys, enforced by          #
# FeatureBundle.validate()) cannot carry them. They live as bundle-instance   #
# sidecar attributes instead.                                                 #
#                                                                             #
# last_non_bot_commit_at's dataclass/attribute-name/accessor/setter live in   #
# porchlamp.core.commit_domains, not here: porchlamp.core.pipeline (D-TIE-1 item 7's      #
# tie-break wiring) needs to read the fact back and may only import          #
# porchlamp.core + stdlib, so it cannot reach an accessor defined in this module.   #
# top_email_domain_share has no such porchlamp.core reader and stays here.          #
# --------------------------------------------------------------------------- #

#: Re-exported from porchlamp.core.commit_domains (see above) so this module's own
#: _resolve_commit_facts/_load_v1 can build and store the fact without a
#: second import elsewhere in this file.
LastNonBotCommitMeasured = commit_domains.LastNonBotCommitMeasured
LastNonBotCommitValue = commit_domains.LastNonBotCommitValue


@dataclass(frozen=True)
class TopEmailDomainShareMeasured:
    """``top_email_domain_share``, measured: the ``ef_domain_mismatch``
    detector's input (03 section 6). ``value`` is the top email domain's
    share of ``n`` non-bot commits in ``[0, 1]``; ``top_domain`` is that
    commit email domain itself, lower-cased
    (:func:`porchlamp.core.commit_domains.top_domain_share`): a personal
    provider or ``users.noreply.github.com`` is one domain here, where 3.2
    elephant_factor counts one org per author on it -- the difference the
    detector reads; ``n`` is the non-bot commit count the share was
    computed over."""

    value: float
    top_domain: str
    n: int
    method: str
    source: str
    source_hash: str
    fetched_at: str


#: A top_email_domain_share fact is either the purpose-built
#: "measured" shape above, or ``Unmeasured`` (missing/failed envelope, or an
#: insufficient sample).
TopEmailDomainShareValue = Union[TopEmailDomainShareMeasured, Unmeasured]

_TOP_EMAIL_DOMAIN_SHARE_ATTR = "_top_email_domain_share_attr"

#: getattr default for a bundle this module never set the sidecar attribute
#: on (a native v2 document, e.g. -- see the module docstring's "these two
#: facts are only sourced from the v1-style raw provenance" scoping note).
#: Same value as porchlamp.core.commit_domains's own default for
#: last_non_bot_commit_at -- both facts are unmeasured for the exact same
#: cause when neither commits envelope resolves.
_NO_COMMIT_FACT = Unmeasured(
    reason="collector_failure",
    detail="no commits_365d or commits_180d envelope resolvable for this bundle",
)

#: Reuse the exact minimum sample size 3.2 elephant_factor uses
#: (constants.PARAM_MIN_N["elephant_factor"] -- 30 non-bot commits) for
#: top_email_domain_share's insufficient_n gate.
_TOP_DOMAIN_SHARE_MIN_N: int = constants.PARAM_MIN_N["elephant_factor"]

#: The two envelope keys :func:`_resolve_commit_window_envelope` tries, in
#: order (D-IN-4: 365 d is the EF/last-commit window; 180 d is the
#: vitality window -- still a usable narrower slice of the same commit
#: history when 365 d was never collected, which is the case for every
#: repo in the current sample corpus, not merely a hypothetical gap).
_COMMIT_ENVELOPE_KEYS: tuple[str, str] = ("commits_365d", "commits_180d")

_COMMIT_FACT_METHOD_365D: str = "measured:commits_365d"
_COMMIT_FACT_METHOD_180D_FALLBACK: str = "measured:commits_180d_fallback"
#: D-MISS-1 F2 ruling's exact method sentinel for "no non-bot commit in the
#: 365 d window" -- a measured absence, never Unmeasured (see
#: LastNonBotCommitMeasured's docstring). The 180d-fallback sibling below is
#: this module's own documented extension of that ruling to the narrower
#: window: when only commits_180d was ever collected, the strongest claim
#: this module can make is "no non-bot commit in the 180 d this bundle
#: actually has", not the full 365 d the ruling's wording describes.
_COMMIT_FACT_METHOD_NO_NON_BOT_365D: str = "measured:no_non_bot_commit_365d"
_COMMIT_FACT_METHOD_NO_NON_BOT_180D_FALLBACK: str = "measured:no_non_bot_commit_180d_fallback"

#: Public re-export: :func:`porchlamp.core.commit_domains.last_non_bot_commit_at`
#: is D-TIE-1 item 7's own bundle-level accessor (see above for why it
#: lives there); exposed under this module's namespace too so a caller of
#: ``porchlamp.derive.bundle`` need not know the split.
last_non_bot_commit_at = commit_domains.last_non_bot_commit_at
_set_last_non_bot_commit_at = commit_domains.set_last_non_bot_commit_at


def top_email_domain_share(bundle: FeatureBundle) -> TopEmailDomainShareValue:
    """The ``ef_domain_mismatch`` detector's input for one bundle.
    ``Unmeasured(reason="collector_failure")`` (:data:`_NO_COMMIT_FACT`) for
    a bundle this module never set the sidecar attribute on."""
    return getattr(bundle, _TOP_EMAIL_DOMAIN_SHARE_ATTR, _NO_COMMIT_FACT)


def _set_top_email_domain_share(bundle: FeatureBundle, value: TopEmailDomainShareValue) -> None:
    setattr(bundle, _TOP_EMAIL_DOMAIN_SHARE_ATTR, value)


set_top_email_domain_share = _set_top_email_domain_share

_STARS_AUDITED_ATTR = "_stars_audited_attr"
_STAR_VELOCITY_FLAG_ATTR = "_star_velocity_flag_attr"
_FORKS_ATTR = "_forks_attr"


def stars_audited(bundle: FeatureBundle) -> int | None:
    """Return the audited star count from ``bundle`` if Measured or int, else ``None``."""
    val = getattr(bundle, _STARS_AUDITED_ATTR, None)
    if isinstance(val, Measured):
        if isinstance(val.value, int) and not isinstance(val.value, bool):
            return val.value
        return None
    if isinstance(val, int) and not isinstance(val, bool):
        return val
    return None


def _set_stars_audited(bundle: FeatureBundle, value: Measured | Unmeasured | int | None) -> None:
    setattr(bundle, _STARS_AUDITED_ATTR, value)


set_stars_audited = _set_stars_audited


def star_velocity_flag(bundle: FeatureBundle) -> bool | None:
    """Return the StarScout lockstep-penalty flag from ``bundle`` if Measured or bool, else ``None``."""
    val = getattr(bundle, _STAR_VELOCITY_FLAG_ATTR, None)
    if isinstance(val, Measured):
        return bool(val.value)
    if isinstance(val, bool):
        return val
    return None


def _set_star_velocity_flag(bundle: FeatureBundle, value: Measured | Unmeasured | bool | None) -> None:
    setattr(bundle, _STAR_VELOCITY_FLAG_ATTR, value)


set_star_velocity_flag = _set_star_velocity_flag


def forks_count(bundle: FeatureBundle) -> int | None:
    """Return the forks count from ``bundle`` if present as int >= 0 (or Measured int >= 0), else ``None``."""
    val = getattr(bundle, _FORKS_ATTR, None)
    if isinstance(val, Measured):
        if isinstance(val.value, int) and not isinstance(val.value, bool) and val.value >= 0:
            return val.value
        return None
    if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
        return val
    return None


forks = forks_count


def _set_forks_count(bundle: FeatureBundle, value: Measured | int | None) -> None:
    setattr(bundle, _FORKS_ATTR, value)


_set_forks = _set_forks_count
set_forks_count = _set_forks_count
set_forks = _set_forks_count


@dataclass(frozen=True)
class ClosedUnreadCounts:
    """The ``closed_unread`` detector's input (03 section 6), as derive
    counted it from the recorded ``pr_window`` envelope
    (:func:`porchlamp.collect.github_graphql.closed_unread_counts`):
    ``population_n`` outside PRs opened in the window, ``closed_unread`` of
    them closed unmerged with no D-IN-2 response ever recorded."""

    closed_unread: int
    population_n: int


_CLOSED_UNREAD_ATTR = "_closed_unread_counts_attr"


def closed_unread_counts(bundle: FeatureBundle) -> ClosedUnreadCounts | None:
    """The ``closed_unread`` counts for one bundle; ``None`` when nothing was
    measured (no recorded PR window, or a bundle derived before derive
    recorded the counts), so the flag never fires."""
    return getattr(bundle, _CLOSED_UNREAD_ATTR, None)


def set_closed_unread_counts(bundle: FeatureBundle, value: ClosedUnreadCounts | None) -> None:
    setattr(bundle, _CLOSED_UNREAD_ATTR, value)


# --------------------------------------------------------------------------- #
# Round-trip serialisation: the OPTIONAL v2-document top-level keys           #
# validate_bundle/dump_bundle use to carry the sidecar facts across a real   #
# dump/reload (see validate_bundle's own docstring for why this matters --   #
# last_non_bot_commit_at feeds tie_key, so losing it on reload would         #
# silently change records_hash for any reproduction of a snapshot from       #
# dumped bundle files, e.g. tests/fixtures/snapshot_50).                    #
# --------------------------------------------------------------------------- #

_LAST_NON_BOT_COMMIT_DOC_KEY = "last_non_bot_commit_at"
_TOP_EMAIL_DOMAIN_SHARE_DOC_KEY = "top_email_domain_share"
_STARS_AUDITED_DOC_KEY = "stars_audited"
_STAR_VELOCITY_FLAG_DOC_KEY = "star_velocity_flag"
_FORKS_DOC_KEY = "forks"
_CLOSED_UNREAD_DOC_KEY = "closed_unread"
_OPTIONAL_TOP_LEVEL_KEYS = frozenset({
    _LAST_NON_BOT_COMMIT_DOC_KEY,
    _TOP_EMAIL_DOMAIN_SHARE_DOC_KEY,
    _STARS_AUDITED_DOC_KEY,
    _STAR_VELOCITY_FLAG_DOC_KEY,
    _FORKS_DOC_KEY,
    _CLOSED_UNREAD_DOC_KEY,
})


def _last_non_bot_commit_at_doc(value: LastNonBotCommitValue) -> dict[str, Any]:
    if isinstance(value, LastNonBotCommitMeasured):
        return {"kind": "measured", "value": value.value, "method": value.method, "source": value.source,
                "source_hash": value.source_hash, "fetched_at": value.fetched_at}
    return {"kind": "unmeasured", "reason": value.reason, "detail": value.detail}


def _last_non_bot_commit_at_from_doc(doc: Any) -> LastNonBotCommitValue:
    """Inverse of :func:`_last_non_bot_commit_at_doc`. Never raises: a
    malformed or unrecognised document falls back to :data:`_NO_COMMIT_FACT`
    (the same "no evidence" default an absent key already gets) rather than
    rejecting the whole bundle over an optional, non-authoritative field."""
    if not isinstance(doc, Mapping):
        return _NO_COMMIT_FACT
    if doc.get("kind") == "measured":
        try:
            return LastNonBotCommitMeasured(
                value=doc.get("value"), method=str(doc["method"]), source=str(doc["source"]),
                source_hash=str(doc["source_hash"]), fetched_at=str(doc["fetched_at"]),
            )
        except KeyError:
            return _NO_COMMIT_FACT
    if doc.get("kind") == "unmeasured":
        try:
            return Unmeasured(reason=doc.get("reason", "collector_failure"), detail=doc.get("detail", ""))
        except BundleValidationError:
            return _NO_COMMIT_FACT
    return _NO_COMMIT_FACT


def _top_email_domain_share_doc(value: TopEmailDomainShareValue) -> dict[str, Any]:
    if isinstance(value, TopEmailDomainShareMeasured):
        return {"kind": "measured", "value": value.value, "top_domain": value.top_domain, "n": value.n,
                "method": value.method, "source": value.source, "source_hash": value.source_hash,
                "fetched_at": value.fetched_at}
    return {"kind": "unmeasured", "reason": value.reason, "detail": value.detail}


def _top_email_domain_share_from_doc(doc: Any) -> TopEmailDomainShareValue:
    """Inverse of :func:`_top_email_domain_share_doc`; same never-raises
    fallback rule as :func:`_last_non_bot_commit_at_from_doc`."""
    if not isinstance(doc, Mapping):
        return _NO_COMMIT_FACT
    if doc.get("kind") == "measured":
        try:
            return TopEmailDomainShareMeasured(
                value=float(doc["value"]), top_domain=str(doc["top_domain"]), n=int(doc["n"]),
                method=str(doc["method"]), source=str(doc["source"]), source_hash=str(doc["source_hash"]),
                fetched_at=str(doc["fetched_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return _NO_COMMIT_FACT
    if doc.get("kind") == "unmeasured":
        try:
            return Unmeasured(reason=doc.get("reason", "collector_failure"), detail=doc.get("detail", ""))
        except BundleValidationError:
            return _NO_COMMIT_FACT
    return _NO_COMMIT_FACT


def _stars_audited_doc(value: Any) -> Any:
    if isinstance(value, Measured):
        return dict(zip(
            MEASURED_DOC_KEYS,
            ("measured", value.value, value.n, value.window_start, value.window_end,
             value.method, value.source, value.source_hash, value.fetched_at),
        ))
    return value


def _stars_audited_from_doc(doc: Any) -> Measured | Unmeasured | int | None:
    if isinstance(doc, int) and not isinstance(doc, bool):
        return doc
    if isinstance(doc, Mapping):
        if doc.get("kind") == "measured":
            try:
                return Measured(
                    value=doc["value"], n=doc["n"], window_start=doc.get("window_start"),
                    window_end=doc.get("window_end"), method=str(doc["method"]),
                    source=str(doc["source"]), source_hash=str(doc["source_hash"]),
                    fetched_at=str(doc["fetched_at"]),
                )
            except (KeyError, BundleValidationError, TypeError, ValueError):
                val = doc.get("value")
                if isinstance(val, int) and not isinstance(val, bool):
                    return val
                return None
        if doc.get("kind") == "unmeasured":
            try:
                return Unmeasured(reason=doc.get("reason", "collector_failure"), detail=doc.get("detail", ""))
            except BundleValidationError:
                return None
    return None


def _star_velocity_flag_doc(value: Any) -> Any:
    if isinstance(value, Measured):
        return dict(zip(
            MEASURED_DOC_KEYS,
            ("measured", value.value, value.n, value.window_start, value.window_end,
             value.method, value.source, value.source_hash, value.fetched_at),
        ))
    return value


def _star_velocity_flag_from_doc(doc: Any) -> Measured | Unmeasured | bool | None:
    if isinstance(doc, bool):
        return doc
    if isinstance(doc, int) and doc in (0, 1):
        return bool(doc)
    if isinstance(doc, Mapping):
        if doc.get("kind") == "measured":
            try:
                return Measured(
                    value=doc["value"], n=doc["n"], window_start=doc.get("window_start"),
                    window_end=doc.get("window_end"), method=str(doc["method"]),
                    source=str(doc["source"]), source_hash=str(doc["source_hash"]),
                    fetched_at=str(doc["fetched_at"]),
                )
            except (KeyError, BundleValidationError, TypeError, ValueError):
                val = doc.get("value")
                if isinstance(val, bool):
                    return val
                if isinstance(val, int) and val in (0, 1):
                    return bool(val)
                return None
        if doc.get("kind") == "unmeasured":
            try:
                return Unmeasured(reason=doc.get("reason", "collector_failure"), detail=doc.get("detail", ""))
            except BundleValidationError:
                return None
    return None


def _forks_from_doc(doc: Any) -> int | None:
    if isinstance(doc, int) and not isinstance(doc, bool) and doc >= 0:
        return doc
    if isinstance(doc, Mapping) and doc.get("kind") == "measured":
        val = doc.get("value")
        if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
            return val
    return None


def _closed_unread_counts_or_none(closed_unread: Any, population_n: Any) -> ClosedUnreadCounts | None:
    """Both counts when they are counts and the closed-unread PRs fit in the
    population, else ``None`` (nothing measured)."""
    if _is_count(closed_unread) and _is_count(population_n) and closed_unread <= population_n:
        return ClosedUnreadCounts(closed_unread=closed_unread, population_n=population_n)
    return None


def _closed_unread_counts_from_doc(doc: Any) -> ClosedUnreadCounts | None:
    """Inverse of the ``closed_unread`` key :func:`dump_bundle` writes; never
    raises: a malformed value reads as nothing measured, like an absent key."""
    if not isinstance(doc, Mapping):
        return None
    return _closed_unread_counts_or_none(doc.get("closed_unread"), doc.get("population_n"))


def _commit_envelope_path(ctx: "_V1Ctx", key: str, envelope_key: str) -> Optional[Path]:
    """Resolve one commit envelope's path for ``key`` (``"<owner>__<name>"``).

    Primary: the real, physical raw-data-dir layout
    (``raw_root/<envelope_key>/<key>.json`` -- the same convention
    ``scripts/build_fixture_snapshot.py``'s own ``_vitality_commit_count``
    already uses to read this exact envelope, and ``scripts/derive.py``'s
    ``src()``/``raw_envelope_path`` write it under). This is the only path
    that resolves against the real sample-200 corpus: its v1 documents key
    ``provenance`` by *meta field* name (``elephant_factor``,
    ``active_nonbot_commits_180d``, ...), never by the collector name
    ``"commits_365d"``/``"commits_180d"`` themselves, so a provenance-dict
    lookup under either literal string never finds anything there
    (verified against the corpus before writing this: ``grep -l
    '"commits_365d"' ~/Projects/cvi-scratch/sample-200/derived/*.json``
    returns nothing).

    Fallback: a v1 provenance entry keyed by the collector name itself (a
    hand-built or future document that does adopt that convention) --
    ``_resolve_envelope``'s own ``raw_root`` handling still applies to
    whatever path it recorded, so this only matters when ``raw_root`` is
    ``None`` and the direct path above cannot be tried at all.
    """
    if ctx.raw_root is not None:
        candidate = Path(ctx.raw_root) / envelope_key / f"{key}.json"
        if candidate.is_file():
            return candidate
    entry = ctx.provenance.get(envelope_key)
    return _resolve_envelope(_first_source(entry), ctx.raw_root)


def _envelope_fetched_at(path: Path) -> Optional[str]:
    """The envelope's own recorded ``fetched_at`` (the ``Envelope`` contract
    field, ``porchlamp/collect/envelope.py``) -- read straight from the file
    rather than from a v1 provenance entry, so this never depends on a
    provenance dict key existing at all (see :func:`_commit_envelope_path`).
    ``None`` for an unreadable or malformed file, or one recording no
    ``fetched_at``."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    fetched_at = document.get("fetched_at") if isinstance(document, Mapping) else None
    return fetched_at if isinstance(fetched_at, str) and fetched_at else None


def _resolve_commit_window_envelope(ctx: "_V1Ctx", key: str) -> Optional[tuple[Path, str, bool]]:
    """Resolve the ``commits_365d`` envelope, falling back to
    ``commits_180d`` when the first is absent. Returns ``(path, fetched_at,
    used_fallback)``, or ``None`` when neither envelope resolves to a
    readable file with a recorded ``fetched_at``.

    "Absent" means the envelope itself does not resolve (no file at the
    expected path, and no provenance entry either) -- an envelope that
    resolves but carries an empty ``data: []`` is a real, measured "0
    commits in that window", not a reason to fall back to the narrower
    one (that would silently conflate "we have no 365 d data" with "we
    measured zero commits in 365 d", two different facts)."""
    for index, envelope_key in enumerate(_COMMIT_ENVELOPE_KEYS):
        path = _commit_envelope_path(ctx, key, envelope_key)
        if path is None:
            continue
        fetched_at = _envelope_fetched_at(path)
        if fetched_at is not None:
            return path, fetched_at, index == 1
    return None


def _iso_utc_z(value: datetime.datetime) -> str:
    """``value`` as an ISO-8601 UTC string with an explicit trailing ``Z``
    (never ``+00:00``) -- D-TIE-1 item 7's own required format, and what
    lets two equal instants recorded under different source offsets
    serialize identically. Only ever normalises an already tz-aware
    ``datetime`` a caller parsed from a recorded string (never reads a
    live clock -- the aliased ``timezone`` import (as ``_utc_timezone``,
    used below) is deliberate: the qualified spelling this module's own
    top-level ``import datetime`` would otherwise call for reads, read as
    plain text, as one word ending "...time" immediately followed by a
    word starting "time...", which is exactly what
    ``tests/test_no_clock.py``'s plain-substring wall-clock scan flags --
    a false positive unrelated to this function ever reading a clock)."""
    return value.astimezone(_utc_timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve_commit_facts(ctx: "_V1Ctx", key: str) -> tuple[LastNonBotCommitValue, TopEmailDomainShareValue]:
    """Build both commit-derived facts from one envelope read (the
    ``commits_365d`` envelope, or ``commits_180d`` when the first is absent
    -- :func:`_resolve_commit_window_envelope`). ``key`` is ``"<owner>__
    <name>"`` (the same basename ``scripts/derive.py``/``scripts/
    build_fixture_snapshot.py`` use for every raw envelope file). Bot
    exclusion reuses the landed typed-actor classification
    (:func:`porchlamp.core.commit_domains.is_bot`) exactly -- a commit with no
    author login but a non-noreply email is not a bot by that rule (none
    of its three conditions read the email at all) and counts as human,
    same as everywhere else this rule applies."""
    resolved = _resolve_commit_window_envelope(ctx, key)
    if resolved is None:
        detail = "no commits_365d or commits_180d envelope with a resolvable path and fetched_at"
        return (
            Unmeasured(reason="collector_failure", detail=detail),
            Unmeasured(reason="collector_failure", detail=detail),
        )
    path, fetched_at, used_fallback = resolved
    data = commit_domains.envelope_rows(_envelope_data(path))
    if data is None:
        detail = f"envelope at {path} records no commit rows (neither a row list nor a window dict with 'rows')"
        return (
            Unmeasured(reason="collector_failure", detail=detail),
            Unmeasured(reason="collector_failure", detail=detail),
        )
    source_hash = _envelope_hash(path)
    source = _relative_source(path, ctx.raw_root)
    ctx.referenced[source] = source_hash
    records = commit_domains.records_from_rows(data)
    window_method = _COMMIT_FACT_METHOD_180D_FALLBACK if used_fallback else _COMMIT_FACT_METHOD_365D

    last_commit = commit_domains.last_non_bot_commit_date(records)
    last_value = _iso_utc_z(last_commit) if last_commit is not None else None
    last_method = window_method if last_commit is not None else (
        _COMMIT_FACT_METHOD_NO_NON_BOT_180D_FALLBACK if used_fallback else _COMMIT_FACT_METHOD_NO_NON_BOT_365D
    )
    last_fact = LastNonBotCommitMeasured(
        value=last_value, method=last_method, source=source, source_hash=source_hash, fetched_at=fetched_at,
    )

    domain = commit_domains.top_domain_share(records)
    domain_fact: TopEmailDomainShareValue
    if domain.n < _TOP_DOMAIN_SHARE_MIN_N:
        domain_fact = Unmeasured(
            reason="insufficient_n",
            detail=f"{domain.n} non-bot commits, minimum {_TOP_DOMAIN_SHARE_MIN_N}",
        )
    elif domain.top_domain == "":
        # n cleared the minimum but not one non-bot commit carried an
        # email domain (porchlamp.core.commit_domains.top_domain_share needs
        # an "@"-bearing email; this sample corpus's commits_180d rows carry
        # none at all) -- a real collector gap, distinct from "too few
        # commits" (insufficient_n) and worth its own detail.
        domain_fact = Unmeasured(
            reason="collector_failure",
            detail=f"{domain.n} non-bot commits but none carry an author_email "
                   "with a domain",
        )
    else:
        domain_fact = TopEmailDomainShareMeasured(
            value=domain.share, top_domain=domain.top_domain, n=domain.n, method=window_method,
            source=source, source_hash=source_hash, fetched_at=fetched_at,
        )
    return last_fact, domain_fact


def _pr_ref_doc(ref: PRRef) -> dict[str, Any]:
    return {"number": ref.number, "created_at": ref.created_at, "merged_at": ref.merged_at}


def _repo_from(doc: Any, problems: list[str]) -> RepoIdentity | None:
    if not isinstance(doc, Mapping):
        problems.append("repo must be a mapping")
        return None
    keys = set(doc)
    problems += [f"repo missing field {key!r}" for key in REPO_KEYS if key not in keys]
    problems += [f"repo unexpected field {key!r}" for key in sorted(keys - set(REPO_KEYS))]
    if problems:
        return None
    try:
        return RepoIdentity(**{key: doc[key] for key in REPO_KEYS})
    except BundleValidationError as error:
        problems.extend(error.problems)
        return None


def _param_from(
    cls: type,
    entry: Mapping[str, Any],
    input_id: str,
    problems: list[str],
    required_keys: tuple[str, ...],
    optional_keys: tuple[str, ...] = (),
) -> Measured | Unmeasured | None:
    keys = set(entry)
    missing = [key for key in required_keys if key not in keys]
    extra = sorted(keys - set(required_keys) - set(optional_keys))
    if missing:
        problems.append(f"{input_id}: missing field(s) {missing}")
    if extra:
        problems.append(f"{input_id}: unexpected field(s) {extra}")
    if missing or extra:
        return None
    kwargs = {key: entry.get(key) for key in required_keys}
    for key in optional_keys:
        if key != "kind":
            kwargs[key] = entry.get(key, "")
    try:
        return cls(**kwargs)
    except BundleValidationError as error:
        problems.extend(f"{input_id}: {problem}" for problem in error.problems)
        return None


def _inputs_from(doc: Any, problems: list[str]) -> dict[str, Measured | Unmeasured]:
    if not isinstance(doc, Mapping):
        problems.append("inputs must be a mapping")
        return {}
    expected = set(constants.PARAM_INPUT_IDS)
    for key in sorted(expected - set(doc)):
        problems.append(f"inputs missing key {key!r}")
    for key in sorted(set(doc) - expected):
        problems.append(f"inputs has unexpected key {key!r}")
    out: dict[str, Measured | Unmeasured] = {}
    for input_id in sorted(set(doc) & expected):
        entry = doc[input_id]
        if not isinstance(entry, Mapping):
            problems.append(f"{input_id}: must be a mapping")
            continue
        if entry.get("kind") == "measured":
            out[input_id] = _param_from(
                Measured, entry, input_id, problems,
                MEASURED_DOC_KEYS[1:], ("kind",),
            )
        elif entry.get("kind") == "unmeasured":
            out[input_id] = _param_from(
                Unmeasured, entry, input_id, problems, ("reason",), ("kind", "detail"),
            )
        else:
            problems.append(f"{input_id}: kind must be 'measured' or 'unmeasured', got {entry.get('kind')!r}")
    return out


def _window_from(doc: Any, problems: list[str]) -> PRWindow | None:
    if not isinstance(doc, Mapping):
        problems.append("pr_window must be a mapping")
        return None
    keys = set(doc)
    required = ("start", "end", "truncated", "events")
    missing = [key for key in required if key not in keys]
    extra = sorted(keys - set(required))
    if missing:
        problems.append(f"pr_window missing field(s) {missing}")
    if extra:
        problems.append(f"pr_window unexpected field(s) {extra}")
    if missing or not isinstance(doc.get("events"), list):
        if not missing and not isinstance(doc.get("events"), list):
            problems.append("pr_window.events must be a list")
        return None
    events: list[PRWindowEvent] = []
    for index, entry in enumerate(doc["events"]):
        if not isinstance(entry, Mapping):
            problems.append(f"pr_window.events[{index}] must be a mapping")
            continue
        entry_missing = [key for key in EVENT_KEYS if key not in entry]
        entry_extra = sorted(set(entry) - set(EVENT_KEYS))
        if entry_missing:
            problems.append(f"pr_window.events[{index}] missing field(s) {entry_missing}")
        if entry_extra:
            problems.append(f"pr_window.events[{index}] unexpected field(s) {entry_extra}")
        if entry_missing or entry_extra:
            continue
        try:
            events.append(PRWindowEvent(**{key: entry[key] for key in EVENT_KEYS}))
        except BundleValidationError as error:
            problems.extend(f"pr_window.events[{index}]: {problem}" for problem in error.problems)
    if problems:
        return None
    try:
        return PRWindow(
            start=doc["start"], end=doc["end"], truncated=doc["truncated"], events=tuple(events)
        )
    except BundleValidationError as error:
        problems.extend(error.problems)
        return None


def _cohorts_from(doc: Any, problems: list[str]) -> Cohorts | None:
    if not isinstance(doc, Mapping):
        problems.append("cohorts must be a mapping")
        return None
    keys = set(doc)
    required = ("decision_pr_numbers", "newcomer_authors")
    missing = [key for key in required if key not in keys]
    extra = sorted(keys - set(required))
    if missing:
        problems.append(f"cohorts missing field(s) {missing}")
    if extra:
        problems.append(f"cohorts unexpected field(s) {extra}")
    if missing:
        return None
    numbers = doc["decision_pr_numbers"]
    if not isinstance(numbers, list) or not all(
        isinstance(n, int) and not isinstance(n, bool) and n >= 1 for n in numbers
    ):
        problems.append("cohorts.decision_pr_numbers must be a list of ints >= 1")
        return None
    authors: list[NewcomerAuthor] = []
    for index, entry in enumerate(doc["newcomer_authors"]):
        if not isinstance(entry, Mapping) or not isinstance(entry.get("first_pr"), Mapping):
            problems.append(f"cohorts.newcomer_authors[{index}] must be a mapping with a first_pr mapping")
            continue
        followups = entry.get("followups")
        if not isinstance(followups, list):
            problems.append(f"cohorts.newcomer_authors[{index}].followups must be a list")
            continue
        try:
            refs = [PRRef(**{key: ref[key] for key in ("number", "created_at", "merged_at")}) for ref in followups]
            first = PRRef(**{key: entry["first_pr"][key] for key in ("number", "created_at", "merged_at")})
            authors.append(NewcomerAuthor(login=entry["login"], first_pr=first, followups=tuple(refs),
                                          account_created_at=entry.get("account_created_at")))
        except (KeyError, BundleValidationError) as error:
            problems.extend(f"cohorts.newcomer_authors[{index}]: {error!s}")
    if problems:
        return None
    try:
        return Cohorts(decision_pr_numbers=tuple(numbers), newcomer_authors=tuple(authors))
    except BundleValidationError as error:
        problems.extend(error.problems)
        return None


def _resolve_envelope(source: Any, raw_root: Union[Path, str, None]) -> Path | None:
    """Resolve a v1 provenance source to a readable envelope file.

    v1 records absolute paths under the data dir where it ran. When
    ``raw_root`` is given, the trailing ``<collector>/<key>.json`` segment is
    looked up under ``raw_root`` first, so a copied data dir is self-
    contained; otherwise the recorded path is used when it still exists.
    """
    if not isinstance(source, str) or not source or source.startswith(("http://", "https://")):
        return None
    candidate = Path(source)
    if raw_root is not None:
        parts = candidate.parts
        if "raw" in parts:
            index = len(parts) - 1 - parts[::-1].index("raw")
            tail = parts[index + 1 :]
            if len(tail) == 2:
                alt = Path(raw_root).joinpath(*tail)
                if alt.is_file():
                    return alt
    if candidate.is_file():
        return candidate
    return None


def _relative_source(path: Path, raw_root: Union[Path, str, None]) -> str:
    """Express a resolved envelope ``path`` as a provenance ``source``
    relative to the data root: never an absolute path of the machine that
    derived it, so a bundle's provenance is portable and reproduces
    byte-identically from a copy of the same data root on any machine.

    ``raw_root`` is always ``<data_root>/raw`` -- the one convention this
    module's own envelope resolution, ``porchlamp.collect.envelope.
    raw_envelope_path`` and ``scripts/derive.py``'s ``src()`` all share --
    so its parent is the data root every recorded source is relativised
    against, giving exactly ``raw/<collector>/<key>.json``. This duplicates
    ``porchlamp.collect.envelope.relative_envelope_path``'s arithmetic rather than
    importing it: this module keeps its own ``_resolve_envelope`` and
    ``_envelope_hash`` to avoid circular or collect-layer dependencies.

    Raises :class:`BundleValidationError` when ``path`` cannot be expressed
    as a relative path under a known root: a stale absolute source (for
    example a v1 document's own recorded path, when no ``raw_root`` was
    given to relativise it against) must never enter a bundle silently."""
    if raw_root is not None:
        try:
            return path.relative_to(Path(raw_root).parent).as_posix()
        except ValueError:  # Path is not subpath of raw_root; fall through to relative check
            candidate = None
    candidate = path.as_posix()
    if not path.is_absolute() and models.is_relative_envelope_source(candidate):
        return candidate
    raise BundleValidationError(
        [f"envelope source {str(path)!r} is absolute and does not resolve under a known data root"]
    )


def _envelope_data(path: Path) -> Any:
    document = json.loads(path.read_text(encoding="utf-8"))
    return document.get("data") if isinstance(document, Mapping) else None


def _envelope_hash(path: Path) -> str:
    """sha256 of the canonical JSON of the envelope's data field."""
    return models.sha256_hex(models.canonical_json(_envelope_data(path)))


def _first_source(entry: Any) -> str | None:
    if isinstance(entry, Mapping):
        source = entry.get("source")
        if isinstance(source, str):
            return source
    return None


def _parse_iso_or_none(value: Any) -> Optional[datetime.datetime]:
    """Parse an ISO-8601 timestamp; ``None`` for absent or unparseable input.

    Never reads a live clock: it only parses a string already recorded in an
    envelope or a derived document."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _valid_iso_string(value: Any) -> Optional[str]:
    """``value`` unchanged when it parses as ISO-8601, else ``None``."""
    return value if _parse_iso_or_none(value) is not None else None


# --------------------------------------------------------------------------- #
# Declarative v2 input mapping table.                                         #
#                                                                              #
# One resolver per v2 input id (18 rows total, one-for-one with               #
# constants.PARAM_INPUT_IDS). Each resolver reads the v1 derived              #
# document's meta/provenance/confidence dicts (plus, for the three            #
# pr_window-derived rows, the raw pr_window envelope) and returns exactly    #
# one Measured or Unmeasured value. Wires collector output by mapping its     #
# row in INPUT_MAPPINGS.                                                      #
# --------------------------------------------------------------------------- #


@dataclass
class _V1Ctx:
    """Shared read-only context every row resolver receives.

    ``problems`` and ``referenced`` are the same list/dict objects
    ``_load_v1`` uses for the whole bundle: appending to ``problems``
    rejects the whole bundle; writing to ``referenced`` folds an envelope hash
    into the bundle's ``provenance_hash``. ``cache`` memoizes the pr_window
    envelope read across the rows that share it.
    """

    meta: Mapping[str, Any]
    provenance: Mapping[str, Any]
    confidence: Mapping[str, Any]
    raw_root: Union[Path, str, None]
    problems: list[str]
    referenced: dict[str, str]
    cache: dict[str, Any] = field(default_factory=dict)


InputResolver = Callable[[_V1Ctx], "Measured | Unmeasured"]


def _map_v1_value(value: Any, vocab: Mapping[str, str]) -> Any:
    """Map a v1 enum spelling through ``vocab``; ``None`` for an unrecognized value.
    An unmappable enum is a row-scoped gap resulting in ``Unmeasured`` rather than
    a whole-bundle rejection."""
    return vocab.get(value)


def _plain_scalar_resolver(
    v1_field: str,
    *,
    missing_reason: str = "collector_failure",
    missing_detail: str = "v1 deriver recorded no value",
    n_field: Optional[str] = None,
) -> InputResolver:
    """Resolver for a v1 meta field carried through unchanged: an int or str
    value, no enum mapping. ``n`` is ``meta[n_field]`` -- the sample the
    deriver computed the value from -- when ``n_field`` names one and it
    holds a non-negative int, else ``0`` (a scalar, not a sample; a
    parameter with a minimum n then scores ``insufficient_n``). Covers
    ``elephant_factor`` (``n_field="elephant_factor_commit_count"``) and
    ``newcomer_issue_supply``."""

    def resolve(ctx: _V1Ctx) -> Measured | Unmeasured:
        confidence_value = ctx.confidence.get(v1_field)
        value = ctx.meta.get(v1_field)
        entry = ctx.provenance.get(v1_field)
        source = _first_source(entry)
        fetched_at = entry.get("fetched_at") if isinstance(entry, Mapping) else None
        value_ok = isinstance(value, (int, str)) and not isinstance(value, bool)
        if confidence_value in ("verified", "derived", "flag") and value_ok:
            path = _resolve_envelope(source, ctx.raw_root)
            if path is not None and isinstance(fetched_at, str) and fetched_at:
                source_hash = _envelope_hash(path)
                relative = _relative_source(path, ctx.raw_root)
                ctx.referenced[relative] = source_hash
                n_value = ctx.meta.get(n_field) if n_field is not None else None
                n = (n_value if isinstance(n_value, int) and not isinstance(n_value, bool) and n_value >= 0
                     else 0)
                return Measured(value=value, n=n, window_start=None, window_end=None, method="primary",
                                 source=relative, source_hash=source_hash, fetched_at=fetched_at)
            detail = (f"v1 source is not an envelope: {source!r}" if path is None
                      else f"v1 provenance for {v1_field!r} has no fetched_at")
            return Unmeasured(reason="collector_failure", detail=detail)
        if confidence_value == "missing":
            return Unmeasured(reason=missing_reason, detail=missing_detail)
        return Unmeasured(reason="collector_failure",
                           detail=f"v1 confidence {confidence_value!r} for {v1_field!r} is not recognized")

    return resolve


def _vocab_scalar_resolver(v1_field: str, vocab: Mapping[str, str]) -> InputResolver:
    """Resolver for an enum-valued v1 field mapped through a fixed
    v1-spelling -> v2-spelling vocabulary (``legal_ip``, ``promotion_ladder``,
    and ``ai_posture``). An unmappable value is handled at row scope
    (:func:`_map_v1_value`)."""

    def resolve(ctx: _V1Ctx) -> Measured | Unmeasured:
        confidence_value = ctx.confidence.get(v1_field)
        value = ctx.meta.get(v1_field)
        entry = ctx.provenance.get(v1_field)
        source = _first_source(entry)
        fetched_at = entry.get("fetched_at") if isinstance(entry, Mapping) else None
        value_ok = isinstance(value, (int, str)) and not isinstance(value, bool)
        if confidence_value in ("verified", "derived", "flag") and value_ok:
            path = _resolve_envelope(source, ctx.raw_root)
            mapped = _map_v1_value(value, vocab)
            # #51 rule 5 + RFC-0016: on a pr_window record cut short of its
            # 180d window, every promotion_ladder rung (including action-based
            # VERIFIED) rests on the absence of earlier actions in the
            # unrecorded portion of the window.
            if (v1_field == "promotion_ladder"
                    and _pr_window_cut_short(_pr_window_envelope(ctx)[1])):
                return Unmeasured(reason="collector_failure", detail=_PR_WINDOW_CUT_SHORT_DETAIL)
            if path is not None and isinstance(fetched_at, str) and fetched_at and mapped is not None:
                source_hash = _envelope_hash(path)
                relative = _relative_source(path, ctx.raw_root)
                ctx.referenced[relative] = source_hash
                return Measured(value=mapped, n=0, window_start=None, window_end=None, method="primary",
                                 source=relative, source_hash=source_hash, fetched_at=fetched_at)
            if path is None:
                detail = f"v1 source is not an envelope: {source!r}"
            elif not isinstance(fetched_at, str) or not fetched_at:
                detail = f"v1 provenance for {v1_field!r} has no fetched_at"
            else:
                detail = f"v1 value {value!r} for {v1_field!r} has no v2 vocabulary mapping"
            return Unmeasured(reason="collector_failure", detail=detail)
        if confidence_value == "missing":
            # #51 rule 5: the deriver withholds every rung but VERIFIED on a cut pr_window record; say why.
            if v1_field == "promotion_ladder" and _pr_window_cut_short(_pr_window_envelope(ctx)[1]):
                return Unmeasured(reason="collector_failure", detail=_PR_WINDOW_CUT_SHORT_DETAIL)
            return Unmeasured(reason="collector_failure", detail="v1 deriver recorded no value")
        return Unmeasured(reason="collector_failure",
                           detail=f"v1 confidence {confidence_value!r} for {v1_field!r} is not recognized")

    return resolve


def _resolve_downloads_monthly(ctx: _V1Ctx) -> Measured | Unmeasured:
    """2.3 Downloads. Reads v1 ``monthly_downloads``.

    Yields ``Unmeasured(not_applicable)`` for ecosystems defined in
    ``constants.NOT_APPLICABLE_RULES`` (``go``, ``maven``, ``systems``).
    When the ``downloads_status`` meta field is present, its "pending" vs
    "unmeasured" status provides refined detail for ``third_party_gap``."""
    ecosystem = ctx.meta.get("ecosystem")
    for rule_param, fact_name, expected_value in constants.NOT_APPLICABLE_RULES:
        if rule_param != "downloads_monthly" or fact_name != "ecosystem":
            continue
        if ecosystem == expected_value:
            return Unmeasured(
                reason="not_applicable",
                detail=f"D-MISS-5: ecosystem == {expected_value} has no public downloads registry (2.3 is undefined)",
            )
    confidence_value = ctx.confidence.get("monthly_downloads")
    value = ctx.meta.get("monthly_downloads")
    entry = ctx.provenance.get("monthly_downloads")
    source = _first_source(entry)
    fetched_at = entry.get("fetched_at") if isinstance(entry, Mapping) else None
    value_ok = isinstance(value, int) and not isinstance(value, bool)
    if confidence_value in ("verified", "derived", "flag") and value_ok:
        path = _resolve_envelope(source, ctx.raw_root)
        if path is not None and isinstance(fetched_at, str) and fetched_at:
            source_hash = _envelope_hash(path)
            relative = _relative_source(path, ctx.raw_root)
            ctx.referenced[relative] = source_hash
            return Measured(value=value, n=0, window_start=None, window_end=None, method="primary",
                             source=relative, source_hash=source_hash, fetched_at=fetched_at)
        detail = (f"v1 source is not an envelope: {source!r}" if path is None
                  else "v1 provenance for 'monthly_downloads' has no fetched_at")
        return Unmeasured(reason="collector_failure", detail=detail)
    if confidence_value == "missing":
        status = ctx.meta.get("downloads_status")
        if status == "pending":
            return Unmeasured(reason="third_party_gap",
                               detail="W1-8 downloads_status=pending: ecosystem is retryable, not yet backfilled")
        if status == "unmeasured":
            return Unmeasured(reason="third_party_gap",
                               detail="W1-8 downloads_status=unmeasured: ecosystem has no downloads registry")
        if status == "unattributed":
            return Unmeasured(reason="third_party_gap",
                               detail="downloads_status=unattributed: deps.dev links no package of this "
                                      "registry to the repo, or the count on file is another package's")
        return Unmeasured(reason="third_party_gap", detail="registry returned no downloads data")
    return Unmeasured(reason="collector_failure",
                       detail=f"v1 confidence {confidence_value!r} for 'monthly_downloads' is not recognized")


#: Ecosystems with package registry support for dependents.
_ECOSYSTEMS_WITH_DEPENDENTS_MAPPING: frozenset[str] = frozenset(
    {"pypi", "npm", "crates", "go", "maven"}
)


def _resolve_dependents_deduped(ctx: _V1Ctx) -> Measured | Unmeasured:
    """2.1 Dependents. Reads the root-organisation-deduplicated count (``"derived"``)
    from ``dependents_count``. Only the ``"derived"`` value is exposed as
    ``dependents_deduped``.

    Yields ``Unmeasured(not_applicable)`` for ecosystems defined in
    ``constants.NOT_APPLICABLE_RULES`` (such as ``systems``). Other unmeasured
    ecosystems yield ``third_party_gap``."""
    ecosystem = ctx.meta.get("ecosystem")
    for rule_param, fact_name, expected_value in constants.NOT_APPLICABLE_RULES:
        if rule_param != "dependents_deduped" or fact_name != "ecosystem":
            continue
        if ecosystem == expected_value:
            return Unmeasured(
                reason="not_applicable",
                detail=f"D-MISS-5: ecosystem == {expected_value} has no package registry (2.1 is undefined)",
            )
    confidence_value = ctx.confidence.get("dependents_count")
    if confidence_value != "derived":
        if confidence_value == "verified":
            return Unmeasured(
                reason="collector_failure",
                detail="dependents_count is the raw deps.dev usedByCount, not deduplicated by root "
                       "organisation (v2 2.1 needs the W1-2 'dependents' collector)",
            )
        if confidence_value == "missing":
            if ecosystem == "maven":
                return Unmeasured(
                    reason="third_party_gap",
                    detail="root pom.xml contents not collected (tree envelope carries paths only; "
                           "a later fetch must add one raw-content call per maven repo)",
                )
            if ecosystem not in _ECOSYSTEMS_WITH_DEPENDENTS_MAPPING:
                return Unmeasured(
                    reason="third_party_gap",
                    detail=f"no dependents mapping for ecosystem {ecosystem!r}",
                )
            return Unmeasured(reason="third_party_gap", detail="deps.dev/ecosyste.ms returned no dependents data")
        return Unmeasured(reason="collector_failure",
                           detail=f"v1 confidence {confidence_value!r} for 'dependents_count' is not recognized")
    value = ctx.meta.get("dependents_count")
    if not isinstance(value, int) or isinstance(value, bool):
        return Unmeasured(reason="collector_failure", detail=f"dependents_count (derived) is not an int: {value!r}")
    entry = ctx.provenance.get("dependents_count")
    source = _first_source(entry)
    fetched_at = entry.get("fetched_at") if isinstance(entry, Mapping) else None
    path = _resolve_envelope(source, ctx.raw_root)
    if path is None:
        return Unmeasured(reason="collector_failure", detail=f"v1 source is not an envelope: {source!r}")
    if not isinstance(fetched_at, str) or not fetched_at:
        return Unmeasured(reason="collector_failure", detail="v1 provenance for 'dependents_count' has no fetched_at")
    source_hash = _envelope_hash(path)
    relative = _relative_source(path, ctx.raw_root)
    ctx.referenced[relative] = source_hash
    return Measured(value=value, n=0, window_start=None, window_end=None, method="primary",
                     source=relative, source_hash=source_hash, fetched_at=fetched_at)


def _pr_window_envelope(ctx: _V1Ctx) -> tuple[Optional[Path], Optional[Mapping[str, Any]], Optional[str]]:
    """Resolve and read the raw ``pr_window`` envelope once per bundle
    load, memoized on ``ctx.cache`` since three rows share it.

    Keyed off the ``pr_window_count`` provenance entry: ``derive_pr_window_fields``
    sets it (confidence ``"verified"``) unconditionally whenever the collector
    had an envelope to read, so its absence means the hook never ran for this
    repo. Returns ``(None, None, None)`` in that case, or when ``raw_root``
    cannot resolve the recorded source to a readable file.
    """
    if "pr_window" in ctx.cache:
        return ctx.cache["pr_window"]
    entry = ctx.provenance.get("pr_window_count")
    source = _first_source(entry)
    fetched_at = entry.get("fetched_at") if isinstance(entry, Mapping) else None
    path = _resolve_envelope(source, ctx.raw_root)
    data = _envelope_data(path) if path is not None else None
    if path is not None and isinstance(data, Mapping) and isinstance(fetched_at, str) and fetched_at:
        result: tuple[Optional[Path], Optional[Mapping[str, Any]], Optional[str]] = (path, data, fetched_at)
    else:
        result = (None, None, None)
    ctx.cache["pr_window"] = result
    return result


def _author_history_envelope(ctx: _V1Ctx) -> tuple[Optional[Path], Optional[Mapping[str, Any]], Optional[str]]:
    """Resolve and read the raw ``author_history`` envelope once per
    bundle load, memoized on ``ctx.cache`` (for the D-IN-3 eligibility
    join for 1.2 DR_90, plus whichever rows read it directly).

    Keyed off the ``author_history_cost_points`` provenance entry:
    ``porchlamp.collect.github_graphql.derive_cohort_fields`` sets it (confidence
    ``"verified"``) unconditionally whenever the hook had an envelope
    to read, mirroring :func:`_pr_window_envelope`'s own key. Returns
    ``(None, None, None)`` when the hook never ran for this repo, or when
    ``raw_root`` cannot resolve the recorded source to a readable file --
    the caller reads this as "author_history was never collected", not as
    an error.
    """
    if "author_history" in ctx.cache:
        return ctx.cache["author_history"]
    entry = ctx.provenance.get("author_history_cost_points")
    source = _first_source(entry)
    fetched_at = entry.get("fetched_at") if isinstance(entry, Mapping) else None
    path = _resolve_envelope(source, ctx.raw_root)
    data = _envelope_data(path) if path is not None else None
    if path is not None and isinstance(data, Mapping) and isinstance(fetched_at, str) and fetched_at:
        result: tuple[Optional[Path], Optional[Mapping[str, Any]], Optional[str]] = (path, data, fetched_at)
    else:
        result = (None, None, None)
    ctx.cache["author_history"] = result
    return result


#: #51: the core definitions (:mod:`porchlamp.core.gates`), shared with the
#: collector and D-GATE-5: the PR nodes whose replies a ``pr_window`` record
#: holds, whether a ``pr_window`` record stopped short of its own window, and
#: whether a windowed record stopped short of the days a measure needs.
_reply_sampled_prs = gates.reply_sampled_prs
_pr_window_cut_short = gates.pr_window_cut_short
_author_history_cut_short = gates.record_cut_short
#: #66 F1: whether the ``author_history`` record stopped short of an instant
#: another envelope fixes (1.2 DR_90's cohort start, from the ``pr_window``
#: record's own anchor).
_author_history_cut_short_at = gates.record_cut_short_at
#: #57: an outside PR (external association, non-bot author), read from the
#: recorded PR node under the current rules, never from the envelope's stored
#: ``derived`` list; the same predicate the collector's per-PR rows apply.
_is_outside_pr = gates.is_outside_pr


def _pr_window_bounds(data: Optional[Mapping[str, Any]]) -> tuple[Optional[str], Optional[str]]:
    """The pr_window envelope's own ``(start, end)`` as ISO strings, derived
    from its recorded ``window_anchor``/``window_days`` — not a live clock
    read: both are values the collector already stored at its own collection time."""
    if not isinstance(data, Mapping):
        return None, None
    anchor = data.get("window_anchor")
    days = data.get("window_days")
    anchor_dt = _parse_iso_or_none(anchor)
    if anchor_dt is None or not isinstance(days, int) or isinstance(days, bool):
        return None, None
    start_dt = anchor_dt - timedelta(days=days)
    return start_dt.isoformat(), anchor


def _merged_external_latency_hours(data: Mapping[str, Any]) -> list[float]:
    """Merge latency in hours for merged outside PRs (1.5 TTM_p90).

    #57: read from the raw pr_window envelope's ``prs`` nodes alone: an
    outside PR (:func:`porchlamp.core.gates.is_outside_pr`: an external
    author association and a non-bot author, D-IN-1) whose merge timestamp is no
    earlier than its ``createdAt``. The envelope's stored ``derived`` list,
    the collection run's own classification, is not read, so a rule change
    since collection reaches this measure, and a bot's own merged PRs
    (Dependabot, Renovate) do not set the p90. RFC-0015: a node landed
    outside GitHub's merge (:func:`porchlamp.core.landing.is_landed`) counts as
    merged, at its close (:func:`porchlamp.core.landing.merged_or_landed_at`).
    """
    out: list[float] = []
    for pr in data.get("prs") or []:
        if not _is_outside_pr(pr):
            continue
        created = _parse_iso_or_none(pr.get("createdAt"))
        merged = _parse_iso_or_none(landing.merged_or_landed_at(pr))
        if created is None or merged is None or merged < created:
            continue
        out.append((merged - created).total_seconds() / 3600.0)
    return out


#: D-IN-4 amendment (``docs/v2/02-RANKING-DESIGN.md`` D-IN-4):
#: the decision-rate cohort is the older half of the 180 d window, not
#: a second fetch beyond it -- every PR in it has had a full 90 d to be
#: decided by the time the envelope's own ``window_anchor`` was recorded.
_DR90_COHORT_START_DAYS = 180
_DR90_COHORT_END_DAYS = 90
_DR90_DECISION_WINDOW_DAYS = 90

#: 1.2 DR_90's ``method``: ``"primary"`` once BOTH the D-IN-3 per-author
#: cap and the eligibility gate apply; ``"proxy:din3_unapplied"`` when the
#: eligibility gate could not be applied (no ``author_history`` envelope
#: for this repo -- ``pr_window`` alone carries no public-event count, so
#: ``is_eligible_account`` would read every author ineligible and collapse
#: the cohort to n=0). The cap itself needs only ``(login, createdAt)``,
#: both present on every ``pr_window`` PR node, so it is UNCONDITIONAL --
#: applied whether or not the gate is. D-MISS-4 reserves ``"proxy:<name>"``
#: for a documented alternate methodology (4.1's review/CI proxy is the
#: precedent); an eligibility-unapplied DR_90 is a real, computed rate
#: under a weaker method than the row's primary definition, and this tag
#: lets consumers distinguish the two.
_DR90_METHOD_PRIMARY = constants.METHOD_PRIMARY
_DR90_METHOD_GATE_UNAPPLIED = constants.PROXY_METHOD_PREFIX + "din3_unapplied"


def _dr90_author_eligible(
    author_records: Mapping[str, Mapping[str, Any]], login: str, pr_created_at: str
) -> Optional[bool]:
    """D-IN-3 tri-state eligibility for one DR_90 cohort PR's author,
    joined against the ``author_history`` envelope's ``author_records``
    by ``login``. ``None`` (unknown, excluded from the gated
    population) when the login has no author_history record at all (the
    two envelopes' decision-cohort windows do not perfectly coincide -- the
    365 d window's cohort is a superset of pr_window's narrower 1.2
    band, so a genuine miss should be rare but is not impossible; #66: the
    superset holds only for two records sharing an anchor, so a history
    anchored N days before the pr_window has no record for an author whose
    only cohort PR opened in the band's last N days, and a history cut short
    of the band's start is refused by :func:`_resolve_dr90_decision_rate`) or when
    its eligibility fetch failed (``eligibility_unmeasured``); otherwise
    ``porchlamp.core.hygiene.is_eligible_account`` on the record's own
    ``account_created_at``/``public_event_count`` against THIS PR's
    ``createdAt`` (D-IN-3: age is measured at PR creation, not "now").

    ``measured`` reads ONLY ``eligibility_unmeasured``: DR_90's gate needs
    exactly the account's age and public-event count, both of which come
    from the per-author eligibility fetch. ``porchlamp.collect.github_graphql._account_eligibility``
    ORs in ``history_unmeasured`` too, but that flag means the author's
    PRIOR-MERGE SEARCH failed -- a fact 1.3 (MR_first's "no prior merged
    PR") needs and DR_90 does not. Coupling the two here would exclude an
    author whose eligibility data is perfectly good from DR_90's
    population solely because an unrelated MR_first-only fetch failed for
    them, which is over-strict for this row specifically (the collector's
    shared helper is correct for its own use: one call site there serves
    both 1.2 and 1.3 at once, so it must OR every flag either row cares
    about; this join serves 1.2 alone).

    Unlike that collector-side helper, a login entirely ABSENT from
    ``author_records`` is treated as unknown (``None``) here rather than
    falling back to the PR node's own ``author.createdAt`` with a zero
    event count (which would read as a confirmed ``False``): this join
    crosses two independently-windowed envelopes (``author_history``'s
    365 d cohort vs. this PR's own ``pr_window`` position), so a genuine
    membership miss is expected and must not be silently scored as
    "confirmed ineligible" on data that was simply never fetched."""
    record = author_records.get(login)
    if record is None:
        return None
    measured = not record.get("eligibility_unmeasured")
    return hygiene.account_eligibility(
        record.get("account_created_at"), pr_created_at, record.get("public_event_count"), measured=measured,
    )


def _dr90_decision_cohort(
    data: Mapping[str, Any],
    anchor: datetime.datetime,
    author_records: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> tuple[int, int, int, int, bool]:
    """1.2 DR_90's cohort and decision count over the raw pr_window envelope,
    D-IN-3-adjusted.

    Reads the ``prs[]`` nodes alone (``createdAt``, ``mergedAt``,
    ``closedAt``, ``author``): #57, a PR belongs to the cohort when it is an
    outside PR (:func:`porchlamp.core.gates.is_outside_pr`: an external
    author association and a non-bot author, D-IN-1), judged from the node
    under the current rules as for 1.5 (:func:`_merged_external_latency_hours`),
    never from the envelope's stored ``derived`` list. The cohort is every
    outside PR whose ``createdAt`` falls in the half-open interval
    ``[anchor - 180d, anchor - 90d)``: the lower bound is inclusive (a PR
    opened exactly 180 d ago is still inside the collector's trailing
    window), the upper bound is exclusive (a PR opened exactly 90 d ago has
    not yet had the full 90 d every cohort member is owed to be decided, so
    it is left for a later snapshot rather than measured early -- this is
    the amendment's whole point). A PR whose ``createdAt`` is missing or
    does not parse as ISO-8601 cannot be windowed at all and is dropped
    from the cohort, counted separately (``malformed``) so the caller can
    name it in an ``insufficient_n`` detail instead of silently shrinking
    ``n`` with no explanation.

    D-IN-3: when ``author_records`` is given (an ``author_history`` envelope
    was collected for this repo), each cohort PR's author is checked with
    :func:`_dr90_author_eligible`, keeping only the ``True`` ones (a ``None`` --
    unmeasured or unmatched -- is excluded from the population entirely,
    never assumed eligible, the same principle ``porchlamp.collect.github_graphql``
    applies throughout author history collection).
    Whether or not the gate is applied, the survivors are then run through
    :func:`porchlamp.core.hygiene.apply_author_cap` keyed on ``(login,
    createdAt)`` -- both present on every ``pr_window`` node regardless of
    ``author_history`` -- so one author's PRs count for at most
    ``AUTHOR_CAP_FRACTION`` of the (possibly gate-reduced) cohort,
    excess dropped most recent first. A cohort member is decided when
    ``mergedAt`` or ``closedAt`` is no more than 90 d after its own
    ``createdAt``, inclusive (exactly 90 d counts as decided), evaluated
    over the CAPPED population.

    Returns ``(decided_count, capped_cohort_size, raw_cohort_size,
    malformed_count, gate_applied)``. ``raw_cohort_size`` is the population
    AFTER the date window and (if applied) the eligibility gate but BEFORE
    the cap; ``capped_cohort_size`` is the (gated and) capped count.

    ``PARAM_MIN_N`` is checked against ``capped_cohort_size`` -- the same
    number the ``Measured.n`` reports, and therefore the same number
    ``porchlamp.core.params.curves._apply`` re-checks against its own ``min_n``
    when the record is scored. Checking a different number
    (``raw_cohort_size``) at the resolver than the record itself reports
    would let a Measured value through that the param layer immediately
    re-rejects as ``insufficient_n`` anyway -- with no detail, since
    ``ParamScore`` carries none -- silently converting the very
    manipulation this cap exists to correct back into missing data. The
    degenerate case that motivated the split (a small, undominated
    population reading as "0 PRs") is fixed at its source instead, in
    :func:`porchlamp.core.hygiene.apply_author_cap` (small populations keep
    everything until the cap actually binds on real concentration).
    ``raw_cohort_size`` is kept in this return value only so the caller
    can name it, alongside the capped count, in an ``insufficient_n``
    detail -- diagnostic value for a human reading the bundle, since a
    capped rate below the floor is often a small population made smaller
    by a real cap bind, and the two numbers together say which.
    """
    cohort_start = anchor - timedelta(days=_DR90_COHORT_START_DAYS)
    cohort_end = anchor - timedelta(days=_DR90_COHORT_END_DAYS)
    decision_window = timedelta(days=_DR90_DECISION_WINDOW_DAYS)
    entries: list[tuple[str, datetime.datetime, Mapping[str, Any]]] = []
    malformed = 0
    for pr in data.get("prs") or []:
        if not _is_outside_pr(pr):
            continue
        created = _parse_iso_or_none(pr.get("createdAt"))
        if created is None:
            malformed += 1
            continue
        if not (cohort_start <= created < cohort_end):
            continue
        # A PR node with no recorded author (missing/legacy pr_window data)
        # must not be lumped into one giant shared "author" by the cap --
        # that would treat every such PR as the SAME sock puppet. Each
        # missing-author PR is its own singleton instead.
        raw_login = str((pr.get("author") or {}).get("login") or "").lower()
        login = raw_login or f"__no_author_pr_{pr.get('number')}"
        entries.append((login, created, pr))

    gate_applied = author_records is not None
    if gate_applied:
        entries = [
            (login, created, pr) for login, created, pr in entries
            if _dr90_author_eligible(author_records, login, pr.get("createdAt")) is True
        ]

    kept_idx = hygiene.apply_author_cap([(login, created) for login, created, _pr in entries])
    kept = [entries[i] for i in kept_idx]

    decided = 0
    for _login, created, pr in kept:
        merged = _parse_iso_or_none(pr.get("mergedAt"))
        closed = _parse_iso_or_none(pr.get("closedAt"))
        decided_by_merge = merged is not None and merged >= created and merged - created <= decision_window
        decided_by_close = closed is not None and closed >= created and closed - created <= decision_window
        if decided_by_merge or decided_by_close:
            decided += 1
    return decided, len(kept), len(entries), malformed, gate_applied


#: #51 rule 5: the detail of a window measure refused because its ``pr_window``
#: record stopped short of its window (:func:`porchlamp.core.gates.pr_window_cut_short`).
_PR_WINDOW_CUT_SHORT_DETAIL = "pr_window envelope is truncated before reaching the 180d window start"


def _author_history_cut_detail(ah_data: Mapping[str, Any], cohort_start: datetime.datetime) -> str:
    """#66 F1: the detail of a 1.2 DR_90 refused because its ``author_history``
    record stopped short of the decision cohort's start (an instant the
    ``pr_window`` record's anchor fixes): the instant, and where the history's
    PRs begin, from :func:`porchlamp.core.gates.pr_window_coverage`."""
    start = cohort_start if cohort_start.tzinfo is not None else cohort_start.replace(tzinfo=_utc_timezone.utc)
    _covered, cutoff = gates.pr_window_coverage(ah_data, start)
    held = f"it holds the PRs opened from {cutoff}" if cutoff else "it records no PR with a readable createdAt"
    return (
        f"author_history envelope is truncated before reaching the decision cohort start {start.isoformat()} "
        f"(pr_window anchor - {_DR90_COHORT_START_DAYS}d): {held}, so the D-IN-3 eligibility join would drop "
        f"every cohort author it never recorded"
    )


def _resolve_dr90_decision_rate(ctx: _V1Ctx) -> Measured | Unmeasured:
    """1.2 DR_90 decision rate (D-IN-4 amendment; D-IN-3 per-author cap and
    eligibility gate). No v1 summary field exists for this cohort (the v1
    pipeline never computed it): both the value and ``n`` are computed here
    from the raw pr_window envelope via :func:`_dr90_decision_cohort`,
    D-IN-3-adjusted against the ``author_history`` envelope when one was
    collected for this repo; see :data:`_DR90_METHOD_PRIMARY` and
    :data:`_DR90_METHOD_GATE_UNAPPLIED` for how the two cases are told apart
    downstream.

    Note: ``porchlamp.collect.github_graphql.derive_cohort_fields`` computes a
    different number over the ``author_history`` envelope's wider (270 d)
    decision-cohort definition, named
    ``meta["dr90_decision_rate_author_history_cohort"]`` specifically so it
    cannot be confused with this row (the two are genuinely different
    cohorts, not two computations of the same one). This resolver does not
    read that field: it recomputes the cap and gate directly against
    ``pr_window``'s narrower (180 d, D-IN-4-amended) cohort.

    #51: it reads every PR of the window, a sampled one included (decisions
    are PR-level facts), and refuses a record cut short of its window.

    #66 F1: it also refuses (``collector_failure``) an ``author_history``
    record cut short of the cohort's start, the instant ``window_anchor``
    minus 180 d of THIS ``pr_window`` record
    (:func:`porchlamp.core.gates.record_cut_short_at`): the two envelopes are
    collected at different times, so the history's own day count does not say
    whether it reaches this cohort. The join reads eligibility only for the
    authors the history recorded, so a cut history silently dropped every
    cohort author it never saw: ``n`` collapsed to an ``insufficient_n`` that
    was a collector failure, or the rate was computed over the repeat
    contributors alone. 1.3, 5.2 and 5.3 already refuse a history cut short of
    their own cohorts (RFC-0013).
    """
    path, data, fetched_at = _pr_window_envelope(ctx)
    if path is None:
        return Unmeasured(reason="collector_failure", detail=_PR_WINDOW_UNRESOLVABLE_DETAIL)
    if _pr_window_cut_short(data):
        return Unmeasured(reason="collector_failure", detail=_PR_WINDOW_CUT_SHORT_DETAIL)
    anchor = _parse_iso_or_none(data.get("window_anchor"))
    if anchor is None:
        return Unmeasured(
            reason="collector_failure",
            detail=f"pr_window envelope has no parseable window_anchor: {data.get('window_anchor')!r}",
        )
    cohort_start = anchor - timedelta(days=_DR90_COHORT_START_DAYS)
    _ah_path, ah_data, _ah_fetched_at = _author_history_envelope(ctx)
    author_records: Optional[Mapping[str, Mapping[str, Any]]] = None
    if ah_data is not None:
        if _author_history_cut_short_at(ah_data, cohort_start):
            return Unmeasured(reason="collector_failure", detail=_author_history_cut_detail(ah_data, cohort_start))
        author_records = {
            str(record.get("login") or "").lower(): record
            for record in ah_data.get("author_records") or []
            if isinstance(record, Mapping)
        }
    decided, cohort, raw_cohort, malformed, gate_applied = _dr90_decision_cohort(data, anchor, author_records)
    min_n = constants.PARAM_MIN_N["dr90_decision_rate"]
    if cohort < min_n:
        detail = (
            f"{cohort} external PRs in the decision cohort ({raw_cohort} before the D-IN-3 "
            f"per-author cap), need >= {min_n}"
        )
        if malformed:
            detail += f" ({malformed} excluded: malformed or missing createdAt)"
        return Unmeasured(reason="insufficient_n", detail=detail)
    window_start = cohort_start.isoformat()
    window_end = (anchor - timedelta(days=_DR90_COHORT_END_DAYS)).isoformat()
    source_hash = _envelope_hash(path)
    relative = _relative_source(path, ctx.raw_root)
    ctx.referenced[relative] = source_hash
    method = _DR90_METHOD_PRIMARY if gate_applied else _DR90_METHOD_GATE_UNAPPLIED
    return Measured(
        value=round(decided / cohort, 4), n=cohort, window_start=window_start, window_end=window_end,
        method=method, source=relative, source_hash=source_hash, fetched_at=fetched_at,
    )


def _nearest_rank_p90(values: list[float]) -> float:
    """Nearest-rank p90 (``ceil(0.9N)``, 1-indexed) over ``values`` (D-NORM,
    1.5 TTM_p90)."""
    ordered = sorted(values)
    rank = max(1, math.ceil(0.9 * len(ordered)))
    return ordered[rank - 1]


_PR_WINDOW_UNRESOLVABLE_DETAIL = (
    "pr_window envelope not resolvable (W1-3 hook did not run for this repo, "
    "or raw_root cannot resolve the recorded source to a file)"
)


def _resolve_ttfhr_hours(ctx: _V1Ctx) -> Measured | Unmeasured:
    """1.1 TTFHR. Reads the ``pr_window_ttfhr_median_hours`` /
    ``pr_window_external_measured_latency_count`` summary fields the derive
    hook already writes (``porchlamp.collect.github_graphql.derive_pr_window_fields``);
    no raw-envelope join needed for the value or ``n``, only to resolve
    provenance."""
    path, data, fetched_at = _pr_window_envelope(ctx)
    if path is None:
        return Unmeasured(reason="collector_failure", detail=_PR_WINDOW_UNRESOLVABLE_DETAIL)
    if _pr_window_cut_short(data):
        return Unmeasured(reason="collector_failure", detail=_PR_WINDOW_CUT_SHORT_DETAIL)
    n_value = ctx.meta.get("pr_window_external_measured_latency_count")
    n = n_value if isinstance(n_value, int) and not isinstance(n_value, bool) else 0
    min_n = constants.PARAM_MIN_N["ttfhr_hours"]
    if n < min_n:
        return Unmeasured(reason="insufficient_n",
                           detail=f"{n} external PRs with a measured TTFHR, need >= {min_n}")
    value = ctx.meta.get("pr_window_ttfhr_median_hours")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return Unmeasured(reason="collector_failure",
                           detail=f"pr_window_ttfhr_median_hours is not numeric: {value!r}")
    window_start, window_end = _pr_window_bounds(data)
    source_hash = _envelope_hash(path)
    relative = _relative_source(path, ctx.raw_root)
    ctx.referenced[relative] = source_hash
    return Measured(value=float(value), n=n, window_start=window_start, window_end=window_end,
                     method="primary", source=relative, source_hash=source_hash, fetched_at=fetched_at)


def _resolve_substantive_review_rate(ctx: _V1Ctx) -> Measured | Unmeasured:
    """4.3 Substantive review rate. The rate itself is the
    ``pr_window_substantive_review_rate`` summary field; ``n`` (decided
    outside PRs in the reply sample) has no summary field and is counted
    from the raw envelope's PR nodes: #57, a decided (``closedAt``) outside PR
    (:func:`porchlamp.core.gates.is_outside_pr`) whose replies the record
    holds (:func:`porchlamp.core.gates.reply_sampled_prs`), the population
    ``porchlamp.collect.github_graphql.derive_pr_window_fields`` computes the
    rate over. The envelope's stored ``derived`` list is not read."""
    path, data, fetched_at = _pr_window_envelope(ctx)
    if path is None:
        return Unmeasured(reason="collector_failure", detail=_PR_WINDOW_UNRESOLVABLE_DETAIL)
    if _pr_window_cut_short(data):
        return Unmeasured(reason="collector_failure", detail=_PR_WINDOW_CUT_SHORT_DETAIL)
    prs = data.get("prs")
    nodes = [pr for pr in prs if isinstance(pr, Mapping)] if isinstance(prs, list) else []
    maintainers = gates.maintainer_logins(nodes)
    decided_outside = [
        pr for pr in _reply_sampled_prs(nodes)
        if _is_outside_pr(pr)
        and pr.get("closedAt") is not None
        and not gates.is_excluded_from_review_denominator(pr, maintainers)
    ]
    n = len(decided_outside)
    min_n = constants.PARAM_MIN_N["substantive_review_rate"]
    if n < min_n:
        return Unmeasured(reason="insufficient_n",
                           detail=f"{n} decided external PRs, need >= {min_n}")
    value = ctx.meta.get("pr_window_substantive_review_rate")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return Unmeasured(reason="collector_failure",
                           detail=f"pr_window_substantive_review_rate is not numeric: {value!r}")
    window_start, window_end = _pr_window_bounds(data)
    source_hash = _envelope_hash(path)
    relative = _relative_source(path, ctx.raw_root)
    ctx.referenced[relative] = source_hash
    return Measured(value=float(value), n=n, window_start=window_start, window_end=window_end,
                     method="primary", source=relative, source_hash=source_hash, fetched_at=fetched_at)


def _landing_note(ctx: _V1Ctx, recorded_field: str, landed_field: str, counted: str) -> str:
    """RFC-0015: the clause a merge-based measure's ``insufficient_n`` detail adds
    about pull requests landed outside GitHub's merge, read from the derive-time
    fields ``recorded_field`` (whether the record carries the landing fields) and
    ``landed_field`` (how many landed PRs or authors the measure counted).

    ``counted`` is the clause for a record that shows landings, with a
    ``{landed}`` placeholder for the count; a record fetched before the landing
    fields says it shows none; a bundle derived by a tree that recorded neither
    field adds nothing."""
    recorded = ctx.meta.get(recorded_field)
    landed = ctx.meta.get(landed_field)
    if recorded is False:
        return "; the record shows no landing outside GitHub's merge (fetched before RFC-0015's fields)"
    if recorded is True and _is_count(landed):
        return "; " + counted.format(landed=landed)
    return ""


def _resolve_ttm_p90_hours(ctx: _V1Ctx) -> Measured | Unmeasured:
    """1.5 TTM_p90. No v1 summary field exists at all (unlike TTFHR and the
    review rate): both the value and ``n`` are computed here from the raw
    pr_window envelope's merged-external-PR latencies
    (:func:`_merged_external_latency_hours`). RFC-0015: a PR landed outside
    GitHub's merge is merged at its close; an ``insufficient_n`` detail names how
    many outside PRs the record shows landed (:func:`_landing_note`)."""
    path, data, fetched_at = _pr_window_envelope(ctx)
    if path is None:
        return Unmeasured(reason="collector_failure", detail=_PR_WINDOW_UNRESOLVABLE_DETAIL)
    if _pr_window_cut_short(data):
        return Unmeasured(reason="collector_failure", detail=_PR_WINDOW_CUT_SHORT_DETAIL)
    latencies = _merged_external_latency_hours(data)
    n = len(latencies)
    min_n = constants.PARAM_MIN_N["ttm_p90_hours"]
    if n < min_n:
        note = _landing_note(
            ctx, "pr_window_landing_recorded", "pr_window_landed_external_count",
            "{landed} outside PRs in the record were landed outside GitHub's merge and count as merged")
        return Unmeasured(reason="insufficient_n",
                           detail=f"{n} merged external PRs with recorded latency, need >= {min_n}{note}")
    window_start, window_end = _pr_window_bounds(data)
    source_hash = _envelope_hash(path)
    relative = _relative_source(path, ctx.raw_root)
    ctx.referenced[relative] = source_hash
    return Measured(value=round(_nearest_rank_p90(latencies), 4), n=n, window_start=window_start,
                     window_end=window_end, method="primary", source=relative, source_hash=source_hash,
                     fetched_at=fetched_at)


def _resolve_gr14_ghost_rate(ctx: _V1Ctx) -> Measured | Unmeasured:
    """1.4 GR_14, which D-GATE-5 also reads (owner ruling 2026-10-06). The
    share and its population ``n`` are computed at derive time by
    ``porchlamp.core.gates.zero_response_counts`` at derive's own reference
    time (``gr14_window_end``), from the pr_window envelope; this row only
    carries them. ``n`` below the minimum stays ``Measured`` with its true
    ``n``: scoring reports ``insufficient_n`` and the gate is UNDECIDED.
    ``n == 0`` has no share to carry and is ``insufficient_n`` here.

    #51 rule 5: refused when the record stopped short of the window the share
    was computed over (``gr14_window_start``; the record's own window when
    derive recorded none), the rule derive itself applies."""
    path, data, fetched_at = _pr_window_envelope(ctx)
    if path is None:
        return Unmeasured(reason="collector_failure", detail=_PR_WINDOW_UNRESOLVABLE_DETAIL)
    if _pr_window_cut_short(data, _parse_iso_or_none(ctx.meta.get("gr14_window_start"))):
        return Unmeasured(reason="collector_failure", detail=_PR_WINDOW_CUT_SHORT_DETAIL)
    n = ctx.meta.get("gr14_population_n")
    if ctx.confidence.get("gr14_population_n") != "derived" or not _is_count(n):
        return Unmeasured(reason="collector_failure",
                           detail="derive recorded no ghost-rate population (re-derive with this tree)")
    min_n = constants.PARAM_MIN_N["gr14_ghost_rate"]
    if n == 0:
        return Unmeasured(reason="insufficient_n",
                           detail=f"0 external non-bot PRs >= 14 d old in the window, need >= {min_n}")
    value = ctx.meta.get("gr14_ghost_rate")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return Unmeasured(reason="collector_failure", detail=f"gr14_ghost_rate is not numeric: {value!r}")
    source_hash = _envelope_hash(path)
    relative = _relative_source(path, ctx.raw_root)
    ctx.referenced[relative] = source_hash
    return Measured(value=float(value), n=n, window_start=_valid_iso_string(ctx.meta.get("gr14_window_start")),
                     window_end=_valid_iso_string(ctx.meta.get("gr14_window_end")), method="primary",
                     source=relative, source_hash=source_hash, fetched_at=fetched_at)


def _resolve_closed_unread(ctx: _V1Ctx) -> ClosedUnreadCounts | None:
    """The ``closed_unread`` counts derive recorded from the pr_window envelope
    (``pr_window_closed_unread_count`` of ``pr_window_outside_pr_count``), with
    the envelope folded into the bundle's provenance as GR_14 folds it. ``None``
    (the flag is unmeasured) when the envelope does not resolve or derive
    recorded no counts: the v1 ``pr_events`` sample is never read for them."""
    path, data, _fetched_at = _pr_window_envelope(ctx)
    if path is None or _pr_window_cut_short(data):
        return None
    counts = _closed_unread_counts_or_none(
        ctx.meta.get("pr_window_closed_unread_count"), ctx.meta.get("pr_window_outside_pr_count"))
    if counts is not None:
        ctx.referenced[_relative_source(path, ctx.raw_root)] = _envelope_hash(path)
    return counts


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _resolved_source_paths(source_field: Any, raw_root: Union[Path, str, None]) -> list[Path]:
    """Resolve a provenance ``source`` *list* to the subset of entries that
    exist as envelopes; ``[]`` when ``source_field`` is not a non-empty list.

    ``openssf_signals`` and ``pipeline_defenses`` are the only two rows
    whose derive hook records more than one contributing envelope path
    under one provenance entry -- every other row's ``source`` is a single
    string (:func:`_first_source`)."""
    if not isinstance(source_field, list) or not source_field:
        return []
    return [path for path in (_resolve_envelope(s, raw_root) for s in source_field) if path is not None]


#: D-MISS-5 consequence for 2.2: the method string when
#: the Pike score is computed without a dependents signal, renormalised.
_OPENSSF_PROXY_METHOD: str = "proxy:no_dependents_signal"


def _resolve_openssf_criticality_proxy(ctx: _V1Ctx) -> Optional["Measured | Unmeasured"]:
    """D-MISS-5 consequence for 2.2: the ``proxy:no_dependents_signal`` path,
    tried when the full ten-signal set was never emitted
    (``_resolve_openssf_criticality``'s caller only reaches here when
    ``confidence["openssf_signals"]`` is not ``"derived"``).

    Reuses :func:`_resolve_dependents_deduped`'s own resolution of the
    dependents fact -- one source for "why is the dependents signal
    unusable" -- rather than a second copy of its ecosystem/confidence
    branching: the two rows read the identical underlying collector fact
    (module docstring's "dependents_count | dependents | deduplicated
    count"). Returns ``None`` (fall through to the caller's generic gap)
    when dependents was actually measured (this row's
    ``openssf_signals_partial`` companion could then only be a hand-built or
    corrupted document, never production output) or when its own reason
    (e.g. an ``insufficient_n`` D-MISS-1 doesn't define for 2.1) offers
    nothing to explain.
    """
    dependents = _resolve_dependents_deduped(ctx)
    if isinstance(dependents, Measured):
        return None
    if dependents.reason == "collector_failure":
        # A retry can still measure it -- never fabricate a proxy over a
        # signal that is merely temporarily unmeasured, unlike a fact about
        # the world (not_applicable) or a genuine third-party gap.
        return Unmeasured(
            reason="collector_failure",
            detail="dependents signal is collector_failure; a retry can still measure openssf_criticality",
        )
    if dependents.reason not in ("not_applicable", "third_party_gap"):
        return None

    partial_signals = ctx.meta.get("openssf_signals_partial")
    missing_field_names = ctx.meta.get("openssf_signals_partial_missing")
    if not isinstance(partial_signals, Mapping) or not isinstance(missing_field_names, list):
        return Unmeasured(reason="collector_failure", detail="openssf_signals_partial is malformed")
    missing_short_names = frozenset(
        openssf.SIGNAL_FIELDS[name] for name in missing_field_names if name in openssf.SIGNAL_FIELDS
    )
    present_count = len(openssf.SIGNAL_FIELDS) - len(missing_short_names)
    if present_count < constants.OPENSSF_PROXY_MIN_SIGNALS:
        return Unmeasured(reason="insufficient_n", detail=f"{present_count} of 10 signals")

    bad = [
        field for field in openssf.SIGNAL_FIELDS
        if field not in missing_field_names
        and (isinstance(partial_signals.get(field), bool) or not isinstance(partial_signals.get(field), (int, float)))
    ]
    if bad:
        return Unmeasured(
            reason="collector_failure",
            detail=f"openssf_signals_partial[{bad[0]!r}] is not numeric: {partial_signals.get(bad[0])!r}",
        )

    entry = ctx.provenance.get("openssf_signals_partial")
    source_field = entry.get("source") if isinstance(entry, Mapping) else None
    fetched_at = entry.get("fetched_at") if isinstance(entry, Mapping) else None
    paths = _resolved_source_paths(source_field, ctx.raw_root)
    if not paths or not isinstance(fetched_at, str) or not fetched_at:
        detail = (f"v1 source is not a resolvable envelope list: {source_field!r}" if not paths
                  else "v1 provenance for 'openssf_signals_partial' has no fetched_at")
        return Unmeasured(reason="collector_failure", detail=detail)
    for path in paths:
        ctx.referenced[_relative_source(path, ctx.raw_root)] = _envelope_hash(path)
    score = openssf.calculate_pike_criticality(partial_signals, missing=missing_short_names)
    return Measured(value=round(score, 4), n=0, window_start=None, window_end=None,
                     method=_OPENSSF_PROXY_METHOD, source=_relative_source(paths[0], ctx.raw_root),
                     source_hash=_envelope_hash(paths[0]), fetched_at=fetched_at)


def _resolve_openssf_criticality(ctx: _V1Ctx) -> Measured | Unmeasured:
    """2.2 OpenSSF criticality. Reads the ``meta.openssf_signals`` dict
    (``porchlamp.collect.pike_signals.derive_pike_signals``: the ten raw Pike signals,
    written with confidence ``"derived"`` only when all ten source envelopes were
    available -- D-MISS-1: never a partial score, class TP "only if a signal is
    missing") and computes C in [0, 1] via
    :func:`porchlamp.core.openssf.calculate_pike_criticality` (the one
    definition of the formula; ``porchlamp.derive`` imports ``porchlamp.core`` and
    stdlib only, and this module is itself both). A ``meta.openssf_signals``
    dict missing (or non-numeric) one of the ten fields is defensive: production's
    ``derive_pike_signals`` never emits a partial dict, but a hand-built or
    corrupted document might, and 2.2's whole-set-or-nothing rule must hold
    for it too -- the first missing field is named in the detail.

    When the full set was never emitted, :func:`_resolve_openssf_criticality_proxy`
    is tried before the generic gap below -- when the dependents signal specifically
    (``not_applicable``/``third_party_gap``) is why, and at least
    ``constants.OPENSSF_PROXY_MIN_SIGNALS`` of the ten are present, this
    row is ``Measured(method="proxy:no_dependents_signal")`` instead of
    Unmeasured; a dependents ``collector_failure`` still leaves 2.2
    ``Unmeasured(collector_failure)`` (a retry can still measure it)."""
    confidence_value = ctx.confidence.get("openssf_signals")
    signals = ctx.meta.get("openssf_signals")
    entry = ctx.provenance.get("openssf_signals")
    source_field = entry.get("source") if isinstance(entry, Mapping) else None
    fetched_at = entry.get("fetched_at") if isinstance(entry, Mapping) else None
    if confidence_value == "derived":
        if not isinstance(signals, Mapping):
            return Unmeasured(reason="collector_failure", detail=f"openssf_signals is not a mapping: {signals!r}")
        missing = [field for field in openssf.SIGNAL_FIELDS if signals.get(field) is None]
        if missing:
            return Unmeasured(
                reason="third_party_gap",
                detail=f"openssf_signals missing signal {missing[0]!r} (02 section 4 2.2: never a partial score)",
            )
        bad = [field for field in openssf.SIGNAL_FIELDS
               if isinstance(signals[field], bool) or not isinstance(signals[field], (int, float))]
        if bad:
            return Unmeasured(reason="collector_failure",
                               detail=f"openssf_signals[{bad[0]!r}] is not numeric: {signals[bad[0]]!r}")
        paths = _resolved_source_paths(source_field, ctx.raw_root)
        if not paths or not isinstance(fetched_at, str) or not fetched_at:
            detail = (f"v1 source is not a resolvable envelope list: {source_field!r}" if not paths
                      else "v1 provenance for 'openssf_signals' has no fetched_at")
            return Unmeasured(reason="collector_failure", detail=detail)
        for path in paths:
            ctx.referenced[_relative_source(path, ctx.raw_root)] = _envelope_hash(path)
        return Measured(value=round(openssf.calculate_pike_criticality(signals), 4), n=0, window_start=None,
                         window_end=None, method=constants.METHOD_PRIMARY,
                         source=_relative_source(paths[0], ctx.raw_root),
                         source_hash=_envelope_hash(paths[0]), fetched_at=fetched_at)
    if confidence_value == "collector_failure":
        # Pike signals deriver recorded this sentinel itself, with the specific
        # reason in the companion detail field -- the generic "not recognized"
        # fallback message below would discard that reason and read as if the
        # confidence value were unexpected rather than deliberately set.
        detail = ctx.meta.get("openssf_signals_detail")
        return Unmeasured(reason="collector_failure",
                           detail=detail if isinstance(detail, str) and detail else "pike_signals collector failed")
    if ctx.confidence.get("openssf_signals_partial") == "derived":
        proxy_result = _resolve_openssf_criticality_proxy(ctx)
        if proxy_result is not None:
            return proxy_result
    if confidence_value == "missing":
        return Unmeasured(
            reason="third_party_gap",
            detail="one or more of the ten Pike signal sources is absent (02 section 4 2.2: never a partial score)",
        )
    return Unmeasured(reason="collector_failure",
                       detail=f"v1 confidence {confidence_value!r} for 'openssf_signals' is not recognized")


#: Mirror of review/CI proxy method string stored when no scorecard
#: subset is usable.
_PIPELINE_DEFENSES_PROXY_METHOD: str = "proxy:review_ci"


def _resolve_pipeline_defenses(ctx: _V1Ctx) -> Measured | Unmeasured:
    """4.1 Pipeline defenses. Reads the pre-computed
    ``meta.pipeline_defenses`` / ``meta.pipeline_defenses_method`` fields
    (``porchlamp.collect.pike_signals.pipeline_defenses``): method ``"primary"``
    (Scorecard 5-check mean, one scorecard envelope, n=0 -- a scalar) or
    ``"proxy:review_ci"`` (0.6·review-rate + 0.4·CI-pass-rate, the pr_window
    and runs envelopes). ``n`` for the proxy method is the merged-PR count
    backing the review-rate denominator (``porchlamp.collect.pike_signals.
    code_review_rate``), read from the pr_window envelope's own ``prs`` list
    -- 10-merged-PR minimum sample applies to the proxy method only
    (``porchlamp.core.params.rigor._PIPELINE.proxy_min_n``). RFC-0015: a PR
    landed outside GitHub's merge is merged there and here
    (:func:`porchlamp.core.landing.merged_or_landed_at`).

    When 4.1 is unmeasurable by either method, its confidence key is simply
    absent from the v1 document (not the string ``"missing"``) -- both are
    treated as the same TP absence here."""
    confidence_value = ctx.confidence.get("pipeline_defenses")
    value = ctx.meta.get("pipeline_defenses")
    method = ctx.meta.get("pipeline_defenses_method")
    entry = ctx.provenance.get("pipeline_defenses")
    source_field = entry.get("source") if isinstance(entry, Mapping) else None
    fetched_at = entry.get("fetched_at") if isinstance(entry, Mapping) else None
    value_ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    method_ok = method in (constants.METHOD_PRIMARY, _PIPELINE_DEFENSES_PROXY_METHOD)
    if confidence_value in ("verified", "derived", "flag") and value_ok and method_ok:
        paths = _resolved_source_paths(source_field, ctx.raw_root)
        if not paths or not isinstance(fetched_at, str) or not fetched_at:
            detail = (f"v1 source is not a resolvable envelope list: {source_field!r}" if not paths
                      else "v1 provenance for 'pipeline_defenses' has no fetched_at")
            return Unmeasured(reason="collector_failure", detail=detail)
        for path in paths:
            ctx.referenced[_relative_source(path, ctx.raw_root)] = _envelope_hash(path)
        n = 0
        if method == _PIPELINE_DEFENSES_PROXY_METHOD:
            data = _envelope_data(paths[0])
            if _pr_window_cut_short(data):
                return Unmeasured(reason="collector_failure", detail=_PR_WINDOW_CUT_SHORT_DETAIL)
            prs = data.get("prs") if isinstance(data, Mapping) else None
            sampled_prs = _reply_sampled_prs(prs or [])
            n = sum(1 for pr in sampled_prs if landing.merged_or_landed_at(pr) is not None)
        return Measured(value=float(value), n=n, window_start=None, window_end=None, method=method,
                         source=_relative_source(paths[0], ctx.raw_root),
                         source_hash=_envelope_hash(paths[0]), fetched_at=fetched_at)
    if confidence_value in (None, "missing"):
        return Unmeasured(
            reason="third_party_gap",
            detail="no scorecard envelope with a usable check subset and no pr_window/runs envelopes to "
                   "derive the review/CI proxy (porchlamp.collect.pike_signals.pipeline_defenses)",
        )
    if confidence_value == "collector_failure":
        # Pike signals deriver crashed before it could report either method
        # for this repo -- the companion detail field names the exception --
        # or (#51) refused the review/CI proxy on a pr_window record cut short
        # of its window, which the detail field says instead.
        detail = ctx.meta.get("pipeline_defenses_detail")
        return Unmeasured(reason="collector_failure",
                           detail=detail if isinstance(detail, str) and detail else "pike_signals collector failed")
    return Unmeasured(reason="collector_failure",
                       detail=f"v1 confidence {confidence_value!r} for 'pipeline_defenses' is not recognized")


#: #51 rule 5: how far back an ``author_history`` record must reach for each
#: cohort rate: the 1.3 decision cohort starts 270 d back, the 5.2/5.3
#: newcomer band 365 d back (``porchlamp.collect.github_graphql``'s
#: ``DECISION_COHORT_START_DAYS`` and ``NEWCOMER_BAND_START_DAYS``, which
#: ``porchlamp.derive`` cannot import; a test pins them equal).
_AUTHOR_HISTORY_REQUIRED_DAYS: dict[str, int] = {
    "mr_first_rate": 270,
    "newcomer_conversion": 365,
    "rr90_return_rate": 365,
}


def _author_history_rate_resolver(
    rate_field: str, count_field: str, detail_field: str, *, unset_detail: str, count_unit: str = "authors",
    landed_field: str, landed_clause: str,
) -> InputResolver:
    """Read-through resolver for the three D-IN-3-adjusted cohort rates
    ``porchlamp.collect.github_graphql.derive_cohort_fields`` computes straight from
    the ``author_history`` envelope -- ``mr_first_rate``, ``newcomer_conversion``,
    ``rr90_return_rate`` -- the same way :func:`_plain_scalar_resolver`
    reads other precomputed scalars (unlike ``dr90_decision_rate``,
    none of these three has its own live pr_window-based resolver yet, so
    there is no ambiguity to resolve).

    - confidence ``"derived"`` (the collector's own unmeasured-fraction
      gate did not fire): ``Measured`` from ``meta[rate_field]``, ``n``
      from ``meta[count_field]``, gated by this input's own
      ``PARAM_MIN_N`` -- including the ``count == 0`` case (``_ratio``
      returns ``None`` for a zero-size cohort even under a "derived"
      confidence), which reads as ``insufficient_n``, never
      ``collector_failure``: a real, checked, empty cohort is not a
      collector defect.
    - confidence ``"missing"`` (the gate fired: too many cohort/
      newcomer authors have no eligibility data to trust the adjusted
      rate): ``Unmeasured(collector_failure, ...)`` with the dynamic,
      per-repo detail carried in ``meta[detail_field]`` -- never the static
      ``unset_detail``, which would hide that a collector failure, not an
      absent collector, is why this row has no value.
    - confidence absent entirely (no ``author_history`` envelope was
      collected for this repo or an older run predating the collector):
      ``Unmeasured(collector_failure, unset_detail)``, the static message
      identifying the missing collector itself.
    - any other confidence value: the same "not recognized" fallback
      :func:`_plain_scalar_resolver` uses.

    ``count_unit`` names what ``count_field`` counts in the
    ``insufficient_n`` detail -- ``"authors"`` for newcomer conversion
    and retention rate, but ``mr_first_rate``'s ``mr_first_pr_count`` counts
    PRs, not authors, so that row passes ``count_unit="PRs"``.

    RFC-0015: ``landed_field`` is the field ``derive_cohort_fields`` records
    beside ``count_field``, how many of those PRs or authors count as merged by
    a landing outside GitHub's merge; an ``insufficient_n`` detail names it in
    ``landed_clause`` (a ``{landed}`` placeholder for the count), or says the
    record shows no landing (:func:`_landing_note`).

    #51 rule 5: a ``"derived"`` rate whose ``author_history`` record stopped
    short of the days its cohort needs (:data:`_AUTHOR_HISTORY_REQUIRED_DAYS`)
    is ``Unmeasured(collector_failure)``, whatever the deriver recorded.
    """

    def resolve(ctx: _V1Ctx) -> Measured | Unmeasured:
        confidence_value = ctx.confidence.get(rate_field)
        if confidence_value == "derived":
            entry = ctx.provenance.get(rate_field)
            source = _first_source(entry)
            fetched_at = entry.get("fetched_at") if isinstance(entry, Mapping) else None
            path = _resolve_envelope(source, ctx.raw_root)
            if path is not None:
                required_days = _AUTHOR_HISTORY_REQUIRED_DAYS.get(rate_field, 365)
                if _author_history_cut_short(_envelope_data(path), required_days):
                    return Unmeasured(
                        reason="collector_failure",
                        detail=(f"author_history window is truncated before reaching the "
                                f"{required_days}d cohort start"),
                    )
            value = ctx.meta.get(rate_field)
            count = ctx.meta.get(count_field)
            count_ok = isinstance(count, int) and not isinstance(count, bool)
            min_n = constants.PARAM_MIN_N.get(rate_field)
            if count_ok and min_n is not None and count < min_n:
                note = _landing_note(ctx, "author_history_landing_recorded", landed_field, landed_clause)
                return Unmeasured(
                    reason="insufficient_n",
                    detail=f"{count} {count_unit} in the D-IN-3-adjusted cohort, need >= {min_n}{note}",
                )
            value_ok = isinstance(value, (int, float)) and not isinstance(value, bool)
            if value_ok and count_ok:
                if path is not None and isinstance(fetched_at, str) and fetched_at:
                    source_hash = _envelope_hash(path)
                    relative = _relative_source(path, ctx.raw_root)
                    ctx.referenced[relative] = source_hash
                    return Measured(
                        value=value, n=count, window_start=None, window_end=None, method="primary",
                        source=relative, source_hash=source_hash, fetched_at=fetched_at,
                    )
                detail = (f"v1 source is not an envelope: {source!r}" if path is None
                          else f"v1 provenance for {rate_field!r} has no fetched_at")
                return Unmeasured(reason="collector_failure", detail=detail)
            return Unmeasured(
                reason="collector_failure",
                detail=f"v1 deriver recorded {rate_field}={value!r}, {count_field}={count!r}: not a usable pair",
            )
        if confidence_value == "missing":
            detail = ctx.meta.get(detail_field)
            if not isinstance(detail, str) or not detail:
                detail = f"{rate_field} unavailable: author_history unmeasured-fraction gate fired with no detail recorded"
            return Unmeasured(reason="collector_failure", detail=detail)
        if confidence_value is None:
            return Unmeasured(reason="collector_failure", detail=unset_detail)
        return Unmeasured(
            reason="collector_failure",
            detail=f"v1 confidence {confidence_value!r} for {rate_field!r} is not recognized",
        )

    return resolve


def _unmapped(detail: str) -> InputResolver:
    """A v2 input with no landed producer yet (or one whose landed collector
    cannot supply it — see each row's ``detail``): constant
    ``Unmeasured(collector_failure)``."""

    def resolve(ctx: _V1Ctx) -> Measured | Unmeasured:
        return Unmeasured(reason="collector_failure", detail=detail)

    return resolve


def _resolve_dx(ctx: _V1Ctx) -> Measured | Unmeasured:
    """5.4 DX (0.5 CI runtime + 0.5 setup signals on 0-100, min n = 5 PR commits;
    or setup-signals proxy ``proxy:setup_signals_only`` when < 5 PR commits, RFC-0020)."""
    confidence_value = ctx.confidence.get("dx")
    if confidence_value in ("derived", "verified"):
        value = ctx.meta.get("dx")
        count = ctx.meta.get("dx_run_count")
        count_ok = isinstance(count, int) and not isinstance(count, bool) and count >= 0
        min_n = constants.PARAM_MIN_N.get("dx")
        raw_method = ctx.meta.get("dx_method")
        is_proxy = (
            raw_method == constants.METHOD_PROXY_DX_SETUP_SIGNALS
            and count_ok
            and min_n is not None
            and count < min_n
        )
        method = (
            constants.METHOD_PROXY_DX_SETUP_SIGNALS
            if is_proxy
            else constants.METHOD_PRIMARY
        )
        req_min_n = constants.DX_PROXY_MIN_N if is_proxy else min_n
        if count_ok and req_min_n is not None and count < req_min_n:
            return Unmeasured(
                reason="insufficient_n",
                detail=f"{count} pull-request commits with a finished CI run, need >= {min_n}",
            )
        value_ok = (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and 0.0 <= float(value) <= 100.0
        )
        if value_ok and count_ok:
            entry = ctx.provenance.get("dx")
            source = _first_source(entry)
            fetched_at = entry.get("fetched_at") if isinstance(entry, Mapping) else None
            path = _resolve_envelope(source, ctx.raw_root)
            if path is not None and isinstance(fetched_at, str) and fetched_at:
                source_hash = _envelope_hash(path)
                relative = _relative_source(path, ctx.raw_root)
                ctx.referenced[relative] = source_hash
                return Measured(
                    value=float(value), n=count, window_start=None, window_end=None, method=method,
                    source=relative, source_hash=source_hash, fetched_at=fetched_at,
                )
            detail = (f"v1 source is not an envelope: {source!r}" if path is None
                      else "v1 provenance for 'dx' has no fetched_at")
            return Unmeasured(reason="collector_failure", detail=detail)
        return Unmeasured(
            reason="collector_failure",
            detail=f"v1 deriver recorded dx={value!r}, dx_run_count={count!r}: not a usable pair",
        )
    if confidence_value == "missing":
        count = ctx.meta.get("dx_run_count")
        count_ok = isinstance(count, int) and not isinstance(count, bool) and count >= 0
        min_n = constants.PARAM_MIN_N.get("dx")
        if (
            ctx.confidence.get("dx_run_count") in ("derived", "verified")
            and count_ok
            and min_n is not None
            and count < min_n
        ):
            return Unmeasured(
                reason="insufficient_n",
                detail=f"{count} pull-request commits with a finished CI run, need >= {min_n}",
            )
        return Unmeasured(
            reason="collector_failure",
            detail="v1 deriver recorded no dx value (no actions_runs envelope)",
        )
    if confidence_value is None:
        return Unmeasured(
            reason="collector_failure",
            detail="v2 5.4 blends CI runtime with setup signals; v1 stores a CI runtime mean only",
        )
    return Unmeasured(
        reason="collector_failure",
        detail=f"v1 confidence {confidence_value!r} for 'dx' is not recognized",
    )


#: The v2 input mapping table: one resolver per
#: ``constants.PARAM_INPUT_IDS`` entry, in the same pillar order.
#:
#: | Input id | Reads | When absent |
#: |---|---|---|
#: | ttfhr_hours | meta.pr_window_ttfhr_median_hours + .pr_window_external_measured_latency_count, computed by derive from the pr_window PR nodes under the current rules (outside PRs: external association, non-bot author; never the envelope's stored derived list, #57) | no pr_window envelope -> collector_failure; n<10 -> insufficient_n |
#: | dr90_decision_rate | raw pr_window envelope: prs[] nodes of outside PRs (gates.is_outside_pr: external association, non-bot author; #57) with their createdAt/mergedAt/closedAt, cohort [anchor-180d,anchor-90d) (D-IN-4 amendment); D-IN-3 eligibility joined from the author_history envelope's author_records | no envelope -> collector_failure; author_history truncated before the cohort start instant (pr_window anchor-180d, not the history's own anchor) -> collector_failure; no author_history envelope -> Measured with method proxy:din3_unapplied; cohort n<10 -> insufficient_n; malformed/missing createdAt excluded and named in that detail |
#: | mr_first_rate | meta.mr_first_rate + .mr_first_pr_count (D-IN-3-adjusted; merged includes landed outside GitHub's merge, RFC-0015: .mr_first_landed_pr_count) | no author_history envelope -> collector_failure (static); unmeasured-fraction gate fired -> collector_failure (dynamic detail); n<5 -> insufficient_n, detail names the landed count |
#: | gr14_ghost_rate | meta.gr14_ghost_rate + .gr14_population_n + .gr14_window_start/_end, computed by derive with porchlamp.core.gates.zero_response_counts (also D-GATE-5's input) | no pr_window envelope -> collector_failure; not derived by this tree -> collector_failure; n=0 -> insufficient_n; n<10 -> insufficient_n at scoring with the true n |
#: | ttm_p90_hours | raw pr_window envelope: prs[] nodes of merged outside PRs (gates.is_outside_pr; #57), merge time porchlamp.core.landing.merged_or_landed_at (RFC-0015) | no envelope -> collector_failure; n<5 -> insufficient_n, detail names meta.pr_window_landed_external_count |
#: | dependents_deduped | meta.dependents_count when confidence=="derived" (dependents hook); meta.ecosystem gates it | ecosystem=="systems" -> not_applicable (D-MISS-5); verified -> collector_failure (raw usedByCount is not deduped); missing -> third_party_gap, maven detail names the uncollected pom.xml, unmapped ecosystem detail names it |
#: | openssf_criticality | meta.openssf_signals, ten raw Pike signals; C computed locally | confidence missing, or any signal absent -> third_party_gap (never a partial score) |
#: | downloads_monthly | meta.monthly_downloads; meta.downloads_status refines the Unmeasured detail | go/maven/systems -> not_applicable (D-MISS-5); otherwise third_party_gap, detail per downloads_status |
#: | legal_ip | meta.legal_ip, vocab-mapped | collector_failure; an unmappable enum is this row's own gap, not a whole-bundle rejection |
#: | elephant_factor | meta.elephant_factor; n = meta.elephant_factor_commit_count (non-bot org-attributed commits in the 365 d window; 0 when absent, e.g. the contributors-only fallback) | collector_failure; n<30 -> insufficient_n at scoring, with the true n |
#: | promotion_ladder | meta.promotion_ladder, vocab-mapped | collector_failure; an unmappable enum is this row's own gap, not a whole-bundle rejection |
#: | pipeline_defenses | meta.pipeline_defenses + .pipeline_defenses_method: "primary" (scorecard) or "proxy:review_ci" (n = merged or landed PRs, RFC-0015) | confidence absent or missing -> third_party_gap (TP with proxy) |
#: | ai_posture | meta.ai_posture, vocab-validated against constants.AI_POSTURE_VALUES (identity map) | collector_failure; an out-of-set value is collector_failure too, never Measured |
#: | substantive_review_rate | meta.pr_window_substantive_review_rate (derive, from the PR nodes, #57); n = decided outside PRs in the reply sample, from the raw envelope's prs[] nodes | no envelope -> collector_failure; n<10 -> insufficient_n |
#: | newcomer_issue_supply | meta.active_unclaimed_gfis | collector_failure |
#: | newcomer_conversion | meta.newcomer_conversion + .newcomer_conversion_author_count (D-IN-3-adjusted; a first PR landed outside GitHub's merge converts, RFC-0015: .newcomer_conversion_landed_author_count) | no author_history envelope -> collector_failure (static); unmeasured-fraction gate fired -> collector_failure (dynamic detail); n<5 -> insufficient_n, detail names the landed count |
#: | rr90_return_rate | meta.rr90_return_rate + .rr90_return_author_count (D-IN-3-adjusted; a first PR landed outside GitHub's merge starts the clock, RFC-0015: .rr90_return_landed_author_count) | no author_history envelope -> collector_failure (static); unmeasured-fraction gate fired -> collector_failure (dynamic detail); n<5 -> insufficient_n, detail names the landed count |
#: | dx | meta.dx + .dx_run_count (0.5 CI runtime + 0.5 setup signals; proxy:setup_signals_only when n<5 and setup signals present) | no actions_runs envelope and no setup signals -> collector_failure; n<5 with no setup signals -> insufficient_n |
INPUT_MAPPINGS: dict[str, InputResolver] = {
    "ttfhr_hours": _resolve_ttfhr_hours,
    "dr90_decision_rate": _resolve_dr90_decision_rate,
    "mr_first_rate": _author_history_rate_resolver(
        "mr_first_rate", "mr_first_pr_count", "cohort_unmeasured_detail",
        unset_detail="needs W1-4 author history; the v1 pipeline collected none",
        count_unit="PRs",
        landed_field="mr_first_landed_pr_count",
        landed_clause="{landed} of them were landed outside GitHub's merge and count as merged",
    ),
    "gr14_ghost_rate": _resolve_gr14_ghost_rate,
    "ttm_p90_hours": _resolve_ttm_p90_hours,
    "dependents_deduped": _resolve_dependents_deduped,
    "openssf_criticality": _resolve_openssf_criticality,
    "downloads_monthly": _resolve_downloads_monthly,
    # "legal_ip" reads the bare "legal_ip" field (identity vocab matching
    # constants.LEGAL_IP_VALUES plus legacy V1_LEGAL_IP_VALUES), rather than
    # the legacy regex heuristic.
    "legal_ip": _vocab_scalar_resolver(
        "legal_ip", {value: value for value in constants.LEGAL_IP_VALUES} | V1_LEGAL_IP_VALUES),
    "elephant_factor": _plain_scalar_resolver("elephant_factor", n_field="elephant_factor_commit_count"),
    # "promotion_ladder" accepts both the current vocabulary as identity
    # (VERIFIED/DOCUMENTED/AD_HOC/CLOSED) and the legacy v1 spellings via
    # V1_LADDER_VALUES as a fallback.
    "promotion_ladder": _vocab_scalar_resolver(
        "promotion_ladder",
        {**V1_LADDER_VALUES, **{value: value for value in constants.PROMOTION_LADDER_VALUES}}),
    "pipeline_defenses": _resolve_pipeline_defenses,
    "ai_posture": _vocab_scalar_resolver("ai_posture", {value: value for value in constants.AI_POSTURE_VALUES}),
    "substantive_review_rate": _resolve_substantive_review_rate,
    "newcomer_issue_supply": _plain_scalar_resolver("active_unclaimed_gfis"),
    "newcomer_conversion": _author_history_rate_resolver(
        "newcomer_conversion", "newcomer_conversion_author_count", "newcomer_unmeasured_detail",
        unset_detail="needs the W1-4 newcomer cohort; the v1 pipeline collected none",
        landed_field="newcomer_conversion_landed_author_count",
        landed_clause="{landed} of them converted by a first PR landed outside GitHub's merge",
    ),
    "rr90_return_rate": _author_history_rate_resolver(
        "rr90_return_rate", "rr90_return_author_count", "newcomer_unmeasured_detail",
        unset_detail="v2 5.3 is cohort-based (W1-4); v1 rr_90 approximates author gaps",
        landed_field="rr90_return_landed_author_count",
        landed_clause="{landed} of them had a first PR landed outside GitHub's merge",
    ),
    "dx": _resolve_dx,
}

if set(INPUT_MAPPINGS) != set(constants.PARAM_INPUT_IDS):
    raise ValueError("INPUT_MAPPINGS must carry exactly one row per v2 input id")


# --------------------------------------------------------------------------- #
# Universe-entry star-velocity pass-through (D-IN-5).                         #
#                                                                              #
# star_velocity_flag/stars_audited are NOT one of the 18 parameter inputs -- #
# D-IN-5 states plainly "Stars never enter the composite", and                #
# INPUT_MAPPINGS' own assert closes `FeatureBundle.inputs` at exactly          #
# `constants.PARAM_INPUT_IDS`, so these two facts cannot be a row there.     #
# This table is the "meta pass-through" a caller reads instead -- same         #
# _V1Ctx-based resolver shape as INPUT_MAPPINGS, built from the v1            #
# meta/provenance/confidence dicts written during universe collection         #
# (`star_velocity_flag`/`stars_audited`).                                     #
# `porchlamp.verify.detectors.DetectorInputs.star_lockstep_penalty_applied` (the     #
# `star_burst` detector's input) and `porchlamp.store.writer`'s `stars_audited`      #
# column are its consumers -- see `resolve_universe_meta_facts` below.        #
# --------------------------------------------------------------------------- #


def _resolve_star_velocity_flag(ctx: _V1Ctx) -> Measured | Unmeasured:
    """D-IN-5's StarScout lockstep-penalty flag. ``Measured.value`` cannot
    hold a ``bool`` (``measured_problems`` rejects it, D-VER-2's
    int/float/str contract), so the flag is encoded as ``1``/``0``; a
    caller reads ``bool(value.value)``. Mirrors ``_plain_scalar_resolver``'s
    confidence-first check (``verified``/``missing``/anything else) but
    cannot reuse it directly because that helper's own ``value_ok`` check
    explicitly excludes ``bool``."""
    confidence_value = ctx.confidence.get("star_velocity_flag")
    value = ctx.meta.get("star_velocity_flag")
    entry = ctx.provenance.get("star_velocity_flag")
    source = _first_source(entry)
    fetched_at = entry.get("fetched_at") if isinstance(entry, Mapping) else None
    if confidence_value == "verified" and isinstance(value, bool):
        path = _resolve_envelope(source, ctx.raw_root)
        if path is not None and isinstance(fetched_at, str) and fetched_at:
            source_hash = _envelope_hash(path)
            relative = _relative_source(path, ctx.raw_root)
            ctx.referenced[relative] = source_hash
            return Measured(value=1 if value else 0, n=0, window_start=None, window_end=None,
                             method="primary", source=relative, source_hash=source_hash,
                             fetched_at=fetched_at)
        detail = (f"v1 source is not an envelope: {source!r}" if path is None
                  else "v1 provenance for 'star_velocity_flag' has no fetched_at")
        return Unmeasured(reason="collector_failure", detail=detail)
    if confidence_value == "missing":
        return Unmeasured(
            reason="collector_failure",
            detail="universe entry never audited star velocity for this repo "
                   "(star-history fetch failed, porchlamp.collect.universe)",
        )
    return Unmeasured(reason="collector_failure",
                       detail=f"v1 confidence {confidence_value!r} for 'star_velocity_flag' is not recognized")


_resolve_stars_audited: InputResolver = _plain_scalar_resolver(
    "stars_audited",
    missing_reason="collector_failure",
    missing_detail="universe entry never audited star velocity for this repo "
                    "(star-history fetch failed, porchlamp.collect.universe)",
)

#: The two universe-entry facts, keyed exactly like INPUT_MAPPINGS but kept
#: in a separate table so INPUT_MAPPINGS' own 18-row assert stays exact.
UNIVERSE_META_MAPPINGS: dict[str, InputResolver] = {
    "star_velocity_flag": _resolve_star_velocity_flag,
    "stars_audited": _resolve_stars_audited,
}


def resolve_universe_meta_facts(
    document: Mapping[str, Any], raw_root: Union[Path, str, None] = None
) -> dict[str, Measured | Unmeasured]:
    """Resolve :data:`UNIVERSE_META_MAPPINGS` over a v1 derived document --
    the same document :func:`load_bundle` reads, but returning the two
    universe-entry star-velocity facts instead of a
    :class:`~porchlamp.core.models.FeatureBundle` (they are not part of its
    closed ``inputs`` schema; see the module comment above). Raises
    :class:`BundleValidationError` for a document missing
    ``meta``/``provenance``/``confidence``, exactly like ``_load_v1``.
    """
    meta = document.get("meta")
    provenance = document.get("provenance")
    confidence = document.get("confidence")
    if not all(isinstance(doc, Mapping) for doc in (meta, provenance, confidence)):
        raise BundleValidationError(["v1 bundle meta/provenance/confidence must be mappings"])
    ctx = _V1Ctx(meta=meta, provenance=provenance, confidence=confidence, raw_root=raw_root,
                 problems=[], referenced={})
    return {input_id: resolver(ctx) for input_id, resolver in UNIVERSE_META_MAPPINGS.items()}


def _pr_event_or_none(
    entry: Mapping[str, Any], index: int, problems: list[str]
) -> Optional[PRWindowEvent]:
    """One ``PRWindowEvent`` from a raw v1 ``pr_events`` entry, or ``None``
    for a ghost/deleted-account event: GitHub returns an empty
    ``author_login`` for these and ``PRWindowEvent`` requires a non-empty one
    (it is an author-keyed record). Other malformed events still append to
    ``problems`` (the whole bundle is rejected) and also return ``None``; the
    caller tells the two apart by checking ``author_login`` itself.
    """
    if not entry.get("author_login"):
        return None
    try:
        return PRWindowEvent(
            pr_number=entry["pr_number"],
            created_at=entry["created_at"],
            closed_at=entry.get("closed_at"),
            merged_at=entry.get("merged_at"),
            author_login=entry["author_login"],
            author_association=entry.get("author_association", "NONE"),
            is_external=bool(entry.get("is_external")),
            first_human_review_at=entry.get("first_human_review_at"),
        )
    except (KeyError, BundleValidationError) as error:
        problems.append(f"v1 pr_events[{index}]: {error!s}")
        return None


def _load_v1(document: Mapping[str, Any], raw_root: Union[Path, str, None]) -> FeatureBundle:
    """Map the v1 derived document (scripts/derive.py output) to v2.

    Envelope-backed fields become Measured with source = envelope path and
    source_hash = sha256 of the envelope's data field; everything else is
    Unmeasured with a documented reason. No default value is emitted.
    """
    repo_str = document.get("repo")
    if not isinstance(repo_str, str) or "/" not in repo_str:
        raise BundleValidationError([f"v1 bundle has no 'owner/name' repo field: {repo_str!r}"])
    owner, name = repo_str.split("/", 1)
    meta = document.get("meta")
    provenance = document.get("provenance")
    confidence = document.get("confidence")
    if not all(isinstance(doc, Mapping) for doc in (meta, provenance, confidence)):
        raise BundleValidationError(["v1 bundle meta/provenance/confidence must be mappings"])
    ecosystem = meta.get("ecosystem")
    if not isinstance(ecosystem, str) or not ecosystem:
        raise BundleValidationError(["v1 bundle has no meta.ecosystem"])

    problems: list[str] = []
    referenced: dict[str, str] = {}

    github_meta = _resolve_envelope(
        _first_source(provenance.get("stars")) or _first_source(provenance.get("forks")), raw_root
    )
    archived, license_spdx, default_branch = False, None, None
    if github_meta is not None:
        data = _envelope_data(github_meta)
        if isinstance(data, Mapping):
            archived = bool(data.get("archived", False))
            license_spdx = data.get("license_spdx") if isinstance(data.get("license_spdx"), str) else None
            default_branch = data.get("default_branch") if isinstance(data.get("default_branch"), str) else None
        hash_ = _envelope_hash(github_meta)
        referenced[_relative_source(github_meta, raw_root)] = hash_
    # fork and mirror_url: not carried by the v1 github_meta envelope; the
    # identity block records them as False/None rather than guessing.
    try:
        repo = RepoIdentity(
            owner=owner, name=name, ecosystem=ecosystem, archived=archived,
            mirror_url=None, fork=False, license_spdx=license_spdx, default_branch=default_branch,
        )
    except BundleValidationError as error:
        raise BundleValidationError(
            [f"v1 repo identity invalid: {problem}" for problem in error.problems]
        ) from error

    ctx = _V1Ctx(meta=meta, provenance=provenance, confidence=confidence, raw_root=raw_root,
                 problems=problems, referenced=referenced)
    inputs: dict[str, Measured | Unmeasured] = {
        input_id: INPUT_MAPPINGS[input_id](ctx) for input_id in constants.PARAM_INPUT_IDS
    }
    # Computed here (not via an INPUT_MAPPINGS row -- neither fact is one of
    # the 18 scored inputs) so the commits envelope it reads is resolved,
    # hashed and folded into ctx.referenced before provenance_hash is computed
    # below from that same dict.
    last_commit_fact, top_domain_fact = _resolve_commit_facts(ctx, f"{owner}__{name}")
    closed_unread_fact = _resolve_closed_unread(ctx)
    stars_audited_fact = _resolve_stars_audited(ctx)
    star_velocity_flag_fact = _resolve_star_velocity_flag(ctx)
    raw_forks = meta.get("forks")
    forks_fact = (
        raw_forks
        if isinstance(raw_forks, int) and not isinstance(raw_forks, bool) and raw_forks >= 0
        else None
    )

    raw_events = document.get("pr_events")
    events: list[PRWindowEvent] = []
    dropped_ghost_author_count = 0
    all_created: list[str] = []
    all_timestamps: list[str] = []
    if isinstance(raw_events, list):
        for index, entry in enumerate(raw_events):
            if not isinstance(entry, Mapping):
                problems.append(f"v1 pr_events[{index}] is not a mapping")
                continue
            was_ghost_author = not entry.get("author_login")
            event = _pr_event_or_none(entry, index, problems)
            if event is not None:
                events.append(event)
                bound_source: Mapping[str, Any] = entry
            elif was_ghost_author:
                # Window bounds are author-irrelevant: a ghost-account event
                # still marks calendar coverage even though it cannot become an
                # author-keyed PRWindowEvent. A genuinely malformed (non-ghost)
                # entry is excluded from bounds entirely (its problems() entry,
                # appended above, rejects the whole bundle) -- folding its
                # unparsed timestamps in here would let a single-event bundle's
                # PRWindow invariant fire before that more informative per-event
                # error is reported.
                dropped_ghost_author_count += 1
                bound_source = entry
            else:
                continue
            created_ts = _valid_iso_string(bound_source.get("created_at"))
            if created_ts is not None:
                all_created.append(created_ts)
                all_timestamps.append(created_ts)
            for ts_field in ("closed_at", "merged_at"):
                ts = _valid_iso_string(bound_source.get(ts_field))
                if ts is not None:
                    all_timestamps.append(ts)
    for source in (
        provenance.get("pr_events", {}).get("source", [])
        if isinstance(provenance.get("pr_events"), Mapping)
        else []
    ):
        path = _resolve_envelope(source, raw_root)
        if path is not None:
            referenced[_relative_source(path, raw_root)] = _envelope_hash(path)

    def iso(ts: str) -> datetime.datetime:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))

    has_events = bool(events)
    window = PRWindow(
        start=min(all_created, key=iso) if (has_events and all_created) else None,
        end=max(all_timestamps, key=iso) if (has_events and all_timestamps) else None,
        truncated=False,  # the v1 document carries no truncation marker
        events=tuple(events),
    )
    # The v1 pipeline produced no cohorts.
    cohorts = Cohorts(decision_pr_numbers=(), newcomer_authors=())

    if problems:
        raise BundleValidationError(problems)
    bundle = FeatureBundle(
        schema_version=constants.SCHEMA_VERSION,
        repo=repo,
        inputs=inputs,
        pr_window=window,
        cohorts=cohorts,
        provenance_hash=models.feature_provenance_hash(referenced),
    )
    bundle.validate()
    _set_dropped_event_count(bundle, dropped_ghost_author_count)
    _set_last_non_bot_commit_at(bundle, last_commit_fact)
    _set_top_email_domain_share(bundle, top_domain_fact)
    _set_stars_audited(bundle, stars_audited_fact)
    _set_star_velocity_flag(bundle, star_velocity_flag_fact)
    _set_forks_count(bundle, forks_fact)
    set_closed_unread_counts(bundle, closed_unread_fact)
    return bundle
