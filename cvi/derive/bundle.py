"""Load, validate and dump FeatureBundle v2 documents (packet W2-1; W2-9; W2-9f).

A v2 document (``data/derived/<key>.json``, ``schema_version = 2``) is the
JSON shape of :class:`cvi.core.models.FeatureBundle`:
``{schema_version, repo, inputs, pr_window, cohorts, provenance_hash}``.
``validate_bundle`` checks it strictly: every one of the 18 parameter input
ids must be present as a Measured or an Unmeasured value, and any other
violation raises :class:`cvi.core.models.BundleValidationError`.

Documents without a ``schema_version`` (or with ``schema_version = 1``) are
the v1 derived format written by ``scripts/derive.py``. The v1 deriver filled
missing inputs with silent defaults (``Bundle.missing(field, default)``); the
v2 mapping does not. Envelope-backed v1 fields become Measured values whose
provenance is the envelope path plus ``sha256`` of the envelope's ``data``
field (R-4); everything else becomes Unmeasured with a documented reason.

W2-9 replaces the fixed ``V1_FIELD_TO_INPUT`` dict of the original five
mappings with :data:`INPUT_MAPPINGS`: one resolver per v2 input id (18 rows,
matching ``constants_v2.PARAM_INPUT_IDS`` one-for-one) so a later packet
wires a newly-landed collector by replacing its one row, not by re-reading
this whole module. See the module's own docstring comments beside each row
for which derived-file field(s) it reads and the D-MISS-1 reason emitted
when they are absent.

W2-9f (Opus review follow-up, see ``.agents/W2-9f/handoff.md``) wires the
now-landed W1-7 rows (``openssf_criticality``, ``pipeline_defenses``),
fixes ``dependents_deduped``'s absence reason to ``third_party_gap`` (it is
class TP, like ``downloads_monthly``), adds vocabulary validation to
``ai_posture``, and stops an unmappable ``legal_ip``/``promotion_ladder``
enum from rejecting the whole bundle: the mapping is now row-scoped
everywhere (:func:`_map_v1_value` no longer appends to the bundle-wide
``problems`` list).

W1-2f (D-MISS-5, ``docs/v2/02-RANKING-DESIGN.md`` section 5) changes only
``dependents_deduped``: ``ecosystem == "systems"`` yields
``Unmeasured(not_applicable)`` unconditionally (a universe-entry fact this
row reads straight off ``ctx.meta``, never from what the collector fetched
-- D-MISS-5's guard), ``maven`` gets a specific ``third_party_gap`` detail
when nothing was measured (the root ``pom.xml`` was never fetched), and any
ecosystem outside the closed rule list plus this packet's known mappings
still falls to a named ``third_party_gap``, never ``not_applicable``.

W2-12 (D-IN-4 amendment, 2026-09-13, ``docs/v2/02-RANKING-DESIGN.md`` D-IN-4)
wires ``dr90_decision_rate``: the decision-rate cohort is external PRs
opened in the older half of the W1-3 180 d window, ``[anchor-180d,
anchor-90d)``, computed from the raw pr_window envelope's own
``window_anchor`` (never a live clock, 04 section 8) -- no second fetch, no
new collector. See :func:`_resolve_dr90_decision_rate` and its helper
:func:`_dr90_decision_cohort` for the exact boundary rules.
"""
from __future__ import annotations

import datetime
import json
import math
from dataclasses import dataclass, field, fields
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Union

from cvi.core import constants_v2, models, openssf
from cvi.core.models import (
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
    """
    if not isinstance(document, Mapping):
        raise BundleValidationError([f"document must be a mapping, got {type(document).__name__}"])
    doc_keys = set(document)
    problems = [f"missing top-level key {key!r}" for key in TOP_LEVEL_KEYS if key not in doc_keys]
    problems += [f"unexpected top-level key {key!r}" for key in sorted(doc_keys - set(TOP_LEVEL_KEYS))]
    if problems:
        raise BundleValidationError(problems)

    version = document["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != constants_v2.SCHEMA_VERSION:
        raise BundleValidationError([f"schema_version must be {constants_v2.SCHEMA_VERSION}, got {version!r}"])

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
    if version == constants_v2.SCHEMA_VERSION:
        return validate_bundle(document)
    if version in (None, 1) and isinstance(document, Mapping) and "meta" in document:
        return _load_v1(document, raw_root)
    raise BundleValidationError([f"unrecognized bundle format: schema_version {version!r}"])


def dump_bundle(bundle: FeatureBundle) -> dict[str, Any]:
    """Serialize a FeatureBundle into the v2 document shape (validates first)."""
    bundle.validate()
    inputs: dict[str, dict[str, Any]] = {}
    for input_id in constants_v2.PARAM_INPUT_IDS:
        value = bundle.inputs[input_id]
        if isinstance(value, Measured):
            inputs[input_id] = dict(zip(MEASURED_DOC_KEYS, ("measured", value.value, value.n, value.window_start,
                                                           value.window_end, value.method, value.source,
                                                           value.source_hash, value.fetched_at)))
        else:
            inputs[input_id] = {"kind": "unmeasured", "reason": value.reason, "detail": value.detail}
    return {
        "schema_version": constants_v2.SCHEMA_VERSION,
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
                }
                for author in bundle.cohorts.newcomer_authors
            ],
        },
        "provenance_hash": bundle.provenance_hash,
    }


def write_bundle(bundle: FeatureBundle, path: Union[Path, str]) -> Path:
    """Write the v2 document to ``path`` (v1 on-disk style: indent 2, sorted keys)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dump_bundle(bundle), indent=2, sort_keys=True), encoding="utf-8")
    return target


#: Instance attribute name backing :func:`dropped_event_count` (W2-9). Not a
#: declared FeatureBundle field (cvi/core/models.py is out of this packet's
#: ownership; see the handoff's ownership request) and not serialized by
#: :func:`dump_bundle` — a dump/reload round trip loses the count.
_DROPPED_EVENT_COUNT_ATTR = "_w2_9_dropped_event_count"


