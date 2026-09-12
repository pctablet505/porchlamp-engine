"""Load, validate and dump FeatureBundle v2 documents (packet W2-1).

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
"""
from __future__ import annotations

import datetime
import json
from dataclasses import fields
from pathlib import Path
from typing import Any, Mapping, Union

from cvi.core import constants_v2, models
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

#: v1 meta fields whose value and provenance match a v2 input definition.
V1_FIELD_TO_INPUT: dict[str, str] = {
    "elephant_factor": "elephant_factor",
    "legal_ip_model": "legal_ip",
    "promotion_ladder": "promotion_ladder",
    "ai_posture": "ai_posture",
    "monthly_downloads": "downloads_monthly",
}

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

#: The 13 v2 inputs no v1 collector produced, with the factual reason.
NO_V2_COLLECTOR: dict[str, str] = {
    "ttfhr_hours": "v1 deriver does not compute TTFHR over the PR window",
    "dr90_decision_rate": "v2 parameter; the v1 pipeline collected no decision cohort",
    "mr_first_rate": "needs W1-4 author history; the v1 pipeline collected none",
    "gr14_ghost_rate": "v2 parameter; the v1 pipeline collected no ghost window",
    "ttm_p90_hours": "v2 parameter; the v1 pipeline collected no merge-latency distribution",
    "dependents_deduped": "v1 dependents_count is not deduplicated by root organisation (v2 2.1)",
    "openssf_criticality": "v1 leaves the ten OpenSSF signals uncollected; v2 2.2 needs all ten",
    "pipeline_defenses": "v2 4.1 scores Scorecard checks or a proxy; v1 stores only the composite score",
    "substantive_review_rate": "v2 4.3 needs review threads; v1 stores comment totals only",
    "newcomer_issue_supply": "v2 5.1 uses a 90-day window and alias labels; v1 counts 30-day GFI issues",
    "newcomer_conversion": "needs the W1-4 newcomer cohort; the v1 pipeline collected none",
    "rr90_return_rate": "v2 5.3 is cohort-based (W1-4); v1 rr_90 approximates author gaps",
    "dx": "v2 5.4 blends CI runtime with setup signals; v1 stores a CI runtime mean only",
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


def _map_v1_value(input_id: str, value: Any, problems: list[str]) -> Any:
    table = V1_LEGAL_IP_VALUES if input_id == "legal_ip" else (
        V1_LADDER_VALUES if input_id == "promotion_ladder" else None
    )
    if table is None:
        return value
    if value in table:
        return table[value]
    problems.append(f"{input_id}: v1 value {value!r} has no v2 vocabulary mapping")
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

    inputs: dict[str, Measured | Unmeasured] = {
        input_id: Unmeasured(reason="collector_failure", detail=NO_V2_COLLECTOR[input_id])
        for input_id in constants_v2.PARAM_INPUT_IDS
        if input_id not in V1_FIELD_TO_INPUT.values()
    }
    for v1_field, input_id in V1_FIELD_TO_INPUT.items():
        confidence_value = confidence.get(v1_field)
        value = meta.get(v1_field)
        entry = provenance.get(v1_field)
        source = _first_source(entry)
        fetched_at = entry.get("fetched_at") if isinstance(entry, Mapping) else None
        value_ok = isinstance(value, (int, str)) and not isinstance(value, bool)
        if confidence_value in ("verified", "derived", "flag") and value_ok:
            path = _resolve_envelope(source, raw_root)
            mapped = _map_v1_value(input_id, value, problems)
            if path is not None and isinstance(fetched_at, str) and fetched_at and mapped is not None:
                source_hash = _envelope_hash(path)
                referenced[str(path)] = source_hash
                inputs[input_id] = Measured(
                    value=mapped, n=0, window_start=None, window_end=None, method="primary",
                    source=str(path), source_hash=source_hash, fetched_at=fetched_at,
                )
                continue
            if path is None:
                detail = f"v1 source is not an envelope: {source!r}"
            elif not isinstance(fetched_at, str) or not fetched_at:
                detail = f"v1 provenance for {v1_field!r} has no fetched_at"
            else:
                detail = f"v1 value {value!r} for {v1_field!r} has no v2 vocabulary mapping"
            inputs[input_id] = Unmeasured(reason="collector_failure", detail=detail)
        elif confidence_value == "missing":
            if input_id == "downloads_monthly":
                inputs[input_id] = Unmeasured(
                    reason="third_party_gap", detail="registry returned no downloads data"
                )
            else:
                inputs[input_id] = Unmeasured(
                    reason="collector_failure", detail="v1 deriver recorded no value"
                )
        else:
            inputs[input_id] = Unmeasured(
                reason="collector_failure",
                detail=f"v1 confidence {confidence_value!r} for {v1_field!r} is not recognized",
            )

    raw_events = document.get("pr_events")
    events: list[PRWindowEvent] = []
    if isinstance(raw_events, list):
        for index, entry in enumerate(raw_events):
            if not isinstance(entry, Mapping):
                problems.append(f"v1 pr_events[{index}] is not a mapping")
                continue
            try:
                events.append(
                    PRWindowEvent(
                        pr_number=entry["pr_number"],
                        created_at=entry["created_at"],
                        closed_at=entry.get("closed_at"),
                        merged_at=entry.get("merged_at"),
                        author_login=entry.get("author_login", ""),
                        author_association=entry.get("author_association", "NONE"),
                        is_external=bool(entry.get("is_external")),
                        first_human_review_at=entry.get("first_human_review_at"),
                    )
                )
            except (KeyError, BundleValidationError) as error:
                problems.append(f"v1 pr_events[{index}]: {error!s}")
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

    created = [event.created_at for event in events]
    all_timestamps = [
        ts for event in events for ts in (event.created_at, event.closed_at, event.merged_at) if ts
    ]
    window = PRWindow(
        start=min(created, key=iso) if created else None,
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
    return bundle
