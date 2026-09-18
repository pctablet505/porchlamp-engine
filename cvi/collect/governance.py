"""Governance evidence — legal IP, promotion ladder, AI posture.

Collects, per repository, the raw evidence for the three governance
parameters of ``docs/v2/02-RANKING-DESIGN.md`` section 4 (3.1, 3.3, 4.2)
and derives them with ``NONE`` / ``AD_HOC`` / ``AD_HOC`` defaults:

* **3.1 Legal IP** — check-run names and commit-status contexts on the
  :data:`MERGED_PR_CAP` (20) most recent merged PRs (DCO, EasyCLA,
  cla-assistant, google-cla, ...). The DCO alternative evidence is the
  ``Signed-off-by`` share over the trailing 365 d commits: the
  ``commits_365d`` envelope (:mod:`cvi.collect.commits`) records a
  per-commit ``signed_off`` boolean, so this module computes the share
  from that envelope with **no second commit fetch** and no
  message text stored anywhere (``03`` section 3: documents/text are not
  evidence for 3.1; only checks and the sign-off fact are).
* **3.3 Promotion ladder** — association transitions read from the
  recorded ``pr_window`` envelope: an account whose earliest
  observed association in the window is external and whose latest is
  MEMBER/COLLABORATOR is a verified promotion. The CLOSED rung needs the
  merged PR authors attributable to one email-domain org via the
  ``commits_365d`` envelope. DOCUMENTED reads the collected
  GOVERNANCE.md / MAINTAINERS.md / CONTRIBUTING.md bodies.
* **4.2 AI policy posture** — presence of an AI-policy file (the
  :data:`AI_POLICY_FILES` family) only. **PROSCRIBED is never detected**:
  bare co-occurrence of a negation/ban token and the word "AI" cannot
  distinguish "Do not submit AI-generated code" from "Do not worry if you
  used AI", nor "AI is prohibited" from "undisclosed AI use is
  prohibited"; both need the ban's *object*, which this module never
  binds. ``classify_ai_posture`` returns REGULATED on AI-policy-file
  presence, else AD_HOC, and never PROSCRIBED, so **the
  ``ai_assisted`` persona's hard-ban gate cannot fire** -- no repository
  can score the PROSCRIBED regime (25 for that persona) through this
  collector. :data:`cvi.core.constants.AI_POSTURE_VALUES` keeps all
  three spellings (REGULATED/PROSCRIBED/AD_HOC): only the producer here is
  narrowed, not the vocabulary. The commit-trailer convention path of
  ``02`` section 4 4.2 ("Assisted-by"/"AI-generated") needs commit message
  text, which this collector never reads, so that path is not evaluated
  here either.

Per ``03`` section 3, documents are not 3.1 evidence: the regime comes from
check/status names and the sign-off share only. Classification constants
are defined here, as the single definition; ``cvi.core.constants`` owns
the value vocabularies (:data:`cvi.core.constants.LEGAL_IP_VALUES`,
``PROMOTION_LADDER_VALUES``, ``AI_POSTURE_VALUES``).

Priority, highest first (``02`` §4 + ``03`` §3):

* 3.1: foundation-CLA check (or foundation owner) > corp-CLA check >
  DCO check > sign-off share ≥ :data:`SIGNOFF_THRESHOLD` > NONE. A CLA bot
  check is direct evidence of a CLA regime and outranks DCO evidence;
  the DCO/CLA check names never overlap in practice.
* 3.3: VERIFIED (observed transition) > CLOSED (single-org merge stream,
  no external merges) > DOCUMENTED (nomination text) > AD_HOC. CLOSED
  outranks DOCUMENTED so a closed repo cannot buy 75 by writing docs
  (``03`` §3: the cheapest fake for 3.3).
* 4.2: REGULATED (policy file present) > AD_HOC. PROSCRIBED is never
  emitted by this module (see above).
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol

from cvi.collect import commits as commit_domains
from cvi.collect import github_graphql as pr_window
from cvi.collect.envelope import sha256_hex, write_envelope
# --------------------------------------------------------------------------- #
# 02 §4 constants (defined here, once)                                        #
# --------------------------------------------------------------------------- #

#: Check evidence comes from the 20 most recent merged PRs.
MERGED_PR_CAP: int = 20

#: D-COL-12: at most this many DISTINCT head SHAs are probed for check/status
#: evidence, newest merge first. Each probed SHA costs two REST calls
#: (``/statuses`` + ``/check-runs``), so the uncapped probe over
#: ``MERGED_PR_CAP`` PRs was up to 40 calls per repository -- about 45 with the
#: pulls pages, ~259k for the 5,753-repo universe, ~52 h of a 5,000/h core
#: budget, which is more than `08` first budgeted for the ENTIRE sweep.
#: A CLA is a property of the project, not of one pull request: where it is
#: required its check appears on essentially every external PR, so twenty
#: probes buy near-duplicate evidence. Five keeps the signal and costs 10 calls.
CHECK_EVIDENCE_SHA_CAP: int = 5

#: D-COL-12: the fewest distinct probed SHAs that can support the NEGATIVE
#: finding "this project has no CLA". A positive signal is a measurement from
#: one probe -- seeing an EasyCLA context proves a CLA exists. Its absence
#: proves nothing until enough commits were examined, and capping the probe
#: makes a thin sample more likely (all five newest merges can be maintainers'
#: own PRs, which carry no CLA check). Below this, 3.1 is an evidence gap
#: rather than a measured ``NONE``.
CHECK_EVIDENCE_MIN_SHAS: int = 3
#: Closed PRs scanned (newest first) to reach the 20 merged: 5 pages of 100.
PULLS_PAGES_MAX: int = 5
#: REST pulls page size (GitHub max).
PULLS_PAGE_SIZE: int = 100
#: 02 §4 3.1: DCO when at least 50% of window commits carry Signed-off-by.
#: The window and cap themselves are :mod:`cvi.collect.commits`'s
#: ``COMMIT_WINDOW_DAYS``/``COMMIT_CAP`` (one definition per fact) --
#: this module reads the already-collected ``commits_365d`` envelope rather
#: than refetching, so it does not redefine them.
SIGNOFF_THRESHOLD: float = 0.5
#: 02 §4 3.3: the files whose bodies name a maintainer nomination process.
GOVERNANCE_FILES: tuple[str, ...] = (
    "GOVERNANCE.md", "MAINTAINERS.md", "CONTRIBUTING.md", "SECURITY.md")
#: 02 §4 4.2: the AI-policy file family (a presence = a policy file).
AI_POLICY_FILES: tuple[str, ...] = (
    "AI.md", "AI_POLICY.md", "AIPOLICY.md", "AI-POLICY.md",
    ".github/AI.md", ".github/copilot-instructions.md", "AGENTS.md",
    "CLAUDE.md")

#: A genuine CLA check/status name -- the shared "is this even a CLA check"
#: test both branches below require (a bare
#: foundation-name substring with no CLA marker at all -- "license/apache-2.0",
#: "apache-rat", "build (eclipse-temurin-17)", "asf-jenkins-build",
#: "cncf-conformance-e2e", "test-on-apache-spark" -- must never match
#: anywhere in this module). Matches an explicit CLA-bot name, a "license/cla"
#: or "cla/..." status-context spelling, or a bare "cla" token bounded by
#: non-alphanumeric characters (so "easycla"/"cncf-cla" still match via their
#: own named alternative, but "clarity-check" or "declarative" do not).
CLA_CHECK_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in (
        r"easycla",
        r"cla[-_ ]?assistant",
        r"cla[-_ ]?check",
        r"license/cla",
        r"cla/",
        r"(?<![a-z0-9])cla(?![a-z0-9])",
    ))
#: Check names / status contexts that evidence a foundation-branded CLA
#: (02 §4 3.1: "EasyCLA/ASF/Eclipse/CNCF CLA check"). Every alternative here
#: also satisfies :data:`CLA_CHECK_PATTERNS` (EasyCLA is CNCF's bot; ASF/
#: Eclipse/CNCF run a named "<foundation>-cla" bot or status context), so a
#: bare foundation-name substring with no CLA marker never matches here:
#: matching "apache"/"asf"/"cncf"/"eclipse" on their own would shadow a
#: real DCO check on common non-CLA build-job names.
FOUNDATION_CHECK_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in (
        r"easycla",
        r"\b(asf|apache)[\s_/-]*cla\b",
        r"\bcncf[\s_/-]*cla\b",
        r"\beclipse[\s_/-]*cla\b",
    ))
#: Check names / status contexts that evidence a corporate CLA bot
#: (02 §4 3.1: "cla-assistant/google-cla/other CLA bot present"). A corp CLA
#: is, definitionally, any genuine CLA check that is not foundation-branded
#: (checked in that order in :func:`classify_legal_ip`); "google-cla" is
#: already a bare "cla" token bounded by "-", so it is covered by
#: :data:`CLA_CHECK_PATTERNS` with no separate pattern needed.
CORP_CLA_CHECK_PATTERNS: tuple[re.Pattern, ...] = CLA_CHECK_PATTERNS
#: A DCO check run (02 §4 3.1: "DCO check run on recent PRs").
DCO_CHECK_PATTERN: re.Pattern = re.compile(r"dco", re.IGNORECASE)

#: 02 §4 3.3: association transitions observable in the PR window.
PROMOTION_TARGET_ASSOCIATIONS: frozenset[str] = frozenset({"MEMBER", "COLLABORATOR"})
#: 02 §4 3.3 DOCUMENTED: a maintainer nomination process named in a
#: governance file (case-insensitive, one line).
NOMINATION_PATTERN: re.Pattern = re.compile(
    r"nominat\w*|"
    r"become a (maintainer|core (member|maintainer)|committer|tsc member)|"
    r"promotion (path|process|ladder)|"
    r"path to (maintainer(ship)?|membership|core)|"
    r"how to (become|apply)",
    re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Transports                                                                    #
# --------------------------------------------------------------------------- #

class RestTransport(Protocol):
    """One REST execution; the live implementation shells to ``gh api``."""

    def call(self, path: str, params: Mapping[str, str]) -> Any:
        """Run one ``gh api`` call and return the parsed JSON body."""


class GhApiError(RuntimeError):
    """A failed ``gh api`` call, with the HTTP status when the answer was
    one (``gh`` prints ``(... HTTP <code>)`` for HTTP error answers)."""

    def __init__(self, message: str, http_status: Optional[int] = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class GhApiTransport:
    """Live REST transport via ``gh api`` (the gh auth token is used).

    Per ``gh api`` semantics, every value after the path is a parameter
    flag (``-f key=value``); a non-2xx answer exits non-zero and raises
    :class:`GhApiError` (``http_status`` set when the answer was an HTTP
    error, e.g. the 404 of a repo with issues/PRs disabled).
    """

    def __init__(self, timeout_seconds: int = 90) -> None:
        self.timeout_seconds = timeout_seconds

    def call(self, path: str, params: Mapping[str, str]) -> Any:
        """Run one ``gh api`` call and return the parsed JSON body.

        ``-X GET`` is explicit: ``gh api`` with ``-f`` fields would
        otherwise infer a write method for read endpoints (404).
        """
        args = ["gh", "api", "-X", "GET", path]
        for key, value in params.items():
            args += ["-f", f"{key}={value}"]
        out = subprocess.run(args, capture_output=True, text=True,
                             timeout=self.timeout_seconds)
        if out.returncode != 0:
            match = re.search(r"\(HTTP (\d+)\)", out.stderr)
            raise GhApiError(
                f"gh api {path} rc={out.returncode}: {out.stderr[:160]}",
                int(match.group(1)) if match else None)
        return json.loads(out.stdout) if out.stdout.strip() else None


#: ``fetch_file(path) -> body``; the live implementation reads
#: ``raw.githubusercontent.com`` at the repo HEAD.
FileFetcher = Callable[[str], Optional[str]]


def fetch_file_text(owner: str, name: str, timeout_seconds: int = 30) -> FileFetcher:
    """Live :data:`FileFetcher` for one repository's HEAD ref.

    Returns the UTF-8 body of the file at ``path``; ``None`` when the file
    does not exist (HTTP 404). Any other failure raises, so a transient
    error never gets cached as "absent" (the envelope checkpoint retries).
    """

    def fetch(path: str) -> Optional[str]:
        url = f"https://raw.githubusercontent.com/{owner}/{name}/HEAD/{path}"
        req = urllib.request.Request(url, headers={"User-Agent": "cvi-research/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
    return fetch


# --------------------------------------------------------------------------- #
# Recorded-response collection                                                  #
# --------------------------------------------------------------------------- #

def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; naive values are taken as UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _is_bot_row(row: Mapping[str, Any]) -> bool:
    """D-IN-1 over one recorded commit row (delegates to :mod:`cvi.collect.commits`)."""
    return commit_domains.is_bot(commit_domains.CommitActor(
        login=str(row.get("author_login") or ""),
        email=str(row.get("author_email") or ""),
        actor_type=str(row.get("author_type") or "")))


def _merged_prs(api: RestTransport, owner: str, name: str
                ) -> tuple[list[dict[str, Any]], bool]:
    """The up-to-:data:`MERGED_PR_CAP` most recent merged PRs, newest first.

    Pages ``repos/{owner}/{repo}/pulls?state=closed`` newest-created first
    (up to :data:`PULLS_PAGES_MAX` pages) and keeps the merged entries.
    Returns ``(merged, pulls_available)``: a 404 on the pulls endpoint is
    the recorded fact that the repository has issues/PRs disabled (no PR
    evidence, not a fetch failure); any other transport error propagates.
    """
    merged: list[dict[str, Any]] = []
    for page in range(1, PULLS_PAGES_MAX + 1):
        try:
            doc = api.call(f"repos/{owner}/{name}/pulls", {
            "state": "closed", "sort": "created", "direction": "desc",
                "per_page": str(PULLS_PAGE_SIZE), "page": str(page)}) or []
        except GhApiError as e:
            if e.http_status == 404:
                return [], False
            raise
        for pr in doc:
            if not pr.get("merged_at"):
                continue
            merged.append({
                "number": pr.get("number"),
                "created_at": pr.get("created_at"),
                "merged_at": pr.get("merged_at"),
                "author_login": (pr.get("user") or {}).get("login") or "",
                "head_sha": (pr.get("head") or {}).get("sha") or "",
            })
            if len(merged) >= MERGED_PR_CAP:
                return merged, True
        if len(doc) < PULLS_PAGE_SIZE:
            break
    return merged, True


def _distinct_head_shas(merged_prs: Sequence[Mapping[str, Any]]
                        ) -> list[tuple[str, Mapping[str, Any]]]:
    """``(sha, pr)`` for each distinct non-empty head SHA, newest merge first.

    Ordered by ``merged_at`` descending so "newest" is a fact rather than an
    accident of the pulls listing, which is sorted by CREATION date -- a pull
    request opened earlier can be merged later, so the two orders differ.
    Entries with no ``merged_at`` sort last rather than being dropped.
    """
    ordered = sorted(
        merged_prs,
        key=lambda pr: (str(pr.get("merged_at") or ""), str(pr.get("number") or "")),
        reverse=True,
    )
    out: list[tuple[str, Mapping[str, Any]]] = []
    seen: set[str] = set()
    for pr in ordered:
        sha = str(pr.get("head_sha") or "")
        if not sha or sha in seen:
            continue
        seen.add(sha)
        out.append((sha, pr))
    return out


def _check_evidence(api: RestTransport, owner: str, name: str,
                    merged_prs: Sequence[Mapping[str, Any]]
                    ) -> tuple[list[dict[str, Any]], int, int]:
    """Status contexts and check-run names on merged PRs' head commits.

    Returns ``(checks, shas_probed, shas_available)``. Two REST calls per
    probed SHA -- ``/commits/{sha}/statuses`` and ``/commits/{sha}/check-runs``
    (GitHub records EasyCLA as a status context and cla-assistant/google-cla as
    check runs; both are 3.1 evidence) -- over at most
    :data:`CHECK_EVIDENCE_SHA_CAP` distinct SHAs, newest merge first (D-COL-12).
    ``shas_available`` is how many distinct SHAs there were to choose from, so
    a reader can tell "looked at 5 of 5" from "looked at 5 of 20"; a SHA whose
    commit 404s is counted as probed, because the call was spent.
    """
    checks: list[dict[str, Any]] = []
    distinct = _distinct_head_shas(merged_prs)
    shas_available = len(distinct)
    shas_probed = 0
    for sha, pr in distinct[:CHECK_EVIDENCE_SHA_CAP]:
        shas_probed += 1
        try:
            statuses = api.call(f"repos/{owner}/{name}/commits/{sha}/statuses",
                                {"per_page": "100"}) or []
            runs = api.call(f"repos/{owner}/{name}/commits/{sha}/check-runs",
                            {"per_page": "100"}) or {}
        except GhApiError as e:
            if e.http_status == 404:
                continue
            raise
        for status in statuses:
            checks.append({
                "name": status.get("context") or "",
                "kind": "status",
                "state": status.get("state"),
                "conclusion": None,
                "pr": pr.get("number"),
                "sha": sha,
            })
        for run in runs.get("check_runs") or []:
            checks.append({
                "name": run.get("name") or "",
                "kind": "check_run",
                "state": run.get("status"),
                "conclusion": run.get("conclusion"),
                "pr": pr.get("number"),
                "sha": sha,
            })
    return checks, shas_probed, shas_available


def _row_sequence(rows: Optional[Any]) -> Optional[Sequence[Mapping[str, Any]]]:
    """Defend the ``c365`` (commits_365d) contract: a sequence of row
    mappings, never a bare mapping itself (a dict-shaped ``c365`` iterates
    over its *keys*, not its rows, and the
    first ``row.get(...)`` then raises ``AttributeError`` on a plain string).
    Returns ``None`` for ``None`` or a malformed (bare-``Mapping``) input, so
    every caller below treats it exactly like "no rows" instead of crashing
    ``derive_repo``.
    """
    if rows is None or isinstance(rows, Mapping):
        return None
    return rows


def signoff_share(c365_rows: Optional[Sequence[Mapping[str, Any]]]) -> Optional[dict[str, Any]]:
    """02 §4 3.1: the ``Signed-off-by`` share over the non-bot commits (D-IN-1).

    ``c365_rows`` are the recorded ``commits_365d`` envelope rows (read by
    the ``scripts/derive.py`` hook and passed straight through -- no
    separate fetch); each row's ``signed_off`` boolean comes from
    :func:`cvi.collect.commits.shape_commit_rows`.
    Returns ``None`` when no ``commits_365d`` envelope was collected at all
    (as opposed to an envelope with zero rows, which returns a share of
    ``None`` inside a real ``{"count": 0, "total": 0, "share": None}``), and
    also when ``c365_rows`` is a malformed (bare-mapping) shape.
    """
    rows_seq = _row_sequence(c365_rows)
    if rows_seq is None:
        return None
    rows = list(rows_seq)
    non_bot = [row for row in rows if not _is_bot_row(row)]
    total = len(non_bot)
    count = sum(1 for row in non_bot if row.get("signed_off"))
    share = round(count / total, 4) if total else None
    return {"count": count, "total": total, "share": share}


def collect_governance(raw_dir: Path, owner: str, name: str,
                       now: Optional[datetime] = None,
                       api: Optional[RestTransport] = None,
                       fetch_file: Optional[FileFetcher] = None,
                       force: bool = False) -> str:
    """Collect the governance evidence envelope for one repository.

    Writes ``raw_dir/governance/<owner>__<name>.json`` — one envelope whose
    ``data`` carries the up-to-:data:`MERGED_PR_CAP` most recent merged
    PRs, the check/status evidence on their head commits, and the
    governance/AI-policy file bodies — and returns ``"ok"``. The
    ``Signed-off-by`` share is *not* collected here: it is computed at
    derive time from the already-collected ``commits_365d`` envelope (see
    :func:`signoff_share`), so this collector never fetches commits and
    never stores message text. Returns ``"skip"`` when the envelope
    already exists and ``force`` is False. ``api`` / ``fetch_file`` are
    injectable transports for the recorded-response oracles; live by
    default.
    """
    key = f"{owner}__{name}"
    target = raw_dir / "governance" / f"{key}.json"
    if target.is_file() and not force:
        return "skip"
    now = now or datetime.now(timezone.utc)
    api = api or GhApiTransport()
    fetch_file = fetch_file or fetch_file_text(owner, name)

    merged_prs, pulls_available = _merged_prs(api, owner, name)
    checks, shas_probed, shas_available = _check_evidence(api, owner, name, merged_prs)
    files: dict[str, Optional[dict[str, Any]]] = {}
    for fname in (*GOVERNANCE_FILES, *AI_POLICY_FILES):
        body = fetch_file(fname)
        files[fname] = None if body is None else {"present": True, "sha256": sha256_hex(body), "body": body}
    data: dict[str, Any] = {
        "pulls_available": pulls_available,
        "merged_pr_cap": MERGED_PR_CAP,
        "merged_prs": merged_prs,
        "checks": checks,
        # D-COL-12: what the check evidence actually rests on.
        "check_sha_cap": CHECK_EVIDENCE_SHA_CAP,
        "checks_shas_probed": shas_probed,
        "checks_shas_available": shas_available,
        "check_names": sorted({c["name"] for c in checks if c["name"]}),
        "files": files,
    }
    write_envelope(target, source="github-rest:governance", data=data,
                   fetched_at=now.isoformat())
    return "ok"


# --------------------------------------------------------------------------- #
# 02 §4 classification (called from the scripts/derive.py hook)               #
# --------------------------------------------------------------------------- #

def classify_legal_ip(check_names: Sequence[str], signoff: Optional[Mapping[str, Any]],
                      foundation_owner: bool = False) -> tuple[str, dict[str, Any]]:
    """02 §4 3.1: the evidence-based legal IP regime.

    ``check_names`` are the observed check-run names / status contexts,
    ``signoff`` the recorded ``{"count", "total", "share"}`` (None when no
    commit evidence), ``foundation_owner`` the owner-in-foundation-list
    fact. Returns ``(value, evidence)`` with ``value`` in
    :data:`cvi.core.constants.LEGAL_IP_VALUES`; the evidence dict is
    factual (``03`` §3: documents are not evidence) and carries the fired
    ``rule``.
    """
    names = sorted({str(n) for n in check_names if n})
    lowered = [n.lower() for n in names]
    evidence: dict[str, Any] = {
        "check_names": names,
        "signoff": dict(signoff) if signoff else None,
        "foundation_owner": bool(foundation_owner),
    }
    if any(p.search(n) for n in lowered for p in FOUNDATION_CHECK_PATTERNS):
        return "FOUNDATION_CLA", {**evidence, "rule": "foundation_cla_check"}
    if foundation_owner:
        return "FOUNDATION_CLA", {**evidence, "rule": "foundation_owner"}
    if any(p.search(n) for n in lowered for p in CORP_CLA_CHECK_PATTERNS):
        return "CORP_CLA", {**evidence, "rule": "corp_cla_check"}
    if any(DCO_CHECK_PATTERN.search(n) for n in lowered):
        return "DCO", {**evidence, "rule": "dco_check"}
    if signoff and int(signoff.get("total") or 0) > 0 and \
            (int(signoff.get("count") or 0) / int(signoff["total"])) >= SIGNOFF_THRESHOLD:
        return "DCO", {**evidence, "rule": "signoff_share"}
    return "NONE", {**evidence, "rule": "no_evidence"}


def _orgs_by_login(c365: Optional[Sequence[Mapping[str, Any]]]) -> dict[str, str]:
    """author_login -> email-domain org over the commits_365d envelope rows.

    ``c365`` must be a row sequence; a malformed bare-mapping shape is
    treated as no rows via :func:`_row_sequence`, never crashed on.
    """
    orgs: dict[str, str] = {}
    for row in (_row_sequence(c365) or []):
        login = str(row.get("author_login") or "").strip()
        if not login or login in orgs:
            continue
        org = commit_domains.org_key(commit_domains.CommitActor(
            login=login, email=str(row.get("author_email") or ""),
            actor_type=str(row.get("author_type") or "")))
        if org:
            orgs[login] = org
    return orgs


def _nomination_match(files: Mapping[str, Optional[Mapping[str, Any]]]
                      ) -> Optional[dict[str, str]]:
    """The first nomination-process line in a 3.3 governance file body."""
    for fname in ("GOVERNANCE.md", "MAINTAINERS.md", "CONTRIBUTING.md"):
        entry = files.get(fname)
        if not entry:
            continue
        for line in str(entry.get("body") or "").splitlines():
            if NOMINATION_PATTERN.search(line):
                return {"file": fname, "line": line.strip()[:200]}
    return None


def classify_promotion_ladder(prw: Optional[Mapping[str, Any]],
                              files: Mapping[str, Optional[Mapping[str, Any]]],
                              c365: Optional[Sequence[Mapping[str, Any]]]
                              ) -> tuple[str, dict[str, Any]]:
    """02 §4 3.3: the promotion ladder rung.

    ``prw`` is the recorded ``pr_window`` envelope data (None when
    absent), ``files`` the governance-file entries, ``c365`` the
    ``commits_365d`` envelope rows. Returns ``(value, evidence)``
    with ``value`` in
    :data:`cvi.core.constants.PROMOTION_LADDER_VALUES`; priority is
    VERIFIED > CLOSED > DOCUMENTED > AD_HOC (module docstring).
    """
    prs = list((prw or {}).get("prs") or [])
    evidence: dict[str, Any] = {"pr_window_envelope": prw is not None}

    observed: dict[str, list[tuple[datetime, str]]] = {}
    for pr in prs:
        author = pr.get("author") or {}
        login = str(author.get("login") or "").strip()
        if not login or pr_window.is_bot_actor(author.get("__typename"), login):
            continue
        parsed = _parse_iso(pr.get("createdAt"))
        if parsed is None:
            continue
        observed.setdefault(login, []).append(
            (parsed, str(pr.get("authorAssociation") or "NONE")))
    promoted: list[dict[str, str]] = []
    for login, entries in observed.items():
        entries.sort(key=lambda pair: pair[0])
        external = [i for i, (_dt, assoc) in enumerate(entries)
                    if assoc in pr_window.EXTERNAL_ASSOCIATIONS]
        target = [i for i, (_dt, assoc) in enumerate(entries)
                  if assoc in PROMOTION_TARGET_ASSOCIATIONS]
        if external and target and min(external) < max(target):
            promoted.append({"login": login,
                             "from": entries[min(external)][1],
                             "to": entries[max(target)][1]})
    evidence["promoted_accounts"] = promoted
    if promoted:
        return "VERIFIED", {**evidence, "rule": "verified_transition"}

    merged = [pr for pr in prs if pr.get("mergedAt")]
    evidence["merged_pr_count"] = len(merged)
    external_merges = sum(
        1 for pr in merged
        if str(pr.get("authorAssociation") or "NONE") in pr_window.EXTERNAL_ASSOCIATIONS)
    evidence["external_merges"] = external_merges
    evidence["merged_author_orgs"] = None
    orgs_by_login = _orgs_by_login(c365) if c365 is not None else {}
    if merged and external_merges == 0 and orgs_by_login:
        author_orgs: list[str] = []
        for pr in merged:
            login = str(((pr.get("author") or {}).get("login")) or "").strip()
            org = orgs_by_login.get(login)
            if not org:
                author_orgs = []
                break
            author_orgs.append(org)
        if author_orgs:
            evidence["merged_author_orgs"] = sorted(set(author_orgs))
            if len(set(author_orgs)) == 1:
                return "CLOSED", {**evidence, "rule": "closed_single_org"}
    match = _nomination_match(files or {})
    evidence["document_match"] = match
    if match:
        return "DOCUMENTED", {**evidence, "rule": "documented_nomination"}
    return "AD_HOC", {**evidence, "rule": "no_evidence"}


def classify_ai_posture(files: Mapping[str, Optional[Mapping[str, Any]]]
                        ) -> tuple[str, dict[str, Any]]:
    """02 §4 4.2: the AI policy posture.

    ``files`` are the recorded governance/AI-policy file entries. Returns
    ``(value, evidence)`` with ``value`` in
    :data:`cvi.core.constants.AI_POSTURE_VALUES``; priority is REGULATED
    (an :data:`AI_POLICY_FILES` entry is present) > AD_HOC. PROSCRIBED is
    never returned here (module docstring: ban detection cannot bind the
    ban's object). The commit-trailer REGULATED path
    of ``02`` §4 4.2 is not evaluated either (module docstring: this
    collector never reads commit message text).
    """
    present = sorted(fname for fname, entry in (files or {}).items() if entry)
    ai_policy_present = [f for f in present if f in AI_POLICY_FILES]
    evidence: dict[str, Any] = {"ai_policy_files_present": ai_policy_present}
    if ai_policy_present:
        return "REGULATED", {**evidence, "rule": "ai_policy_file"}
    return "AD_HOC", {**evidence, "rule": "no_evidence"}


def derive_governance_fields(gov: Optional[Mapping[str, Any]],
                             prw: Optional[Mapping[str, Any]],
                             c365: Optional[Any],
                             foundation_owner: bool = False
                             ) -> dict[str, tuple[Any, str]]:
    """Bundle fields for 02 §4 3.1/3.3/4.2 from the collected envelopes.

    ``gov`` is the ``governance`` envelope data (checks + files),
    ``prw`` the ``pr_window`` envelope data, ``c365`` the
    ``commits_365d`` envelope rows (they carry ``signed_off`` --
    see :func:`signoff_share`). Returns ``{field: (value, confidence)}`` for
    the six fields written by the ``scripts/derive.py`` hook: ``legal_ip`` /
    ``legal_ip_evidence``, ``promotion_ladder`` /
    ``promotion_ladder_evidence`` and ``ai_posture`` /
    ``ai_posture_evidence`` (the ``_v2`` suffix marks the v2 vocabulary
    where the v1 bundle already carries a same-named v1-enum field driving
    the live v1 scoring sweep -- 00-README non-negotiable: never change a
    running sweep).

    Confidence, per field: an envelope absent or failed to fetch is
    Unmeasured(collector_failure); an envelope that fetched successfully
    and found nothing is a measured zero (D-MISS-1) -- ``"missing"`` is
    this v1-format document's spelling
    of Unmeasured (:func:`cvi.derive.bundle._load_v1` maps a ``"missing"``
    confidence to ``Unmeasured(reason="collector_failure")``), ``"derived"``
    a real measured value:

    * ``legal_ip``: ``"missing"`` only when **both** the governance-check
      envelope and the commits_365d envelope are absent -- either one alone
      still lets :func:`classify_legal_ip` derive a real value (checks or
      the sign-off share).
    * ``promotion_ladder``: ``"missing"`` only when **both** the
      pr_window envelope and the governance-file envelope are absent --
      VERIFIED/CLOSED both need ``prs`` from pr_window, DOCUMENTED needs
      only the governance files, so either source alone still lets
      :func:`classify_promotion_ladder` derive a real rung.
    * ``ai_posture``: ``"missing"`` when the governance-file envelope is
      absent -- it is the only source of file bodies.

    A previously-collected envelope that is simply empty (zero checks, zero
    files) is not "absent": it is passed to the classifier as usual and
    genuinely measures the RC default (NONE/AD_HOC/AD_HOC).
    """
    files = (gov or {}).get("files") or {}
    check_names = (gov or {}).get("check_names") or []
    signoff = signoff_share(c365)

    if gov is None and c365 is None:
        legal, legal_ev, legal_conf = (
            "NONE", {"reason": "no governance or commits_365d envelope"}, "missing")
    else:
        legal, legal_ev = classify_legal_ip(check_names, signoff, foundation_owner)
        legal_conf = "derived"
        # D-COL-12. A POSITIVE finding stands on one probe: an EasyCLA context
        # proves a CLA exists no matter how few commits were examined, and a
        # DCO sign-off share is computed from commits_365d, not from the probe
        # at all. Only the NEGATIVE default -- rule "no_evidence", i.e. "we saw
        # no CLA check, therefore NONE" -- depends on having examined enough
        # commits, and capping the probe at CHECK_EVIDENCE_SHA_CAP makes a thin
        # sample likelier (the newest merges can all be maintainers' own PRs,
        # which carry no CLA check). Below CHECK_EVIDENCE_MIN_SHAS distinct
        # probed SHAs that is absence of evidence, not evidence of absence, so
        # 3.1 becomes an evidence gap instead of a measured NONE.
        # A governance envelope written before this field existed does not
        # record the count; treat that as "not recorded" and leave the old
        # behaviour, rather than retroactively gapping an existing corpus.
        probed = (gov or {}).get("checks_shas_probed")
        if (legal_ev.get("rule") == "no_evidence" and isinstance(probed, int)
                and probed < CHECK_EVIDENCE_MIN_SHAS):
            legal_ev = {**legal_ev,
                        "reason": (f"only {probed} distinct head SHA(s) probed for check evidence; "
                                   f"{CHECK_EVIDENCE_MIN_SHAS} are needed to conclude no CLA"),
                        "checks_shas_probed": probed,
                        "checks_shas_available": (gov or {}).get("checks_shas_available")}
            legal_conf = "missing"

    if prw is None and gov is None:
        ladder, ladder_ev, ladder_conf = (
            "AD_HOC", {"reason": "no pr_window or governance envelope"}, "missing")
    else:
        ladder, ladder_ev = classify_promotion_ladder(prw, files, c365)
        ladder_conf = "derived"

    if gov is None:
        posture, posture_ev, posture_conf = (
            "AD_HOC", {"reason": "no governance envelope"}, "missing")
    else:
        posture, posture_ev = classify_ai_posture(files)
        posture_conf = "derived"

    return {
        "legal_ip": (legal, legal_conf),
        "legal_ip_evidence": (legal_ev, legal_conf),
        "promotion_ladder": (ladder, ladder_conf),
        "promotion_ladder_evidence": (ladder_ev, ladder_conf),
        "ai_posture": (posture, posture_conf),
        "ai_posture_evidence": (posture_ev, posture_conf),
    }