def dropped_event_count(bundle: FeatureBundle) -> int:
    """Number of ``pr_events`` dropped from ``bundle.pr_window.events`` (W2-9)
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
    expected = set(constants_v2.PARAM_INPUT_IDS)
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
            authors.append(NewcomerAuthor(login=entry["login"], first_pr=first, followups=tuple(refs)))
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


def _envelope_data(path: Path) -> Any:
    document = json.loads(path.read_text(encoding="utf-8"))
    return document.get("data") if isinstance(document, Mapping) else None


def _envelope_hash(path: Path) -> str:
    """sha256 of the canonical JSON of the envelope's data field (W0-3 rule)."""
    return models.sha256_hex(models.canonical_json(_envelope_data(path)))


def _first_source(entry: Any) -> str | None:
    if isinstance(entry, Mapping):
        source = entry.get("source")
        if isinstance(source, str):
            return source
    return None


def _parse_iso_or_none(value: Any) -> Optional[datetime.datetime]:
    """Parse an ISO-8601 timestamp; ``None`` for absent or unparseable input.

    Never reads a live clock (04 section 8): it only parses a string already
    recorded in an envelope or a derived document."""
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
# W2-9: the declarative v2 input mapping table.                               #
#                                                                              #
# One resolver per v2 input id (18 rows total, one-for-one with               #
# constants_v2.PARAM_INPUT_IDS). Each resolver reads the v1 derived           #
# document's meta/provenance/confidence dicts (plus, for the three           #
# pr_window-derived rows, the raw pr_window envelope) and returns exactly    #
# one Measured or Unmeasured value. A later packet wires a newly-landed      #
# collector output by replacing its row in INPUT_MAPPINGS; nothing else in   #
# this module needs to change.                                               #
# --------------------------------------------------------------------------- #


