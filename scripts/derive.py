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
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.request
from dataclasses import asdict
from itertools import pairwise
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from cvi.collect import commits as commit_domains
from cvi.constants import BOT_LOGIN_MARKERS
from cvi.models import (
    AIPosture,
    AuthorAssociation,
    Ecosystem,
    LegalIPModel,
    PromotionLadder,
    RawPREvent,
)
from cvi.provenance import utc_now_iso

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
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


class Bundle:
    """Accumulates meta / provenance / confidence maps."""

    def __init__(self) -> None:
        self.meta: dict[str, Any] = {}
        self.prov: dict[str, Any] = {}
        self.conf: dict[str, str] = {}

    def set(self, field: str, value: Any, source: Optional[str],
            fetched_at: Optional[str], confidence: str) -> None:
        self.meta[field] = value
        self.prov[field] = {"source": source, "fetched_at": fetched_at}
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


def _body(data_dir: Path, owner: str, name: str, fname: str,
          fetch: bool) -> tuple[Optional[str], Optional[str]]:
    """File body from the bodies cache, optionally fetched + cached."""
    p = data_dir / "raw" / "bodies" / f"{owner}__{name}__{fname}.json"
    if p.is_file():
        env = json.loads(p.read_text(encoding="utf-8"))
        return (env.get("data") or {}).get("body"), env.get("fetched_at")
    if not fetch:
        return None, None
    url = f"https://raw.githubusercontent.com/{owner}/{name}/HEAD/{fname}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception:  # review-ok: gate-network — any fetch failure caches None body
        text = None
    at = utc_now_iso(datetime.now(timezone.utc))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fetched_at": at, "source": url,
                             "data": {"body": text}}), encoding="utf-8")
    return text, at


