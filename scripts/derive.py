"""Derive a RepoMeta feature bundle from the raw JSON cache for one repo.

Reads data/raw/<collector>/<owner>__<name>.json envelopes and writes
data/derived/<owner>__<name>.json:
  {"meta": {...RepoMeta fields...}, "pr_events": [...],
   "provenance": {field: {source, fetched_at}},
   "confidence": {field: "verified|derived|missing|flag"}}

Confidence vocabulary:
  verified  single authoritative source value
  derived   heuristic / approximation (documented at each derivation site)
  missing   no source file (or null value); the default is emitted
  flag      two sources disagree beyond tolerance (scorecard cross-check)

``--as-of <ISO-8601>`` is the reference time every window is measured against (the
snapshot's ``as_of``). It is required with ``--all``, so a batch derivation never reads
the wall clock and two runs over the same cache give identical bundles. A pinned
derivation reads only the cache: ``--fetch`` (which stamps its own fetch time) is refused
with it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import urllib.request
from dataclasses import asdict
from itertools import pairwise
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Sequence

from porchlamp.collect import commits as commit_domains
from porchlamp.collect import validate
from porchlamp.collect.ci_runs import commit_waits
from porchlamp.collect.depsdev import package_key, package_name_for_repo
from porchlamp.collect.envelope import raw_envelope_path, read_envelope, relative_envelope_path
from porchlamp.collect.universe import UNIVERSE_ENTRY_COLLECTOR, StarVelocityRecord
from porchlamp.constants import BOT_LOGIN_MARKERS
from porchlamp.models import (
    AIPosture,
    AuthorAssociation,
    Ecosystem,
    LegalIPModel,
    PromotionLadder,
    RawPREvent,
)
from porchlamp.provenance import utc_now_iso
from porchlamp.paths import data_root

DATA_DIR = data_root()
GFI_WINDOW_DAYS = 30
RR_WINDOW_DAYS = 90
COMMITS_WINDOW_DAYS = 180
SCORECARD_MISMATCH = 0.5
FOUNDATION_ORGS = {
    "apache", "kubernetes", "cncf", "linuxfoundation", "eclipse",
    "openjs-foundation", "fdiooss", "openstack", "llvm", "chaoss",
}
# GitHub's real author_association enum; model enum collapses FIRST_TIMER /
# FIRST_TIME_CONTRIBUTOR -> FIRST_TIME and CONTRIBUTOR -> NONE (external, not
# first-time; is_external is carried separately).
EXTERNAL_ASSOC = {"NONE", "FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR", "CONTRIBUTOR"}
ASSOC_MAP = {
    "NONE": AuthorAssociation.NONE,
    "FIRST_TIMER": AuthorAssociation.FIRST_TIME,
    "FIRST_TIME_CONTRIBUTOR": AuthorAssociation.FIRST_TIME,
    "CONTRIBUTOR": AuthorAssociation.NONE,
    "COLLABORATOR": AuthorAssociation.MEMBER,
    "MEMBER": AuthorAssociation.MEMBER,
    "OWNER": AuthorAssociation.OWNER,
}


def _parse_dt(s: Any) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _is_bot(login: Any) -> bool:
    low = (login or "").lower()
    return any(m in low for m in BOT_LOGIN_MARKERS)


def _label_names(labels: Any) -> set[str]:
    out = set()
    for lab in labels or []:
        out.add((lab if isinstance(lab, str) else (lab or {}).get("name", "")).lower())
    return out


def _envelope_sha256(
    source: Any,
    cache: dict[str, Optional[str]],
    data_dir: Path = DATA_DIR,
) -> Optional[str]:
    """Attested sha256 of the envelope at ``source`` (memoized), or ``None`` (`03` §5).

    The hash recorded here is :attr:`porchlamp.collect.envelope.Envelope.sha256` — native
    when the envelope carries one, otherwise the same backfilled canonical hash of its
    ``data`` field that :func:`porchlamp.snapshot.manifest.envelope_entries` uses —
    so derive and verify agree on what "the envelope's hash" means without a
    second definition.

    ``source`` is whatever one call site passed: usually a ``src(collector,
    key)`` envelope path, sometimes a literal like ``"cli"`` or a raw URL,
    sometimes ``None``. Anything that is not a readable envelope path yields
    ``None`` silently, exactly as a non-path ``source`` is silently
    unverifiable downstream — this function only ever adds information, never
    changes what ``source`` itself records.
    """
    if not isinstance(source, str) or not source:
        return None
    if source not in cache:
        try:
            p = Path(source)
            if not p.is_file() and not p.is_absolute():
                cand = Path(data_dir) / p
                p = cand if cand.is_file() else DATA_DIR / p
            cache[source] = read_envelope(p).sha256
        except (OSError, json.JSONDecodeError):
            cache[source] = None
    return cache[source]


class Bundle:
    """Accumulates meta / provenance / confidence maps.

    Every provenance entry whose ``source`` (or, for a
    list-valued ``source``, any element of it) resolves to a readable envelope
    also records that envelope's hash under ``"sha256"``, so
    ``scripts/verify.py`` can verify content integrity rather than only path
    existence. Every ``b.set(...)`` call across the collector blocks below funnels
    through here.
    """

    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        self.meta: dict[str, Any] = {}
        self.prov: dict[str, Any] = {}
        self.conf: dict[str, str] = {}
        self._hash_cache: dict[str, Optional[str]] = {}
        self._data_dir: Path = data_dir

    def set(self, field: str, value: Any, source: Optional[str],
            fetched_at: Optional[str], confidence: str) -> None:
        self.meta[field] = value
        entry: dict[str, Any] = {"source": source, "fetched_at": fetched_at}
        if isinstance(source, list):
            hashes = [_envelope_sha256(item, self._hash_cache, self._data_dir) for item in source]
            if any(h is not None for h in hashes):
                entry["sha256"] = hashes
        else:
            digest = _envelope_sha256(source, self._hash_cache, self._data_dir)
            if digest is not None:
                entry["sha256"] = digest
        self.prov[field] = entry
        self.conf[field] = confidence

    def missing(self, field: str, default: Any) -> None:
        self.set(field, default, None, None, "missing")


def _load(data_dir: Path, collector: str, key: str) -> tuple[Optional[Any], Optional[str]]:
    """Return (envelope.data, fetched_at) or (None, None) when absent."""
    p = data_dir / "raw" / collector / f"{key}.json"
    if not p.is_file():
        return None, None
    try:
        env = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, None
    return env.get("data"), env.get("fetched_at") or env.get("collected_at")


#: Ecosystems whose registry has a downloads source (``scripts/gather.py``).
_DOWNLOAD_ECOSYSTEMS = frozenset({"pypi", "npm", "crates"})


def attributed_downloads(dl: Optional[dict], dep: Optional[dict], ecosystem: str,
                         owner: str, name: str) -> tuple[Optional[dict], bool]:
    """``(downloads data to use, whether a count was withheld as not this repo's)``.

    A downloads count belongs to a package, and the downloads file is keyed by
    the repo's NAME, which is only a guess at the package name: npm ``router``
    carried 291 M downloads a month on three repos, none of which is its owner.
    The dependents envelope, when the attributing resolver wrote it (it records
    ``guess_linked``), names the package deps.dev links to this repo
    (``package``; None when it links none). The downloads envelope names the
    package it counted (``package``; absent on a file written before
    attribution, which counted the name guess). The count is the repo's only
    when both name the same package. A repo with no linked package has no
    downloads to show, whatever the file under its name holds.

    Without an attributed dependents envelope (older data, or an ecosystem
    with no downloads registry) the downloads data passes through unchanged:
    there is nothing to compare it with.
    """
    if ecosystem not in _DOWNLOAD_ECOSYSTEMS or not isinstance(dep, dict) \
            or "guess_linked" not in dep:
        return dl, False
    package = dep.get("package")
    if not isinstance(package, str) or not package:
        return None, True
    if not isinstance(dl, dict):
        return dl, False
    counted = dl.get("package") or package_name_for_repo(owner, name, ecosystem)
    if package_key(ecosystem, str(counted)) != package_key(ecosystem, package):
        return None, True
    return dl, False


def _body_cache_path(data_dir: Path, owner: str, name: str, fname: str) -> Path:
    """The bodies-cache envelope path for one governance file, shared by
    :func:`_body` and provenance recording."""
    return data_dir / "raw" / "bodies" / f"{owner}__{name}__{fname}.json"


def _body(data_dir: Path, owner: str, name: str, fname: str,
          fetch: bool) -> tuple[Optional[str], Optional[str]]:
    """File body from the bodies cache, optionally fetched + cached."""
    p = _body_cache_path(data_dir, owner, name, fname)
    if p.is_file():
        try:
            env = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            env = None
        if isinstance(env, dict):
            return (env.get("data") or {}).get("body"), env.get("fetched_at")
    if not fetch:
        return None, None
    validate.github_segment(owner, "owner")
    validate.github_segment(name, "name")
    validate.relative_file_path(fname, "governance file name")
    url = validate.https_url(f"https://raw.githubusercontent.com/{owner}/{name}/HEAD/{fname}",
                             validate.RAW_GITHUB_HOSTS, "raw content")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception:  # review-ok: gate-network — any fetch failure caches None body
        text = None
    at = utc_now_iso(datetime.now(timezone.utc))
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps({"fetched_at": at, "source": url,
                                   "data": {"body": text}}), encoding="utf-8")
        os.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return text, at


def elephant_factor(contributors: Optional[list[dict]]) -> int:
    """Spec 3.2 login-proxy fallback: minimum number of distinct logins whose
    contributions reach >= 50% of total contributions. The primary EF
    comes from commit-author email domains (via porchlamp.collect.commits); this approximation is used only
    when no commits_365d envelope exists. Greedy descending accumulation
    yields the minimum count; EF >= 1 always (spec floor)."""
    if not contributors:
        return 1
    total = sum(int(c.get("contributions") or 0) for c in contributors)
    if total <= 0:
        return 1
    acc, ef = 0, 0
    for c in sorted(contributors, key=lambda c: -int(c.get("contributions") or 0)):
        acc += int(c.get("contributions") or 0)
        ef += 1
        if acc / total >= 0.5:
            return ef
    return ef


def elephant_factor_sample_n(records: list, now: datetime) -> int:
    """The number of commits ``commit_domains.elephant_factor(records, now)``
    divides by: non-bot commits in its trailing window (undated rows kept,
    as it keeps them) whose author resolves to an org."""
    window = commit_domains._human_window(  # the EF's own window rule, reused so n counts what EF divides by
        records, now, commit_domains.COMMIT_WINDOW_DAYS, include_undated=True)
    return sum(1 for record in window if commit_domains.org_key(record.actor) is not None)


def _legal_ip(body: Optional[str], license_spdx: str, owner: str) -> LegalIPModel:
    """Heuristic: CLA mention -> FOUNDATION_CLA (foundation org or Apache-2.0)
    else CORP_CLA_UNILATERAL; DCO sign-off -> DCO; Apache-2.0 + foundation org
    -> FOUNDATION_CLA; default DCO (permissive default, spec 3.1 best regime)."""
    text = body or ""
    foundation = owner.lower() in FOUNDATION_ORGS or owner.lower().endswith("-foundation")
    ap2 = "apache-2.0" in license_spdx.lower()
    if re.search(r"\bCLA\b|contributor license agreement", text, re.I):
        return LegalIPModel.FOUNDATION_CLA if (foundation or ap2) else LegalIPModel.CORP_CLA_UNILATERAL
    if re.search(r"signed[\s-]?off|\bDCO\b", text, re.I):
        return LegalIPModel.DCO
    if ap2 and foundation:
        return LegalIPModel.FOUNDATION_CLA
    return LegalIPModel.DCO


def _ai_posture(corpus: str) -> AIPosture:
    """Heuristic over README/CONTRIBUTING/AGENTS.md/CLAUDE.md bodies:
    AI/LLM mention + ban wording -> PROSCRIBED; attribution trailers /
    stated AI policy -> REGULATED; no evidence -> AD_HOC (spec 4.2)."""
    low = corpus.lower()
    mentions_ai = re.search(r"\bai\b|\bllm\b|chatgpt|copilot|generative", low)
    if mentions_ai and re.search(r"prohibit|forbid|banned|\bban\b|disallow|not accept|will be rejected", low):
        return AIPosture.PROSCRIBED
    if re.search(r"assisted-by|generated-by|co-authored-by|ai[- ]assist|ai polic|ai usage|attribution", low):
        return AIPosture.REGULATED
    return AIPosture.AD_HOC


def _promotion_ladder(corpus: str) -> PromotionLadder:
    """Heuristic over CONTRIBUTING/CODE_OF_CONDUCT: documented triage /
    reviewer / maintainer pathway -> DOCUMENTED_PATHWAY, else AD_HOC (3.3)."""
    if re.search(r"triage|reviewer|become a (maintainer|member)|promotion|"
                 r"steering|tsc|core team|career path", corpus, re.I):
        return PromotionLadder.DOCUMENTED_PATHWAY
    return PromotionLadder.AD_HOC


_DX_SETUP_SCORE_MAP: dict[int, float] = {0: 0.0, 1: 40.0, 2: 70.0, 3: 100.0}
#: D-IN-4 "CI runs": the median is taken over at most this many commits.
_DX_MAX_COMMITS = 30


def _dx_commit_waits(runs: Optional[list[dict]]) -> list[float]:
    """Seconds each of the newest pull-request commits waited for CI
    (``porchlamp.collect.ci_runs.commit_waits``), capped at D-IN-4's window."""
    return commit_waits(runs or [])[:_DX_MAX_COMMITS]


_DX_CONTRIB_KEYWORDS = (
    r"\b(?:setup|set\s+up|dev(?:elopment|eloping)?|build(?:ing)?|"
    r"install(?:ation|ing)?|getting\s+started|local|environment|"
    r"test(?:ing|s)?|quickstart|quick\s+start|how\s+to\s+contribute)\b"
)
_DX_README_KEYWORDS = (
    r"\b(?:install(?:ation|ing)?|build(?:ing)?|setup|set\s+up|"
    r"getting\s+started|quick\s*start|usage|development)\b"
)
_DX_CONTRIB_SETUP_RX = re.compile(
    rf"(?:^|\n)\s*(?:#+\s*[^\n]*?{_DX_CONTRIB_KEYWORDS}|[^\n]*?{_DX_CONTRIB_KEYWORDS}[^\n]*\n[=\-]{{3,}})",
    re.IGNORECASE,
)
_DX_README_INSTALL_RX = re.compile(
    rf"(?:^|\n)\s*(?:#+\s*[^\n]*?{_DX_README_KEYWORDS}|[^\n]*?{_DX_README_KEYWORDS}[^\n]*\n[=\-]{{3,}})",
    re.IGNORECASE,
)
_DX_ENV_BASENAMES: frozenset[str] = frozenset({
    "devcontainer.json", ".devcontainer.json", "flake.nix", "default.nix", "shell.nix",
})


def _has_dx_env_file(tree_paths: Sequence[str]) -> bool:
    for p in tree_paths:
        if not isinstance(p, str):
            continue
        low = p.lower()
        base = low.rsplit("/", 1)[-1]
        if (
            ".devcontainer/" in low
            or low.startswith(".devcontainer")
            or base in _DX_ENV_BASENAMES
            or base.startswith("dockerfile")
            or base.startswith("docker-compose")
            or base.startswith("compose.y")
        ):
            return True
    return False


def _derive_dx_54(
    runs: Optional[list[dict]],
    tree: Optional[dict],
    contrib_body: Optional[str],
    readme_body: Optional[str],
    gov: Optional[dict] = None,
) -> tuple[Optional[float], int]:
    """02 §4 5.4 DX: 0.5 * runtime_score + 0.5 * setup_signals_score.

    Runtime score: the median CI wait over the newest 30 pull-request commits
    with a usable wait (``porchlamp.collect.ci_runs.commit_waits`` over the
    ``actions_runs`` envelope: a commit's wait is its slowest workflow run):
    100 if <= 20 min, -5/min after, floor 0; ``None`` (unmeasured) if ``runs``
    is None or no commit has a usable wait. The count returned is the number
    of commits the median is taken over.
    Setup signals (0/1/2/3 -> 0/40/70/100):
      1. devcontainer or Dockerfile or flake.nix in tree paths,
      2. CONTRIBUTING.md with a setup/development section,
      3. README.md with an install/build section.
    """
    if runs is None:
        return None, 0
    waits = _dx_commit_waits(runs)
    if not waits:
        return None, 0
    median_minutes = statistics.median(waits) / 60.0
    if median_minutes <= 20.0:
        runtime_score = 100.0
    else:
        runtime_score = max(0.0, 100.0 - 5.0 * (median_minutes - 20.0))

    tree_paths = (tree or {}).get("paths") or [] if isinstance(tree, dict) else []
    sig_env = _has_dx_env_file(tree_paths)

    eff_contrib = contrib_body
    if not eff_contrib and isinstance(gov, dict):
        gov_files = gov.get("files") or {}
        if isinstance(gov_files, dict):
            c_entry = gov_files.get("CONTRIBUTING.md")
            if isinstance(c_entry, dict) and isinstance(c_entry.get("body"), str):
                eff_contrib = c_entry["body"]
    sig_contrib = bool(eff_contrib and _DX_CONTRIB_SETUP_RX.search(eff_contrib))
    sig_readme = bool(readme_body and _DX_README_INSTALL_RX.search(readme_body))

    signal_count = int(sig_env) + int(sig_contrib) + int(sig_readme)
    setup_score = _DX_SETUP_SCORE_MAP[signal_count]
    return round(0.5 * runtime_score + 0.5 * setup_score, 4), len(waits)


def _jsonable(v: Any) -> Any:
    if isinstance(v, PromotionLadder):
        return v.name
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def derive_repo(owner: str, name: str, ecosystem: Ecosystem,
                data_dir: Path = DATA_DIR, fetch: bool = False,
                now: Optional[datetime] = None) -> dict[str, Any]:
    """Build the feature bundle (meta + pr_events + provenance + confidence)."""
    now = now or datetime.now(timezone.utc)
    key = f"{owner}__{name}"
    b = Bundle(data_dir)
    b.set("owner", owner, "cli", None, "derived")
    b.set("name", name, "cli", None, "derived")
    b.set("ecosystem", ecosystem, "cli", None, "derived")

    gh, gh_at = _load(data_dir, "github_meta", key)
    gh = gh or {}
    prs_raw, prs_at = _load(data_dir, "pr_events", key)
    reviews, rev_at = _load(data_dir, "pr_reviews", key)
    contribs, contribs_at = _load(data_dir, "contributors", key)
    commits, commits_at = _load(data_dir, "commits_180d", key)
    gfis, gfi_at = _load(data_dir, "gfi", key)
    runs, runs_at = _load(data_dir, "actions_runs", key)
    deps, deps_at = _load(data_dir, "depsdev", key)
    sc, sc_at = _load(data_dir, "scorecard", key)
    tree, tree_at = _load(data_dir, "tree", key)
    dl, dl_at = _load(data_dir, "downloads", f"{ecosystem.value}__{name}")
    dep, dep_at = _load(data_dir, "dependents", key)
    dl, dl_withheld = attributed_downloads(dl, dep, ecosystem.value, owner, name)

    def _rel(p: Path | str) -> str:
        """Provenance path, relative to the data root this derivation is
        reading from.

        Relative to ``data_dir``, not to the module default: a derivation run
        against any other root -- a test corpus, another machine's checkout,
        a second sample -- was recording an absolute path, which names the
        machine that derived it and cannot be resolved anywhere else. The
        path is part of the hashed record, so that made those records
        non-portable and unreproducible (docs/v2/00-README.md).

        A path outside ``data_dir`` is a bug in the caller rather than
        something to paper over, and ``relative_envelope_path`` raises for
        it. Every caller here builds its path with ``raw_envelope_path
        (data_dir, ...)``, so that cannot happen by construction.
        """
        return relative_envelope_path(data_dir, p)

    def src(c: str, k: str) -> str:
        # Standard envelope-path formula, shared with porchlamp.collect.pike_signals's
        # env_path() via porchlamp.collect.envelope.
        return _rel(raw_envelope_path(data_dir, c, k))

    # --- stars / forks (github_meta primary; deps.dev is the verify step) ---
    for f, gk in (("stars", "stars"), ("forks", "forks")):
        if gh.get(gk) is not None:
            b.set(f, int(gh[gk]), src("github_meta", key), gh_at, "verified")
        else:
            b.missing(f, 0)

    # --- Universe-entry star-velocity audit pass-through (D-IN-5) ---
    # porchlamp.collect.universe.star_velocity_evidence (scripts/build_universe.py)
    # writes one UNIVERSE_ENTRY_COLLECTOR envelope per repo regardless of
    # whether the audit succeeded (its own `record.to_json()`, which is
    # exactly the shape a failed fetch also serializes -- {"measured":
    # false, "reason": "collector_failure", ...}). "missing" here (never a
    # silent True/False/0 default) means confidence, not the meta value
    # itself: porchlamp.derive.bundle.UNIVERSE_META_MAPPINGS checks confidence
    # first and never trusts the None placeholder.
    uni_doc, uni_at = _load(data_dir, UNIVERSE_ENTRY_COLLECTOR, key)
    uni_record: Optional[StarVelocityRecord] = None
    if isinstance(uni_doc, dict):
        try:
            uni_record = StarVelocityRecord.from_json(uni_doc)
        except (ValueError, KeyError, TypeError):
            uni_record = None
    if uni_record is not None and uni_record.measured:
        uni_src = src(UNIVERSE_ENTRY_COLLECTOR, key)
        b.set("star_velocity_flag", bool(uni_record.star_velocity_flag), uni_src, uni_at, "verified")
        b.set("stars_audited", int(uni_record.stars_audited), uni_src, uni_at, "verified")
        b.set("star_velocity", dict(uni_record.star_velocity or {}), uni_src, uni_at, "verified")
    else:
        b.missing("star_velocity_flag", None)
        b.missing("stars_audited", None)
        b.missing("star_velocity", None)

    # --- monthly_downloads (null -> 0, confidence missing) ---
    monthly = (dl or {}).get("monthly")
    if monthly is not None:
        b.set("monthly_downloads", int(monthly), src("downloads", f"{ecosystem.value}__{name}"), dl_at, "verified")
    else:
        b.missing("monthly_downloads", 0)

    # --- dependents_count (deps.dev usedby_count) ---
    usedby = (deps or {}).get("usedby_count")
    if usedby is not None:
        b.set("dependents_count", int(usedby), src("depsdev", key), deps_at, "verified")
    else:
        b.missing("dependents_count", 0)

    # --- elephant_factor (spec 3.2, org-proxy approximation above) ---
    if contribs is not None:
        b.set("elephant_factor", elephant_factor(contribs), src("contributors", key), contribs_at, "derived")
    else:
        b.missing("elephant_factor", 1)

    # --- active_nonbot_commits_180d (BOT_LOGIN_MARKERS, case-insensitive) ---
    if commits is not None:
        b.set("active_nonbot_commits_180d",
              sum(1 for c in commits if not _is_bot(c.get("author_login"))),
              src("commits_180d", key), commits_at, "verified")
    else:
        b.missing("active_nonbot_commits_180d", 0)

    # --- PR events -> RawPREvent ---
    events: list[RawPREvent] = []
    for e in prs_raw or []:
        created = _parse_dt(e.get("created_at"))
        closed, merged = _parse_dt(e.get("closed_at")), _parse_dt(e.get("merged_at"))
        raw_assoc = str(e.get("author_association") or "NONE").upper()
        first_rev = _parse_dt((reviews or {}).get(str(e.get("number"))))
        latency = None
        if first_rev and created and first_rev >= created:
            latency = (first_rev - created).total_seconds() / 3600.0
        # APPROXIMATION: comments_total is the issue+review thread total, not
        # inline-only. A PR that received a review counts as engaged (>=1) even
        # with 0 issue comments, so reviewed-but-open PRs are not ghosts.
        mrc = int(e.get("comments_total") or 0)
        if first_rev is not None:
            mrc = max(mrc, 1)
        events.append(RawPREvent(
            repo_owner=owner, repo_name=name, pr_number=int(e["number"]),
            created_at=created or now, author_login=str(e.get("author_login") or ""),
            author_association=ASSOC_MAP.get(raw_assoc, AuthorAssociation.NONE),
            closed_at=closed, merged_at=merged,
            is_external=raw_assoc in EXTERNAL_ASSOC,
            first_human_review_at=first_rev, response_latency_hours=latency,
            maintainer_review_comments=mrc,
        ))
    b.prov["pr_events"] = {"source": [src("pr_events", key), src("pr_reviews", key)],
                           "fetched_at": [prs_at, rev_at]}
    b.conf["pr_events"] = "derived" if prs_raw is not None else "missing"

    # --- mean_inline_comments: mean comments_total over EXTERNAL terminal PRs
    # that received a review. APPROXIMATION: total thread comments stand in
    # for inline review comments (no per-comment API in the raw cache). ---
    vals = [ev.maintainer_review_comments for ev in events
            if ev.is_external and ev.first_human_review_at and (ev.closed_at or ev.merged_at)]
    if vals:
        b.set("mean_inline_comments", round(sum(vals) / len(vals), 4),
              [src("pr_events", key), src("pr_reviews", key)], rev_at, "derived")
    else:
        b.missing("mean_inline_comments", 0.0)

    # --- rr_90: fraction of external authors with >= 2 PRs (i.e. a prior PR)
    # where any consecutive PR gap is <= 90 days. APPROXIMATION: author-level,
    # not per-PR; prior_merged_commits is unavailable in the raw cache. ---
    by_author: dict[str, list[datetime]] = {}
    for ev in events:
        if ev.is_external:
            by_author.setdefault(ev.author_login, []).append(ev.created_at)
    returned = total = 0
    for dts in by_author.values():
        if len(dts) < 2:
            continue
        total += 1
        dts.sort()
        if any((b_ - a).total_seconds() <= RR_WINDOW_DAYS * 86400
               for a, b_ in pairwise(dts)):
            returned += 1
    if total:
        b.set("rr_90", round(returned / total, 4), src("pr_events", key), prs_at, "derived")
    else:
        b.missing("rr_90", 0.0)

    # --- dx_ci_minutes: median CI wait per pull-request commit, in minutes (0 = unknown sentinel) ---
    ci_waits = _dx_commit_waits(runs)
    if ci_waits:
        b.set("dx_ci_minutes", round(statistics.median(ci_waits) / 60.0, 4), src("actions_runs", key), runs_at, "verified")
    else:
        b.missing("dx_ci_minutes", 0.0)

    # --- active_unclaimed_gfis (spec 5.1) ---
    cutoff = now - timedelta(days=GFI_WINDOW_DAYS)
    if gfis is not None:
        n = sum(1 for g in gfis
                if g.get("assignee") is None
                and (_parse_dt(g.get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff
                and {"good-first-issue", "help-wanted"} & _label_names(g.get("labels")))
        b.set("active_unclaimed_gfis", n, src("gfi", key), gfi_at, "verified")
    else:
        b.missing("active_unclaimed_gfis", 0)

    # --- scorecard_score: scorecard primary, deps.dev cross-check (flag) ---
    sc_val, dd_val = (sc or {}).get("score"), (deps or {}).get("scorecard_overall")
    if sc_val is not None:
        conf = "flag" if dd_val is not None and abs(float(sc_val) - float(dd_val)) > SCORECARD_MISMATCH else "verified"
        b.set("scorecard_score", float(sc_val), src("scorecard", key), sc_at, conf)
    elif dd_val is not None:
        b.set("scorecard_score", float(dd_val), src("depsdev", key), deps_at, "derived")
    else:
        b.missing("scorecard_score", 0.0)

    # --- body-driven governance heuristics (cached under raw/bodies/) ---
    contrib_body, contrib_body_at = _body(data_dir, owner, name, "CONTRIBUTING.md", fetch)
    coc_body, coc_body_at = _body(data_dir, owner, name, "CODE_OF_CONDUCT.md", fetch)
    readme_body, readme_body_at = _body(data_dir, owner, name, "README.md", fetch)
    agents_body, _ = _body(data_dir, owner, name, "AGENTS.md", fetch)
    claude_body, _ = _body(data_dir, owner, name, "CLAUDE.md", fetch)
    corpus = "\n".join(filter(None, [readme_body, contrib_body, agents_body, claude_body]))

    # The source must be the local bodies-cache envelope this function just
    # read (a real path under raw/, hashable), never the raw GitHub URL it was
    # originally fetched from -- a URL is not a path under raw/ and
    # porchlamp.derive.bundle._resolve_envelope can never resolve it, so
    # every legal_ip_model/ai_posture/promotion_ladder value would be silently
    # unverifiable (no sha256) regardless of whether the body was cached.
    # Falls back to the tree envelope (still a real, hashable source) only
    # when the relevant body was never fetched at all.
    contrib_path = _body_cache_path(data_dir, owner, name, "CONTRIBUTING.md")
    coc_path = _body_cache_path(data_dir, owner, name, "CODE_OF_CONDUCT.md")
    readme_path = _body_cache_path(data_dir, owner, name, "README.md")

    legal_source = _rel(contrib_path) if contrib_path.is_file() else src("tree", key)
    b.set("legal_ip_model", _legal_ip(contrib_body, str(gh.get("license_spdx") or ""), owner),
          legal_source, contrib_body_at or tree_at, "derived")
    ai_source = _rel(readme_path) if readme_path.is_file() else src("tree", key)
    b.set("ai_posture", _ai_posture(corpus), ai_source, readme_body_at or tree_at, "derived")
    if contrib_path.is_file():
        ladder_source, ladder_at = _rel(contrib_path), contrib_body_at
    elif coc_path.is_file():
        ladder_source, ladder_at = _rel(coc_path), coc_body_at
    else:
        ladder_source, ladder_at = src("tree", key), tree_at
    b.set("promotion_ladder", _promotion_ladder(f"{contrib_body or ''}\n{coc_body or ''}"),
          ladder_source, ladder_at, "derived")
    b.set("off_platform_reviews", False, src("tree", key), tree_at, "derived")  # no LKML evidence source
    b.set("hard_ai_ban", b.meta["ai_posture"] is AIPosture.PROSCRIBED,
          "derived from ai_posture", None, "derived")

    # --- OpenSSFSignals: only when ALL ten fields have sources; the PIKE
    # scorer treats missing fields as 0.0 in the numerator while keeping the
    # full 10.5 denominator, so partial signals would bias criticality toward
    # 0 instead of neutral. org_count / releases / issue counts / comment
    # frequency have no collector yet -> None (engine neutral 50). ---
    b.missing("openssf_signals", None)

    # --- Eligibility markers (D-GATE-1/2/3/6) ---
    from porchlamp.collect import repo_object as repo_object_mod
    ro, ro_at = _load(data_dir, "repo_object", key)
    _ats = {"repo_object": ro_at, "github_meta": gh_at, "tree": tree_at}
    for field, (value, conf, col) in repo_object_mod.derive_eligibility(ro, gh, tree).items():
        b.set(field, value, src(col, key) if col else None, _ats.get(col), conf)

    # --- 5.1 newcomer-issue supply from per-label GFI envelopes:
    # supersedes the legacy 30d/AND-label block above for per-label GFI
    # envelopes; legacy list data keeps the value derived above. Must run
    # before the meta snapshot below. ---
    from porchlamp.collect import labels as porchlamp_labels

    gfi_51 = porchlamp_labels.count_newcomer_issues(gfis, gfi_at, now)
    if gfi_51 is not None:
        b.set("active_unclaimed_gfis", gfi_51, src("gfi", key), gfi_at, "verified")

    # --- Email-domain EF (spec 3.2), bus factor, vitality (D-GATE-4) ---
    # `c365` is the ROW LIST from here on (the Pike and governance blocks
    # below read it too), whichever shape the envelope recorded:
    # commit_domains.envelope_rows unwraps the collector's window dict.
    c365_data, c365_at = _load(data_dir, "commits_365d", key)
    c365 = commit_domains.envelope_rows(c365_data)
    if c365 is None:
        c365_at = None
    if c365 is not None:
        recs = commit_domains.records_from_rows(c365)
        # Both factors are None when under half the window's commits could be
        # attributed to an org/author at all: the 50% threshold is then
        # unreachable, so the measure is missing rather than large (D-MISS-1).
        ef = commit_domains.elephant_factor(recs, now)
        if ef is not None:
            b.set("elephant_factor", ef, src("commits_365d", key), c365_at, "derived")
            # The sample EF was computed from (D-PARAM 3.2 min n = 30
            # commits): the bundle loader passes it as Measured.n, so a
            # thin window scores insufficient_n with its true n.
            b.set("elephant_factor_commit_count", elephant_factor_sample_n(recs, now),
                  src("commits_365d", key), c365_at, "derived")
        else:
            b.missing("elephant_factor", 1)
        bf = commit_domains.bus_factor(recs, now)
        if bf is not None:
            b.set("bus_factor", bf, src("commits_365d", key), c365_at, "derived")
        else:
            b.missing("bus_factor", 1)
        b.set("vitality", commit_domains.is_vital(recs, now),
              src("commits_365d", key), c365_at, "derived")
    else:
        b.missing("bus_factor", 1)
        b.missing("vitality", False)

    # --- Package-level dependents from the dependents envelope (deps.dev primary,
    # ecosyste.ms fallback) (D-PARAM 2.1). The v1 usedby_count above is always
    # None; this block overrides it only when a measured envelope exists.
    # "derived": the value passes the documented D-PARAM 2.1 dedup, not a raw source figure. ---
    if dep is not None and dep.get("deduped_count") is not None:
        b.set("dependents_count", int(dep["deduped_count"]),
              src("dependents", key), dep_at, "derived")

    # --- Windowed PR responsiveness fields (D-IN-2, 02 §4 1.1/4.3) ---
    from porchlamp.collect import github_graphql as prw_mod
    prw, prw_at = _load(data_dir, "pr_window", key)
    for field, (value, conf) in prw_mod.derive_pr_window_fields(prw).items():
        b.set(field, value, src("pr_window", key) if prw is not None else None, prw_at, conf)

    # --- 1.4 GR_14 = D-GATE-5's zero-response share (owner ruling
    # 2026-10-06: one measurement, per the spec): porchlamp.core.gates.
    # zero_response_counts over the pr_window envelope at this derivation's
    # own reference time `now`. D-IN-2 responses and D-IN-1 bot authors are
    # recomputed from the recorded PR nodes by the function that wrote the
    # envelope's stored classifications. A merge within the deadline also
    # answers (mergedAt), and the 14-d age is judged at min(now, the time
    # GitHub was read): the earlier of the envelope's fetched_at and its
    # window_anchor (rulings 2026-10-06). ---
    from porchlamp.core import constants as core_constants, gates as core_gates
    prw_prs = prw.get("prs") if isinstance(prw, dict) else None
    if isinstance(prw_prs, list):
        records = prw_mod.ghost_rate_prs_from_window(prw_prs)
        read_times = [t for t in (_parse_dt(prw_at), _parse_dt(prw.get("window_anchor"))) if t is not None]
        unanswered, gr_n = core_gates.zero_response_counts(
            records, now, observed_at=min(read_times) if read_times else None)
        gr_src = src("pr_window", key)
        b.set("gr14_population_n", gr_n, gr_src, prw_at, "derived")
        b.set("gr14_ghost_rate", unanswered / gr_n if gr_n else None, gr_src, prw_at, "derived")
        b.set("gr14_window_start", (now - timedelta(days=core_constants.PR_WINDOW_DAYS)).isoformat(),
              gr_src, prw_at, "derived")
        b.set("gr14_window_end", now.isoformat(), gr_src, prw_at, "derived")
    else:
        b.missing("gr14_population_n", 0)
        b.missing("gr14_ghost_rate", None)

    # --- Downloads measurement status per ecosystem (D-MISS-1) ---
    try:
        import backfill_downloads as _bdl
    except ImportError:
        from scripts import backfill_downloads as _bdl
    _dl_status = _bdl.downloads_status(ecosystem.value,
                                       b.conf.get("monthly_downloads") == "verified")
    if dl_withheld:
        _dl_status = "unattributed"
    b.set("downloads_status", _dl_status,
          src("downloads", f"{ecosystem.value}__{name}") if _dl_status == "measured" else None,
          dl_at, "derived")

    # --- Locally-derived Pike signals (02 §4 2.2 / 4.1, D-MISS-4):
    # all ten or nothing, and the pipeline defenses (scorecard five-check
    # mean or the review/CI proxy; the method is stored). ---
    from porchlamp.collect import pike_signals as pike_mod
    rel, rel_at = _load(data_dir, "releases", key)
    iss, iss_at = _load(data_dir, "issues_counts", key)
    rns, rns_at = _load(data_dir, "runs", key)
    try:
        w17_fields = pike_mod.derive_w17_fields(
            data_dir, key, now, gh, gh_at, c365, c365_at, dep, dep_at,
            rel, rel_at, iss, iss_at, rns, rns_at, sc, sc_at, prw, prw_at,
        )
    except Exception as e:  # review-ok: gate-crash — one repo's
        # Pike-signal derivation must never abort the whole bundle (or, in a
        # sweep, the rest of the run) -- every field this hook could have
        # emitted becomes a recorded collector_failure naming the exception
        # type, and derive_repo carries on with everything computed above
        # and below (including the governance block right after)
        # unaffected.
        detail = f"pike_signals.derive_w17_fields raised {type(e).__name__}: {e}"
        b.set("openssf_signals", None, None, None, "collector_failure")
        b.set("openssf_signals_detail", detail, None, None, "derived")
        b.set("pipeline_defenses", None, None, None, "collector_failure")
        b.set("pipeline_defenses_detail", detail, None, None, "derived")
    else:
        for field, (value, source, at, conf) in w17_fields.items():
            b.set(field, value, source, at, conf)
    # --- Governance evidence (02 §4 3.1/3.3/4.2) — evidence-based
    # legal IP / promotion ladder / AI posture with an evidence field per
    # value; NONE/AD_HOC/AD_HOC measured-zero defaults when the relevant
    # envelope(s) fetched successfully and found nothing, "missing"
    # confidence (Unmeasured(collector_failure) at the v2 bundle stage)
    # when the relevant envelope was never fetched or failed. The governance
    # envelope carries the check names and policy-file bodies; the
    # Signed-off-by share comes from the c365 (commits_365d) rows already
    # loaded above, not from a second fetch (porchlamp.collect.governance module
    # docstring). prw / c365 reuse the PR window and commits_365d envelopes.
    # Source/fetched_at follow the governance envelope when it exists, else
    # commits_365d, so a value derived from c365 alone (no governance
    # envelope) still carries real provenance instead of None; a
    # "missing"-confidence field carries no source (matches every other
    # b.missing() field in this module). (Foundation-owner fact mirrors the
    # expression above; the list stays in FOUNDATION_ORGS, one definition per
    # fact.) Field names match porchlamp.core.constants.PARAM_INPUT_IDS exactly:
    # promotion_ladder / ai_posture supersede the regex heuristic set above
    # with the evidence-based ones (02 §4 3.3/4.2). ---
    from porchlamp.collect import governance as gov_mod
    gov, gov_at = _load(data_dir, "governance", key)
    if gov is not None:
        gov_src, gov_prov_at = src("governance", key), gov_at
    elif c365 is not None:
        gov_src, gov_prov_at = src("commits_365d", key), c365_at
    else:
        gov_src, gov_prov_at = None, None
    for field, (value, conf) in gov_mod.derive_governance_fields(
            gov, prw, c365, owner.lower() in FOUNDATION_ORGS
            or owner.lower().endswith("-foundation")).items():
        if conf == "missing":
            b.set(field, value, None, None, conf)
        else:
            b.set(field, value, gov_src, gov_prov_at, conf)

    ai_posture_val = b.meta.get("ai_posture")
    is_proscribed = (
        ai_posture_val is AIPosture.PROSCRIBED
        or ai_posture_val == "PROSCRIBED"
        or getattr(ai_posture_val, "name", None) == "PROSCRIBED"
    )
    b.set("hard_ai_ban", is_proscribed, "derived from ai_posture", None, "derived")

    # --- 5.4 DX (02 §4 5.4): 0.5 CI runtime + 0.5 setup signals ---
    dx_val, dx_n = _derive_dx_54(runs, tree, contrib_body, readme_body, gov)
    if runs is not None and dx_val is not None:
        b.set("dx", dx_val, src("actions_runs", key), runs_at, "derived")
        b.set("dx_run_count", dx_n, src("actions_runs", key), runs_at, "derived")
    elif runs is not None:
        b.set("dx_run_count", 0, src("actions_runs", key), runs_at, "derived")
        b.missing("dx", None)
    else:
        b.missing("dx_run_count", 0)
        b.missing("dx", None)

    # --- Author-history cohorts (D-IN-3/D-IN-4, 02 §4 1.2/1.3/5.2/5.3):
    # DR_90, MR_first, newcomer conversion, RR_90 (raw and D-IN-3
    # eligibility+cap-adjusted). Self-contained on the author_history
    # envelope alone (its 365-d window covers the decision cohort's
    # [270 d, 90 d] span, so the pr_window envelope above is not needed
    # here; see the module note in porchlamp.collect.github_graphql). ---
    from porchlamp.collect import github_graphql as cohort_mod
    ah, ah_at = _load(data_dir, "author_history", key)
    ah_src = src("author_history", key) if ah is not None else None
    for field, (value, conf) in cohort_mod.derive_cohort_fields(ah).items():
        b.set(field, value, ah_src, ah_at, conf)

    meta = {k: _jsonable(v) for k, v in b.meta.items()}
    if b.meta.get("openssf_signals") is not None:
        meta["openssf_signals"] = asdict(b.meta["openssf_signals"])
    out_events = [{k: _jsonable(v) for k, v in asdict(ev).items()} for ev in events]
    return {"repo": f"{owner}/{name}", "meta": meta, "pr_events": out_events,
            "provenance": b.prov, "confidence": b.conf}


def main(argv: Optional[list[str]] = None) -> int:
    """CLI: derive one repo's bundle from the cache, or batch derive with --all."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("owner", nargs="?", default=None)
    ap.add_argument("name", nargs="?", default=None)
    ap.add_argument("--all", action="store_true", help="derive all repos found in candidates.jsonl or raw/ cache")
    ap.add_argument("--workers", type=int, default=1, help="concurrency for batch derivation")
    ap.add_argument("--ecosystem", default="pypi", choices=[e.value for e in Ecosystem])
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--fetch", action="store_true", help="fetch uncached file bodies")
    ap.add_argument("--as-of", default=None,
                    help="reference time, ISO-8601 with an offset (the snapshot's as_of); required with --all")
    a = ap.parse_args(argv)
    now: Optional[datetime] = None
    if a.as_of is not None:
        try:
            now = datetime.fromisoformat(a.as_of.replace("Z", "+00:00"))
        except ValueError:
            ap.error(f"--as-of is not an ISO-8601 timestamp: {a.as_of!r}")
        if now.tzinfo is None:
            ap.error(f"--as-of needs a UTC offset: {a.as_of!r}")
        if a.fetch:
            ap.error("--fetch cannot be pinned to --as-of: fetch the bodies first, then derive offline")
    elif a.all:
        ap.error("--all needs --as-of: a batch derivation is pinned to one reference time")
    if not a.all:
        if not a.owner or not a.name:
            ap.error("the following arguments are required: owner, name (or pass --all)")
        bundle = derive_repo(a.owner, a.name, Ecosystem(a.ecosystem), a.data_dir, fetch=a.fetch, now=now)
        out = a.data_dir / "derived" / f"{a.owner}__{a.name}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
        print(out)
        return 0

    candidates_file = a.data_dir / "raw" / "candidates.jsonl"
    if not candidates_file.is_file():
        candidates_file = a.data_dir / "candidates.jsonl"
    repos: list[tuple[str, str, Ecosystem]] = []
    if candidates_file.is_file():
        for line in candidates_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("owner") and r.get("name"):
                eco_str = r.get("ecosystem", a.ecosystem)
                try:
                    eco = Ecosystem(eco_str)
                except ValueError:
                    eco = Ecosystem(a.ecosystem)
                repos.append((r["owner"], r["name"], eco))
    else:
        raw_dir = a.data_dir / "raw"
        if raw_dir.is_dir():
            seen: set[tuple[str, str]] = set()
            for p in sorted(raw_dir.glob("*/*__*.json")):
                parts = p.stem.split("__", 1)
                if len(parts) == 2:
                    seen.add((parts[0], parts[1]))
            for o, n in sorted(seen):
                repos.append((o, n, Ecosystem(a.ecosystem)))

    def _derive_one(item: tuple[str, str, Ecosystem]) -> Path:
        owner, name, eco = item
        b = derive_repo(owner, name, eco, a.data_dir, fetch=a.fetch, now=now)
        out_path = a.data_dir / "derived" / f"{owner}__{name}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(b, indent=2, sort_keys=True), encoding="utf-8")
        return out_path

    if a.workers > 1:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=a.workers) as executor:
            for out_path in executor.map(_derive_one, repos):
                print(out_path)
    else:
        for item in repos:
            print(_derive_one(item))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