@dataclass
class _V1Ctx:
    """Shared read-only-ish context every row resolver receives.

    ``problems`` and ``referenced`` are the same list/dict objects
    ``_load_v1`` uses for the whole bundle: appending to ``problems`` (an
    unrecognized enum value, 3.1/3.3's pre-W2-9 behaviour) rejects the whole
    bundle; writing to ``referenced`` folds an envelope hash into the
    bundle's ``provenance_hash``. ``cache`` memoizes the pr_window envelope
    read across the three rows that share it.
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
    """Map a v1 enum spelling through ``vocab``; ``None`` for an unrecognized
    value. W2-9f (W5): this never touches the bundle-wide ``problems`` list
    -- an unmappable enum is this one row's own gap (the caller returns a
    row-scoped ``Unmeasured``), not a whole-bundle-rejecting data-corruption
    signal (the pre-W2-9f behaviour: a single bad enum in a row the loader
    was already about to discard -- e.g. every real sample-200 document's
    ``raw.githubusercontent.com`` source for ``legal_ip``/``promotion_ladder``,
    which can never resolve to an envelope regardless of the enum -- lost
    the entire repo's 17 other, perfectly good inputs)."""
    return vocab.get(value)


def _plain_scalar_resolver(
    v1_field: str,
    *,
    missing_reason: str = "collector_failure",
    missing_detail: str = "v1 deriver recorded no value",
) -> InputResolver:
    """Resolver for a v1 meta field carried through unchanged: an int or str
    value, no enum mapping, ``n=0`` (a scalar, not a sample). Covers
    ``elephant_factor`` and (W2-9) ``newcomer_issue_supply`` -- not
    ``ai_posture`` (W2-9f, W4: it moved to :func:`_vocab_scalar_resolver`
    with an identity vocab, so an out-of-set value is validated rather than
    passed through as a Measured)."""

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
                ctx.referenced[str(path)] = source_hash
                return Measured(value=value, n=0, window_start=None, window_end=None, method="primary",
                                 source=str(path), source_hash=source_hash, fetched_at=fetched_at)
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
    v1-spelling -> v2-spelling vocabulary (``legal_ip``, ``promotion_ladder``;
    W2-9f: also ``ai_posture``, whose v1 spelling already is the v2 spelling
    -- an identity ``{v: v for v in constants_v2.AI_POSTURE_VALUES}`` vocab
    both validates and passes it through unchanged, W4). An unmappable value
    is this row's own gap (:func:`_map_v1_value`), never a whole-bundle
    rejection (W5)."""

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
            if path is not None and isinstance(fetched_at, str) and fetched_at and mapped is not None:
                source_hash = _envelope_hash(path)
                ctx.referenced[str(path)] = source_hash
                return Measured(value=mapped, n=0, window_start=None, window_end=None, method="primary",
                                 source=str(path), source_hash=source_hash, fetched_at=fetched_at)
            if path is None:
                detail = f"v1 source is not an envelope: {source!r}"
            elif not isinstance(fetched_at, str) or not fetched_at:
                detail = f"v1 provenance for {v1_field!r} has no fetched_at"
            else:
                detail = f"v1 value {value!r} for {v1_field!r} has no v2 vocabulary mapping"
            return Unmeasured(reason="collector_failure", detail=detail)
        if confidence_value == "missing":
            return Unmeasured(reason="collector_failure", detail="v1 deriver recorded no value")
        return Unmeasured(reason="collector_failure",
                           detail=f"v1 confidence {confidence_value!r} for {v1_field!r} is not recognized")

    return resolve


def _resolve_downloads_monthly(ctx: _V1Ctx) -> Measured | Unmeasured:
    """2.3 Downloads. Reads v1 ``monthly_downloads``, unchanged from pre-W2-9
    behaviour for the Measured path and the "old-format, no W1-8 hook" gap
    (over-correction guard). W2-9 adds: when W1-8's ``downloads_status`` meta
    field is present, its "pending" (ecosystem is retryable, not yet
    backfilled) vs "unmeasured" (no registry for this ecosystem) distinction
    replaces the generic detail text — same reason (``third_party_gap``,
    D-MISS-1 class TP), a more precise ``detail``.
    """
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
            ctx.referenced[str(path)] = source_hash
            return Measured(value=value, n=0, window_start=None, window_end=None, method="primary",
                             source=str(path), source_hash=source_hash, fetched_at=fetched_at)
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
        return Unmeasured(reason="third_party_gap", detail="registry returned no downloads data")
    return Unmeasured(reason="collector_failure",
                       detail=f"v1 confidence {confidence_value!r} for 'monthly_downloads' is not recognized")


#: Ecosystems D-MISS-5 does not name (``02`` section 5: the rule list names
#: only ``systems`` -> 2.1). Anything outside this set is a mapping gap this
#: row has never seen, not a fact about the world -- it must still fall to
#: ``third_party_gap``, never ``not_applicable`` (W1-2f: "the rule list is
#: closed"). Mirrors :class:`cvi.models.Ecosystem`'s members; not imported
#: from there because ``cvi.derive`` reads only ``cvi.core.models`` (04
#: section 3's import rule) and ``cvi.models`` sits outside ``cvi.core``.
_ECOSYSTEMS_WITH_DEPENDENTS_MAPPING: frozenset[str] = frozenset(
    {"pypi", "npm", "crates", "go", "maven"}
)


def _resolve_dependents_deduped(ctx: _V1Ctx) -> Measured | Unmeasured:
    """2.1 Dependents. v1's ``dependents_count`` carries two unrelated facts
    under the same key, distinguished only by ``confidence`` (W1-2): the raw,
    non-deduplicated deps.dev ``usedByCount`` (``"verified"``) or the W1-2
    hook's root-organisation-deduplicated count (``"derived"``). Only the
    ``"derived"`` value is D-PARAM 2.1's dependents_deduped; the raw count is
    never exposed under this input id, deduplicated or not.

    2.1 is class TP (02 section 4 row 2.1): ``confidence == "missing"`` means
    deps.dev/ecosyste.ms returned no ``usedByCount`` at all for this package
    -- a third-party gap, exactly like 2.3 downloads_monthly's equivalent
    branch (W2-9f, W1) -- not ``collector_failure`` (reserved here for a
    genuine local-side fault: an unresolvable envelope, or a value the W1-2
    hook wrote in a shape this loader cannot parse).

    W1-2f (D-MISS-5): ``ecosystem == "systems"`` always yields
    ``Unmeasured(not_applicable)`` here, ahead of the confidence check --
    the fact is about the ecosystem (recorded on the bundle at universe
    entry, ``ctx.meta["ecosystem"]``), never about what
    ``cvi.collect.depsdev.collect_dependents`` happened to fetch, per
    D-MISS-5's guard that this reason is assigned only by a design-level
    rule keyed on a universe fact. ``maven`` gets a more specific
    ``third_party_gap`` detail when nothing was measured: today's gap is
    that the root ``pom.xml`` was never fetched (the tree envelope carries
    file paths only, not contents), not that deps.dev/ecosyste.ms lack the
    package. Any ecosystem outside D-MISS-5's closed rule list and this
    packet's known mappings still falls to the generic ``third_party_gap``,
    named, never ``not_applicable``.
    """
    ecosystem = ctx.meta.get("ecosystem")
    if ecosystem == "systems":
        return Unmeasured(
            reason="not_applicable",
            detail="D-MISS-5: ecosystem == systems has no package registry (2.1 is undefined)",
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
    ctx.referenced[str(path)] = source_hash
    return Measured(value=value, n=0, window_start=None, window_end=None, method="primary",
                     source=str(path), source_hash=source_hash, fetched_at=fetched_at)


def _pr_window_envelope(ctx: _V1Ctx) -> tuple[Optional[Path], Optional[Mapping[str, Any]], Optional[str]]:
    """Resolve and read the raw ``pr_window`` envelope (W1-3) once per bundle
    load, memoized on ``ctx.cache`` since three rows share it.

    Keyed off the ``pr_window_count`` provenance entry: ``derive_pr_window_fields``
    sets it (confidence ``"verified"``) unconditionally whenever the W1-3 hook
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


def _pr_window_bounds(data: Optional[Mapping[str, Any]]) -> tuple[Optional[str], Optional[str]]:
    """The pr_window envelope's own ``(start, end)`` as ISO strings, derived
    from its recorded ``window_anchor``/``window_days`` — not a live clock
    read (04 section 8): both are values the W1-3 collector already stored
    at its own collection time."""
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
    """Merge latency in hours for merged, external PRs (1.5 TTM_p90).

    Joins the raw pr_window envelope's ``derived`` classifications
    (``is_external``, ``merged``) with its ``prs`` raw nodes (``createdAt``,
    ``mergedAt``) by PR ``number``: the W1-3 collector records them in two
    separate lists, neither alone carries both facts.
    """
    classified = {
        entry.get("number"): entry
        for entry in (data.get("derived") or [])
        if isinstance(entry, Mapping) and entry.get("number") is not None
    }
    out: list[float] = []
    for pr in data.get("prs") or []:
        if not isinstance(pr, Mapping):
            continue
        info = classified.get(pr.get("number"))
        if not info or not info.get("is_external") or not info.get("merged"):
            continue
        created = _parse_iso_or_none(pr.get("createdAt"))
        merged = _parse_iso_or_none(pr.get("mergedAt"))
        if created is None or merged is None or merged < created:
            continue
        out.append((merged - created).total_seconds() / 3600.0)
    return out


#: D-IN-4 amendment (2026-09-13, ``docs/v2/02-RANKING-DESIGN.md`` D-IN-4):
#: the decision-rate cohort is the older half of the W1-3 180 d window, not
#: a second fetch beyond it -- every PR in it has had a full 90 d to be
#: decided by the time the envelope's own ``window_anchor`` was recorded.
_DR90_COHORT_START_DAYS = 180
_DR90_COHORT_END_DAYS = 90
_DR90_DECISION_WINDOW_DAYS = 90

#: 1.2 DR_90's ``method``: ``"primary"``, like the other pr_window-derived
#: rows (:func:`_resolve_ttfhr_hours`, :func:`_resolve_ttm_p90_hours`,
#: :func:`_resolve_substantive_review_rate`) -- this is the row's one and
#: only defined methodology (D-MISS-4 reserves ``"proxy:<name>"`` for a
#: *documented alternate* methodology such as 4.1's review/CI proxy; this
#: cohort computation over the real, collected pr_window envelope is not
#: that, it is the primary and only measurement 1.2 has). The packet
#: (W2-12) that authored this row specified a longer descriptive ``method``
#: string naming the cohort interval and the D-IN-3 caveat directly, but
#: ``Measured.__post_init__`` (``cvi.core.models.measured_problems``)
#: rejects any ``method`` other than exactly ``"primary"`` or a
#: ``"proxy:"``-prefixed one -- a whole-codebase invariant, not something
#: this row can special-case. The caveat that matters (author eligibility,
#: D-IN-3, is not applied yet -- every external author counts, pending
#: W3-1) is preserved here and on :func:`_resolve_dr90_decision_rate`
#: instead of in the method string.
_DR90_METHOD = constants_v2.METHOD_PRIMARY


def _dr90_decision_cohort(
    data: Mapping[str, Any], anchor: datetime.datetime
) -> tuple[int, int, int]:
    """1.2 DR_90's cohort and decision count over the raw pr_window envelope.

    Joins ``derived[].is_external`` with ``prs[].{createdAt,mergedAt,closedAt}``
    by PR ``number`` -- the same by-number join :func:`_merged_external_latency_hours`
    uses for 1.5. The cohort is every external PR whose ``createdAt`` falls
    in the half-open interval ``[anchor - 180d, anchor - 90d)``: the lower
    bound is inclusive (a PR opened exactly 180 d ago is still inside the
    W1-3 collector's trailing window), the upper bound is exclusive (a PR
    opened exactly 90 d ago has not yet had the full 90 d every cohort
    member is owed to be decided, so it is left for a later snapshot rather
    than measured early -- this is the amendment's whole point). A cohort
    member is decided when ``mergedAt`` or ``closedAt`` is no more than
    90 d after its own ``createdAt``, inclusive (exactly 90 d counts as
    decided). A PR whose ``createdAt`` is missing or does not parse as
    ISO-8601 cannot be windowed at all and is dropped from the cohort,
    counted separately (``malformed``) so the caller can name it in an
    ``insufficient_n`` detail instead of silently shrinking ``n`` with no
    explanation.

    Returns ``(decided_count, cohort_size, malformed_count)``.
    """
    classified = {
        entry.get("number"): entry
        for entry in (data.get("derived") or [])
        if isinstance(entry, Mapping) and entry.get("number") is not None
    }
    cohort_start = anchor - timedelta(days=_DR90_COHORT_START_DAYS)
    cohort_end = anchor - timedelta(days=_DR90_COHORT_END_DAYS)
    decision_window = timedelta(days=_DR90_DECISION_WINDOW_DAYS)
    decided = 0
    cohort = 0
    malformed = 0
    for pr in data.get("prs") or []:
        if not isinstance(pr, Mapping):
            continue
        info = classified.get(pr.get("number"))
        if not info or not info.get("is_external"):
            continue
        created = _parse_iso_or_none(pr.get("createdAt"))
        if created is None:
            malformed += 1
            continue
        if not (cohort_start <= created < cohort_end):
            continue
        cohort += 1
        merged = _parse_iso_or_none(pr.get("mergedAt"))
        closed = _parse_iso_or_none(pr.get("closedAt"))
        decided_by_merge = merged is not None and merged >= created and merged - created <= decision_window
        decided_by_close = closed is not None and closed >= created and closed - created <= decision_window
        if decided_by_merge or decided_by_close:
            decided += 1
    return decided, cohort, malformed


def _resolve_dr90_decision_rate(ctx: _V1Ctx) -> Measured | Unmeasured:
    """1.2 DR_90 decision rate (D-IN-4 amendment, 2026-09-13). No v1 summary
    field exists for this cohort (the v1 pipeline never computed it): both
    the value and ``n`` are computed here from the raw pr_window envelope
    via :func:`_dr90_decision_cohort`. Author eligibility (D-IN-3) is not
    yet applied -- packet W3-1 -- so today every external author counts;
    see :data:`_DR90_METHOD` for why that caveat lives in this docstring
    and not in the ``Measured.method`` value itself."""
    path, data, fetched_at = _pr_window_envelope(ctx)
    if path is None:
        return Unmeasured(reason="collector_failure", detail=_PR_WINDOW_UNRESOLVABLE_DETAIL)
    anchor = _parse_iso_or_none(data.get("window_anchor"))
    if anchor is None:
        return Unmeasured(
            reason="collector_failure",
            detail=f"pr_window envelope has no parseable window_anchor: {data.get('window_anchor')!r}",
        )
    decided, cohort, malformed = _dr90_decision_cohort(data, anchor)
    min_n = constants_v2.PARAM_MIN_N["dr90_decision_rate"]
    if cohort < min_n:
        detail = f"{cohort} external PRs in the decision cohort, need >= {min_n}"
        if malformed:
            detail += f" ({malformed} excluded: malformed or missing createdAt)"
        return Unmeasured(reason="insufficient_n", detail=detail)
    window_start = (anchor - timedelta(days=_DR90_COHORT_START_DAYS)).isoformat()
    window_end = (anchor - timedelta(days=_DR90_COHORT_END_DAYS)).isoformat()
    source_hash = _envelope_hash(path)
    ctx.referenced[str(path)] = source_hash
    return Measured(
        value=round(decided / cohort, 4), n=cohort, window_start=window_start, window_end=window_end,
        method=_DR90_METHOD, source=str(path), source_hash=source_hash, fetched_at=fetched_at,
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
    """1.1 TTFHR. Reads the W1-3 ``pr_window_ttfhr_median_hours`` /
    ``pr_window_external_measured_latency_count`` summary fields the derive
    hook already writes (``cvi.collect.github_graphql.derive_pr_window_fields``);
    no raw-envelope join needed for the value or ``n``, only to resolve
    provenance."""
    path, data, fetched_at = _pr_window_envelope(ctx)
    if path is None:
        return Unmeasured(reason="collector_failure", detail=_PR_WINDOW_UNRESOLVABLE_DETAIL)
    n_value = ctx.meta.get("pr_window_external_measured_latency_count")
    n = n_value if isinstance(n_value, int) and not isinstance(n_value, bool) else 0
    min_n = constants_v2.PARAM_MIN_N["ttfhr_hours"]
    if n < min_n:
        return Unmeasured(reason="insufficient_n",
                           detail=f"{n} external PRs with a measured TTFHR, need >= {min_n}")
    value = ctx.meta.get("pr_window_ttfhr_median_hours")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return Unmeasured(reason="collector_failure",
                           detail=f"pr_window_ttfhr_median_hours is not numeric: {value!r}")
    window_start, window_end = _pr_window_bounds(data)
    source_hash = _envelope_hash(path)
    ctx.referenced[str(path)] = source_hash
    return Measured(value=float(value), n=n, window_start=window_start, window_end=window_end,
                     method="primary", source=str(path), source_hash=source_hash, fetched_at=fetched_at)


def _resolve_substantive_review_rate(ctx: _V1Ctx) -> Measured | Unmeasured:
    """4.3 Substantive review rate. The rate itself is the W1-3
    ``pr_window_substantive_review_rate`` summary field; ``n`` (decided
    external PRs) has no summary field and is counted from the raw
    envelope's ``derived`` classifications."""
    path, data, fetched_at = _pr_window_envelope(ctx)
    if path is None:
        return Unmeasured(reason="collector_failure", detail=_PR_WINDOW_UNRESOLVABLE_DETAIL)
    decided_external = [
        entry for entry in (data.get("derived") or [])
        if isinstance(entry, Mapping) and entry.get("is_external") and entry.get("decided")
    ]
    n = len(decided_external)
    min_n = constants_v2.PARAM_MIN_N["substantive_review_rate"]
    if n < min_n:
        return Unmeasured(reason="insufficient_n",
                           detail=f"{n} decided external PRs, need >= {min_n}")
    value = ctx.meta.get("pr_window_substantive_review_rate")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return Unmeasured(reason="collector_failure",
                           detail=f"pr_window_substantive_review_rate is not numeric: {value!r}")
    window_start, window_end = _pr_window_bounds(data)
    source_hash = _envelope_hash(path)
    ctx.referenced[str(path)] = source_hash
    return Measured(value=float(value), n=n, window_start=window_start, window_end=window_end,
                     method="primary", source=str(path), source_hash=source_hash, fetched_at=fetched_at)


def _resolve_ttm_p90_hours(ctx: _V1Ctx) -> Measured | Unmeasured:
    """1.5 TTM_p90. No v1 summary field exists at all (unlike TTFHR and the
    review rate): both the value and ``n`` are computed here from the raw
    pr_window envelope's merged-external-PR latencies
    (:func:`_merged_external_latency_hours`)."""
    path, data, fetched_at = _pr_window_envelope(ctx)
    if path is None:
        return Unmeasured(reason="collector_failure", detail=_PR_WINDOW_UNRESOLVABLE_DETAIL)
    latencies = _merged_external_latency_hours(data)
    n = len(latencies)
    min_n = constants_v2.PARAM_MIN_N["ttm_p90_hours"]
    if n < min_n:
        return Unmeasured(reason="insufficient_n",
                           detail=f"{n} merged external PRs with recorded latency, need >= {min_n}")
    window_start, window_end = _pr_window_bounds(data)
    source_hash = _envelope_hash(path)
    ctx.referenced[str(path)] = source_hash
    return Measured(value=round(_nearest_rank_p90(latencies), 4), n=n, window_start=window_start,
                     window_end=window_end, method="primary", source=str(path), source_hash=source_hash,
                     fetched_at=fetched_at)


def _resolved_source_paths(source_field: Any, raw_root: Union[Path, str, None]) -> list[Path]:
    """Resolve a provenance ``source`` *list* to the subset of entries that
    exist as envelopes; ``[]`` when ``source_field`` is not a non-empty list.

    ``openssf_signals`` and ``pipeline_defenses`` (W2-9f, W1-7) are the only
    two rows whose W1-7 derive hook (``cvi.collect.pike_signals.
    derive_w17_fields``) records more than one contributing envelope path
    under one provenance entry -- every other row's ``source`` is a single
    string (:func:`_first_source`)."""
    if not isinstance(source_field, list) or not source_field:
        return []
    return [path for path in (_resolve_envelope(s, raw_root) for s in source_field) if path is not None]


def _resolve_openssf_criticality(ctx: _V1Ctx) -> Measured | Unmeasured:
    """2.2 OpenSSF criticality (W1-7, W2-9f). Reads the W1-7
    ``meta.openssf_signals`` dict (``cvi.collect.pike_signals.
    derive_w17_fields``: the ten raw Pike signals, written with confidence
    ``"derived"`` only when all ten source envelopes were available --
    D-MISS-1/02 section 4 row 2.2: never a partial score, class TP "only if
    a signal is missing") and computes C in [0, 1] via
    :func:`cvi.core.openssf.calculate_pike_criticality` (W2-9g: the one
    definition of the formula; ``cvi.derive`` imports ``cvi.core`` and
    stdlib only, and this module is itself both). A ``meta.openssf_signals``
    dict missing (or non-
    numeric) one of the ten fields is defensive: production's
    ``derive_pike_signals`` never emits a partial dict, but a hand-built or
    corrupted document might, and 2.2's whole-set-or-nothing rule must hold
    for it too -- the first missing field is named in the detail."""
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
            ctx.referenced[str(path)] = _envelope_hash(path)
        return Measured(value=round(openssf.calculate_pike_criticality(signals), 4), n=0, window_start=None,
                         window_end=None, method=constants_v2.METHOD_PRIMARY, source=str(paths[0]),
                         source_hash=_envelope_hash(paths[0]), fetched_at=fetched_at)
    if confidence_value == "missing":
        return Unmeasured(
            reason="third_party_gap",
            detail="one or more of the ten Pike signal sources is absent (02 section 4 2.2: never a partial score)",
        )
    if confidence_value == "collector_failure":
        # W1-7f items 1 and 3: cvi.collect.pike_signals.derive_w17_fields
        # (crashed, or found a windowed count's own fetch-time window_anchor
        # more than WINDOW_STALENESS_DAYS from as_of) recorded this sentinel
        # itself, with the specific reason in the companion detail field --
        # the generic "not recognized" fallback message below would discard
        # that reason and read as if the confidence value were unexpected
        # rather than deliberately set.
        detail = ctx.meta.get("openssf_signals_detail")
        return Unmeasured(reason="collector_failure",
                           detail=detail if isinstance(detail, str) and detail else "pike_signals collector failed")
    return Unmeasured(reason="collector_failure",
                       detail=f"v1 confidence {confidence_value!r} for 'openssf_signals' is not recognized")


#: ``cvi.collect.pike_signals.METHOD_PROXY``, mirrored (04 section 3: same
#: import restriction as :data:`_OPENSSF_WEIGHTS`) -- the review/CI proxy
#: method string 4.1 stores when no scorecard subset is usable.
_PIPELINE_DEFENSES_PROXY_METHOD: str = "proxy:review_ci"


def _resolve_pipeline_defenses(ctx: _V1Ctx) -> Measured | Unmeasured:
    """4.1 Pipeline defenses (W1-7, W2-9f). Reads the pre-computed W1-7
    ``meta.pipeline_defenses`` / ``meta.pipeline_defenses_method`` fields
    (``cvi.collect.pike_signals.pipeline_defenses``): method ``"primary"``
    (Scorecard 5-check mean, one scorecard envelope, n=0 -- a scalar) or
    ``"proxy:review_ci"`` (0.6·review-rate + 0.4·CI-pass-rate, the pr_window
    and runs envelopes). ``n`` for the proxy method is the merged-PR count
    backing the review-rate denominator (``cvi.collect.pike_signals.
    code_review_rate``), read from the pr_window envelope's own ``prs`` list
    -- 02 section 4 row 4.1's 10-merged-PR minimum sample applies to the
    proxy method only (``cvi.core.params.rigor._PIPELINE.proxy_min_n``).

    Unlike every other v1 field, ``scripts/derive.py`` never calls
    ``Bundle.missing`` for this one: when 4.1 is unmeasurable by either
    method, its confidence key is simply absent from the v1 document (not
    the string ``"missing"``) -- both are treated as the same TP absence
    here (02 section 4 row 4.1: "TP with proxy")."""
    confidence_value = ctx.confidence.get("pipeline_defenses")
    value = ctx.meta.get("pipeline_defenses")
    method = ctx.meta.get("pipeline_defenses_method")
    entry = ctx.provenance.get("pipeline_defenses")
    source_field = entry.get("source") if isinstance(entry, Mapping) else None
    fetched_at = entry.get("fetched_at") if isinstance(entry, Mapping) else None
    value_ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    method_ok = method in (constants_v2.METHOD_PRIMARY, _PIPELINE_DEFENSES_PROXY_METHOD)
    if confidence_value in ("verified", "derived", "flag") and value_ok and method_ok:
        paths = _resolved_source_paths(source_field, ctx.raw_root)
        if not paths or not isinstance(fetched_at, str) or not fetched_at:
            detail = (f"v1 source is not a resolvable envelope list: {source_field!r}" if not paths
                      else "v1 provenance for 'pipeline_defenses' has no fetched_at")
            return Unmeasured(reason="collector_failure", detail=detail)
        for path in paths:
            ctx.referenced[str(path)] = _envelope_hash(path)
        n = 0
        if method == _PIPELINE_DEFENSES_PROXY_METHOD:
            data = _envelope_data(paths[0])
            prs = data.get("prs") if isinstance(data, Mapping) else None
            n = sum(1 for pr in (prs or []) if isinstance(pr, Mapping) and pr.get("mergedAt") is not None)
        return Measured(value=float(value), n=n, window_start=None, window_end=None, method=method,
                         source=str(paths[0]), source_hash=_envelope_hash(paths[0]), fetched_at=fetched_at)
    if confidence_value in (None, "missing"):
        return Unmeasured(
            reason="third_party_gap",
            detail="no scorecard envelope with a usable check subset and no pr_window/runs envelopes to "
                   "derive the review/CI proxy (cvi.collect.pike_signals.pipeline_defenses)",
        )
    if confidence_value == "collector_failure":
        # W1-7f item 1: pike_signals.derive_w17_fields crashed before it
        # could report either method for this repo -- the companion detail
        # field names the exception (set alongside this sentinel; see
        # _resolve_openssf_criticality's identical branch).
        detail = ctx.meta.get("pipeline_defenses_detail")
        return Unmeasured(reason="collector_failure",
                           detail=detail if isinstance(detail, str) and detail else "pike_signals collector failed")
    return Unmeasured(reason="collector_failure",
                       detail=f"v1 confidence {confidence_value!r} for 'pipeline_defenses' is not recognized")


def _unmapped(detail: str) -> InputResolver:
    """A v2 input with no landed producer yet (or one whose landed collector
    cannot supply it — see each row's ``detail``): constant
    ``Unmeasured(collector_failure)``."""

    def resolve(ctx: _V1Ctx) -> Measured | Unmeasured:
        return Unmeasured(reason="collector_failure", detail=detail)

    return resolve


#: The v2 input mapping table (W2-9): one resolver per
#: ``constants_v2.PARAM_INPUT_IDS`` entry, in the same pillar order.
#:
#: | Input id | Reads | When absent |
#: |---|---|---|
#: | ttfhr_hours | meta.pr_window_ttfhr_median_hours + .pr_window_external_measured_latency_count (W1-3) | no pr_window envelope -> collector_failure; n<10 -> insufficient_n |
#: | dr90_decision_rate | raw pr_window envelope: derived[].is_external joined with prs[].{createdAt,mergedAt,closedAt} by number, cohort [anchor-180d,anchor-90d) (W2-12, D-IN-4 amendment) | no envelope -> collector_failure; cohort n<10 -> insufficient_n; malformed/missing createdAt excluded and named in that detail |
#: | mr_first_rate | (none landed) | collector_failure: needs W1-4 author history |
#: | gr14_ghost_rate | (none landed) | collector_failure: needs a snapshot as_of the v1-to-v2 loader is never given (04 section 8) |
#: | ttm_p90_hours | raw pr_window envelope: derived[].{is_external,merged} joined with prs[].{createdAt,mergedAt} by number (W1-3) | no envelope -> collector_failure; n<5 -> insufficient_n |
#: | dependents_deduped | meta.dependents_count when confidence=="derived" (W1-2 dependents hook); meta.ecosystem gates it (W1-2f) | ecosystem=="systems" -> not_applicable (D-MISS-5); verified -> collector_failure (raw usedByCount is not deduped); missing -> third_party_gap, maven detail names the uncollected pom.xml, unmapped ecosystem detail names it (W2-9f, W1-2f) |
#: | openssf_criticality | meta.openssf_signals, ten raw Pike signals (W1-7); C computed locally (mirrored, 04 section 3) | confidence missing, or any signal absent -> third_party_gap (never a partial score) |
#: | downloads_monthly | meta.monthly_downloads; meta.downloads_status (W1-8) refines the Unmeasured detail | third_party_gap, detail per downloads_status |
#: | legal_ip | meta.legal_ip_model, vocab-mapped | collector_failure; an unmappable enum is this row's own gap, not a whole-bundle rejection (W2-9f, W5) |
#: | elephant_factor | meta.elephant_factor (W1-5 override already lands here) | collector_failure |
#: | promotion_ladder | meta.promotion_ladder, vocab-mapped | collector_failure; an unmappable enum is this row's own gap, not a whole-bundle rejection (W2-9f, W5) |
#: | pipeline_defenses | meta.pipeline_defenses + .pipeline_defenses_method (W1-7): "primary" (scorecard) or "proxy:review_ci" (n = merged PRs) | confidence absent or missing -> third_party_gap (TP with proxy) |
#: | ai_posture | meta.ai_posture, vocab-validated against constants_v2.AI_POSTURE_VALUES (identity map, W2-9f, W4) | collector_failure; an out-of-set value is collector_failure too, never Measured |
#: | substantive_review_rate | meta.pr_window_substantive_review_rate (W1-3); n from the raw envelope | no envelope -> collector_failure; n<10 -> insufficient_n |
#: | newcomer_issue_supply | meta.active_unclaimed_gfis (W1-1 recount already lands here) | collector_failure |
#: | newcomer_conversion | (none landed) | collector_failure: needs the W1-4 newcomer cohort |
#: | rr90_return_rate | (none landed) | collector_failure: needs the W1-4 newcomer cohort |
#: | dx | (none landed) | collector_failure: v1 has CI runtime only, not the setup-signal half |
INPUT_MAPPINGS: dict[str, InputResolver] = {
    "ttfhr_hours": _resolve_ttfhr_hours,
    "dr90_decision_rate": _resolve_dr90_decision_rate,
    "mr_first_rate": _unmapped("needs W1-4 author history; the v1 pipeline collected none"),
    "gr14_ghost_rate": _unmapped(
        "1.4 needs a snapshot-relative 'now' to classify PR age; the v1-to-v2 loader receives no "
        "as_of and must not borrow the pr_window collector's own fetch-time clock as one (04 section 8) "
        "-- computing this belongs at derive time (scripts/derive.py), out of this packet's ownership"
    ),
    "ttm_p90_hours": _resolve_ttm_p90_hours,
    "dependents_deduped": _resolve_dependents_deduped,
    "openssf_criticality": _resolve_openssf_criticality,
    "downloads_monthly": _resolve_downloads_monthly,
    # W1-6 round 3 (Blocker BB3): "legal_ip" now reads W1-6's own bare
    # "legal_ip" field (identity vocab -- classify_legal_ip already returns
    # exactly constants_v2.LEGAL_IP_VALUES spellings), never the v1
    # "legal_ip_model" regex heuristic it was built to replace -- a repo
    # W1-6 measured FOUNDATION_CLA must not publish to v2 as Measured('DCO')
    # from the abandoned heuristic. No legacy fallback: "legal_ip_model"'s
    # CORP_CLA_RECIPROCAL/CORP_CLA_UNILATERAL split has no v2 counterpart to
    # fall back to (tests/derive/test_input_mappings.py::test_legal_ip and
    # ::test_legal_ip_unmappable_enum_over_correction_guard assert the old
    # "legal_ip_model" wiring and now fail by design -- reported, not fixed
    # here; W1_LEGAL_IP_VALUES above is unused as of this row and is left
    # for that packet to retire).
    "legal_ip": _vocab_scalar_resolver(
        "legal_ip", {value: value for value in constants_v2.LEGAL_IP_VALUES}),
    "elephant_factor": _plain_scalar_resolver("elephant_factor"),
    # W1-6 round 3 (Blocker BB3): "promotion_ladder" now accepts BOTH the v2
    # vocabulary as identity (VERIFIED/DOCUMENTED/AD_HOC/CLOSED -- what
    # W1-6's classify_promotion_ladder actually writes into this same bare
    # field) and the legacy v1 spellings via V1_LADDER_VALUES (a fallback
    # for any document not yet re-derived under W1-6 -- e.g. an old v1-only
    # spelling VERIFIED_PROMOTION/DOCUMENTED_PATHWAY/CLOSED_SINGLE_FIRM).
    # Before this row, VERIFIED/DOCUMENTED (genuine evidence) resolved to
    # Unmeasured while AD_HOC (no evidence) alone resolved Measured, purely
    # because the two vocabularies happen to spell "AD_HOC" the same way.
    "promotion_ladder": _vocab_scalar_resolver(
        "promotion_ladder",
        {**V1_LADDER_VALUES, **{value: value for value in constants_v2.PROMOTION_LADDER_VALUES}}),
    "pipeline_defenses": _resolve_pipeline_defenses,
    "ai_posture": _vocab_scalar_resolver("ai_posture", {value: value for value in constants_v2.AI_POSTURE_VALUES}),
    "substantive_review_rate": _resolve_substantive_review_rate,
    "newcomer_issue_supply": _plain_scalar_resolver("active_unclaimed_gfis"),
    "newcomer_conversion": _unmapped("needs the W1-4 newcomer cohort; the v1 pipeline collected none"),
    "rr90_return_rate": _unmapped("v2 5.3 is cohort-based (W1-4); v1 rr_90 approximates author gaps"),
    "dx": _unmapped("v2 5.4 blends CI runtime with setup signals; v1 stores a CI runtime mean only"),
}

assert set(INPUT_MAPPINGS) == set(constants_v2.PARAM_INPUT_IDS), (
    "INPUT_MAPPINGS must carry exactly one row per v2 input id"
)


# --------------------------------------------------------------------------- #
# W1-10: universe-entry star-velocity pass-through (D-IN-5, `03` section 6). #
#                                                                              #
# star_velocity_flag/stars_audited are NOT one of the 18 v2 parameter        #
# inputs -- D-IN-5 states plainly "Stars never enter the composite", and     #
# INPUT_MAPPINGS' own assert above closes `FeatureBundle.inputs` at exactly  #
# `constants_v2.PARAM_INPUT_IDS`, so these two facts can never be a row      #
# there. Nor is `cvi.core.models.RepoIdentity` (or any other FeatureBundle   #
# field) extended to carry them: `cvi/core/models.py` is out of this        #
# packet's ownership. This table is the "meta pass-through" a caller reads  #
# instead -- same _V1Ctx-based resolver shape as INPUT_MAPPINGS, built from #
# the identical v1 meta/provenance/confidence dicts `scripts/derive.py`'s   #
# universe hook writes into (`cvi.collect.universe.star_velocity_evidence`  #
# via `scripts/build_universe.py`, `star_velocity_flag`/`stars_audited`).   #
# `cvi.verify.detectors.DetectorInputs.star_lockstep_penalty_applied` (the  #
# `star_burst` detector's only input) and `cvi.store.writer`'s              #
# `stars_audited` column (G-006) are its two consumers -- see              #
# `resolve_universe_meta_facts` below for the public entry point a future  #
# packet (W4-4) calls alongside `load_bundle` for the same document.        #
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
            ctx.referenced[str(path)] = source_hash
            return Measured(value=1 if value else 0, n=0, window_start=None, window_end=None,
                             method="primary", source=str(path), source_hash=source_hash,
                             fetched_at=fetched_at)
        detail = (f"v1 source is not an envelope: {source!r}" if path is None
                  else "v1 provenance for 'star_velocity_flag' has no fetched_at")
        return Unmeasured(reason="collector_failure", detail=detail)
    if confidence_value == "missing":
        return Unmeasured(
            reason="collector_failure",
            detail="universe entry never audited star velocity for this repo "
                   "(star-history fetch failed, cvi.collect.universe)",
        )
    return Unmeasured(reason="collector_failure",
                       detail=f"v1 confidence {confidence_value!r} for 'star_velocity_flag' is not recognized")


#: The two universe-entry facts, keyed exactly like INPUT_MAPPINGS but kept
#: in a separate table so INPUT_MAPPINGS' own 18-row assert stays exact.
UNIVERSE_META_MAPPINGS: dict[str, InputResolver] = {
    "star_velocity_flag": _resolve_star_velocity_flag,
    "stars_audited": _plain_scalar_resolver(
        "stars_audited",
        missing_reason="collector_failure",
        missing_detail="universe entry never audited star velocity for this repo "
                        "(star-history fetch failed, cvi.collect.universe)",
    ),
}


def resolve_universe_meta_facts(
    document: Mapping[str, Any], raw_root: Union[Path, str, None] = None
) -> dict[str, Measured | Unmeasured]:
    """Resolve :data:`UNIVERSE_META_MAPPINGS` over a v1 derived document --
    the same document :func:`load_bundle` reads, but returning the two
    universe-entry star-velocity facts instead of a
    :class:`~cvi.core.models.FeatureBundle` (they are not part of its
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
    for a ghost/deleted-account event (W2-9): GitHub returns an empty
    ``author_login`` for these and ``PRWindowEvent`` requires a non-empty one
    (it is an author-keyed record). Other malformed events still append to
    ``problems`` (pre-W2-9 behaviour, unchanged: the whole bundle is
    rejected) and also return ``None``; the caller tells the two apart by
    checking ``author_login`` itself.
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
        referenced[str(github_meta)] = hash_
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
        input_id: INPUT_MAPPINGS[input_id](ctx) for input_id in constants_v2.PARAM_INPUT_IDS
    }

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
                # Window bounds are author-irrelevant (W2-9): a ghost-account
                # event still marks calendar coverage even though it cannot
                # become an author-keyed PRWindowEvent. A genuinely malformed
                # (non-ghost) entry is excluded from bounds entirely, same as
                # pre-W2-9 (its problems() entry, appended above, rejects the
                # whole bundle) -- folding its unparsed timestamps in here
                # would let a single-event bundle's PRWindow invariant fire
                # before that more informative per-event error is reported.
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
            referenced[str(path)] = _envelope_hash(path)

    def iso(ts: str) -> datetime.datetime:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))

    window = PRWindow(
        start=min(all_created, key=iso) if all_created else None,
        end=max(all_timestamps, key=iso) if all_timestamps else None,
        truncated=False,  # the v1 document carries no truncation marker
        events=tuple(events),
    )
    # W1-3/W1-4 collect the cohorts; the v1 pipeline produced none.
    cohorts = Cohorts(decision_pr_numbers=(), newcomer_authors=())

    if problems:
        raise BundleValidationError(problems)
    bundle = FeatureBundle(
        schema_version=constants_v2.SCHEMA_VERSION,
        repo=repo,
        inputs=inputs,
        pr_window=window,
        cohorts=cohorts,
        provenance_hash=models.feature_provenance_hash(referenced),
    )
    bundle.validate()
    _set_dropped_event_count(bundle, dropped_ghost_author_count)
    return bundle