def elephant_factor(contributors: Optional[list[dict]]) -> int:
    """Spec 3.2 login-proxy fallback: minimum number of distinct logins whose
    contributions reach >= 50% of total contributions. W1-5: the primary EF
    now comes from commit-author email domains (append-only block at the end
    of derive_repo, via cvi.collect.commits); this approximation is used only
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
    b = Bundle()
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

    def src(c: str, k: str) -> str:
        return str(data_dir / "raw" / c / f"{k}.json")

    # --- stars / forks (github_meta primary; deps.dev is the verify step) ---
    for f, gk in (("stars", "stars"), ("forks", "forks")):
        if gh.get(gk) is not None:
            b.set(f, int(gh[gk]), src("github_meta", key), gh_at, "verified")
        else:
            b.missing(f, 0)

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

    # --- dx_ci_minutes: mean run_duration_seconds / 60 (0 = unknown sentinel) ---
    durs = [int(r.get("run_duration_seconds") or 0) for r in runs or []]
    if durs:
        b.set("dx_ci_minutes", round(sum(durs) / len(durs) / 60.0, 4), src("actions_runs", key), runs_at, "verified")
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
    paths = set((tree or {}).get("paths") or [])
    contrib_body, contrib_body_at = _body(data_dir, owner, name, "CONTRIBUTING.md", fetch)
    coc_body, _ = _body(data_dir, owner, name, "CODE_OF_CONDUCT.md", fetch)
    corpus = "\n".join(filter(None, [
        _body(data_dir, owner, name, "README.md", fetch)[0], contrib_body,
        _body(data_dir, owner, name, "AGENTS.md", fetch)[0],
        _body(data_dir, owner, name, "CLAUDE.md", fetch)[0],
    ]))
    body_src = f"https://raw.githubusercontent.com/{owner}/{name}/HEAD/CONTRIBUTING.md"
    b.set("legal_ip_model", _legal_ip(contrib_body, str(gh.get("license_spdx") or ""), owner),
          body_src if "CONTRIBUTING.md" in paths else src("tree", key),
          contrib_body_at or tree_at, "derived")
    b.set("ai_posture", _ai_posture(corpus),
          f"https://raw.githubusercontent.com/{owner}/{name}/HEAD/README.md", None, "derived")
    b.set("promotion_ladder", _promotion_ladder(f"{contrib_body or ''}\n{coc_body or ''}"),
          body_src, contrib_body_at or tree_at, "derived")
    b.set("off_platform_reviews", False, src("tree", key), tree_at, "derived")  # no LKML evidence source
    b.set("hard_ai_ban", b.meta["ai_posture"] is AIPosture.PROSCRIBED,
          "derived from ai_posture", None, "derived")

    # --- OpenSSFSignals: only when ALL ten fields have sources; the PIKE
    # scorer treats missing fields as 0.0 in the numerator while keeping the
    # full 10.5 denominator, so partial signals would bias criticality toward
    # 0 instead of neutral. org_count / releases / issue counts / comment
    # frequency have no collector yet -> None (engine neutral 50). ---
    b.missing("openssf_signals", None)

    # --- W1-9 (D-GATE-1/2/3/6): eligibility markers ---
    from cvi.collect import repo_object as repo_object_mod
    ro, ro_at = _load(data_dir, "repo_object", key)
    _ats = {"repo_object": ro_at, "github_meta": gh_at, "tree": tree_at}
    for field, (value, conf, col) in repo_object_mod.derive_eligibility(ro, gh, tree).items():
        b.set(field, value, src(col, key) if col else None, _ats.get(col), conf)

    # --- W1-1: 5.1 newcomer-issue supply from per-label GFI envelopes.
    # Supersedes the legacy 30d/AND-label block above for W1-1-format
    # envelopes; legacy list data keeps the value derived above. Must run
    # before the meta snapshot below. ---
    from cvi.collect import labels as cvi_labels

    gfi_51 = cvi_labels.count_newcomer_issues(gfis, gfi_at, now)
    if gfi_51 is not None:
        b.set("active_unclaimed_gfis", gfi_51, src("gfi", key), gfi_at, "verified")

    # --- W1-5: email-domain EF (spec 3.2), bus factor, vitality (D-GATE-4) ---
    c365, c365_at = _load(data_dir, "commits_365d", key)
    if c365 is not None:
        recs = commit_domains.records_from_rows(c365)
        b.set("elephant_factor", commit_domains.elephant_factor(recs, now),
              src("commits_365d", key), c365_at, "derived")
        b.set("bus_factor", commit_domains.bus_factor(recs, now),
              src("commits_365d", key), c365_at, "derived")
        b.set("vitality", commit_domains.is_vital(recs, now),
              src("commits_365d", key), c365_at, "derived")
    else:
        b.missing("bus_factor", 1)
        b.missing("vitality", False)

    # --- W1-2 (D-PARAM 2.1 / F3): package-level dependents from the
    # dependents envelope (deps.dev primary, ecosyste.ms fallback). The v1
    # usedby_count above is always None (F3); this block overrides it only
    # when a measured envelope exists. "derived": the value passes the
    # documented D-PARAM 2.1 dedup, not a raw source figure. ---
    dep, dep_at = _load(data_dir, "dependents", key)
    if dep is not None and dep.get("deduped_count") is not None:
        b.set("dependents_count", int(dep["deduped_count"]),
              src("dependents", key), dep_at, "derived")

    meta = {k: _jsonable(v) for k, v in b.meta.items()}
    if b.meta.get("openssf_signals") is not None:
        meta["openssf_signals"] = asdict(b.meta["openssf_signals"])
    out_events = [{k: _jsonable(v) for k, v in asdict(ev).items()} for ev in events]
    return {"repo": f"{owner}/{name}", "meta": meta, "pr_events": out_events,
            "provenance": b.prov, "confidence": b.conf}


def main(argv: Optional[list[str]] = None) -> int:
    """CLI: derive one repo's bundle from the cache."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("owner")
    ap.add_argument("name")
    ap.add_argument("--ecosystem", default="pypi", choices=[e.value for e in Ecosystem])
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--fetch", action="store_true", help="fetch uncached file bodies")
    a = ap.parse_args(argv)
    bundle = derive_repo(a.owner, a.name, Ecosystem(a.ecosystem), a.data_dir, fetch=a.fetch)
    out = a.data_dir / "derived" / f"{a.owner}__{a.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
