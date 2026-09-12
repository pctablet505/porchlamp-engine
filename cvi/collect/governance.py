"""W1-6: governance evidence — legal IP, promotion ladder, AI posture.

Collects, per repository, the raw evidence for the three governance
parameters of ``docs/v2/02-RANKING-DESIGN.md`` section 4 (3.1, 3.3, 4.2)
and derives them with ``NONE`` / ``AD_HOC`` / ``AD_HOC`` defaults:

* **3.1 Legal IP** — check-run names and commit-status contexts on the
  :data:`MERGED_PR_CAP` (20) most recent merged PRs (DCO, EasyCLA,
  cla-assistant, google-cla, ...). The DCO alternative evidence is the
  ``Signed-off-by`` share over the trailing 365 d commits: the W1-5
  ``commits_365d`` envelope (:mod:`cvi.collect.commits`) already receives
  ``commit.message`` in its raw REST response but discarded it; the W1-6
  ownership extension there (module docstring) now also records a
  per-commit ``signed_off`` boolean, so this module computes the share
  from the existing envelope with **no second commit fetch** and no
  message text stored anywhere (``03`` section 3: documents/text are not
  evidence for 3.1; only checks and the sign-off fact are).
* **3.3 Promotion ladder** — association transitions read from the
  recorded ``pr_window`` envelope (W1-3): an account whose earliest
  observed association in the window is external and whose latest is
  MEMBER/COLLABORATOR is a verified promotion. The CLOSED rung needs the
  merged PR authors attributable to one email-domain org via the
  ``commits_365d`` envelope (W1-5). DOCUMENTED reads the collected
  GOVERNANCE.md / MAINTAINERS.md / CONTRIBUTING.md bodies.
* **4.2 AI policy posture** — presence of an AI-policy file (the
  :data:`AI_POLICY_FILES` family) and explicit ban sentences in the
  collected governance/AI-policy file bodies. The commit-trailer
  convention path of ``02`` section 4 4.2 ("Assisted-by"/"AI-generated")
  needs commit message text; this packet's collection scope (05-WORK-PLAN
  W1-6 goal) is check names, the sign-off share, governance-file text and
  PR associations only, so that path is not evaluated here (recorded as a
  scope decision in the W1-6 handoff, not a defect).

Per ``03`` section 3, documents are not 3.1 evidence: the regime comes from
check/status names and the sign-off share only. Classification constants
are defined here, as the single definition (R-2); W2-1 moves them to the
v2 frozen constants module (``cvi.core.constants_v2``), which already owns
the value vocabularies (:data:`cvi.core.constants_v2.LEGAL_IP_VALUES`,
``PROMOTION_LADDER_VALUES``, ``AI_POSTURE_VALUES``).

Priority, highest first (documented decision, ``02`` §4 + ``03`` §3):

* 3.1: foundation-CLA check (or foundation owner) > corp-CLA check >
  DCO check > sign-off share ≥ :data:`SIGNOFF_THRESHOLD` > NONE. A CLA bot
  check is direct evidence of a CLA regime and outranks DCO evidence;
  the DCO/CLA check names never overlap in practice.
* 3.3: VERIFIED (observed transition) > CLOSED (single-org merge stream,
  no external merges) > DOCUMENTED (nomination text) > AD_HOC. CLOSED
  outranks DOCUMENTED so a closed repo cannot buy 75 by writing docs
  (``03`` §3: the cheapest fake for 3.3).
* 4.2: PROSCRIBED (explicit ban sentence) > REGULATED (policy file present)
  > AD_HOC.
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
from cvi.collect.envelope import write_envelope
# --------------------------------------------------------------------------- #
# 02 §4 / packet W1-6 constants (single definition until W2-1 moves them)     #
# --------------------------------------------------------------------------- #

#: Packet W1-6: check evidence comes from the 20 most recent merged PRs.
MERGED_PR_CAP: int = 20
#: Closed PRs scanned (newest first) to reach the 20 merged: 5 pages of 100.
PULLS_PAGES_MAX: int = 5
#: REST pulls page size (GitHub max).
PULLS_PAGE_SIZE: int = 100
#: 02 §4 3.1: DCO when at least 50% of window commits carry Signed-off-by.
#: The window and cap themselves are :mod:`cvi.collect.commits`'s
#: ``COMMIT_WINDOW_DAYS``/``COMMIT_CAP`` (R-2: one definition per fact) --
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

#: Check names / status contexts that evidence a foundation CLA (02 §4 3.1:
#: "EasyCLA/ASF/Eclipse/CNCF CLA check"). Case-insensitive substrings;
#: matched before the corp patterns so "EasyCLA" never falls into "cla".
FOUNDATION_CHECK_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (r"easycla", r"\basf\b", r"apache", r"cncf", r"eclipse"))
#: Check names / status contexts that evidence a corporate CLA bot
#: (02 §4 3.1: "cla-assistant/google-cla/other CLA bot present").
CORP_CLA_CHECK_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (r"cla[- ]?assistant", r"google[- ]?cla", r"\bcla\b"))
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

#: 02 §4 4.2 PROSCRIBED: an explicit ban sentence about AI-generated
#: content (case-insensitive, one line).
AI_BAN_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in (
        # "<AI>[- <modifier>] <noun> <verb> is/are not <accepted|...>"
        r"\b(ai|llm|gpt|copilot|chatgpt|machine[- ]generated|artificial[- ]intelligence)"
        r"([- ](generated|assisted|created|written|code|contributions?|content|submissions?|commits?|usage))?"
        r"( \w+){0,3}\s+(is|are)\s+(not|no longer|never)\s+"
        r"(accepted|allowed|permitted|welcome|merged)\b",
        # "<AI>[- <modifier>] <noun> <verb> is/are <prohibited|forbidden|banned|disallowed>"
        r"\b(ai|llm|gpt|copilot|chatgpt|machine[- ]generated|artificial[- ]intelligence)"
        r"([- ](generated|assisted|created|written|code|contributions?|content|submissions?|commits?|usage))?"
        r"( \w+){0,3}\s+(is|are)\s+(prohibited|forbidden|banned|disallowed)\b",
        # "no / not / never / without <AI>[- <modifier>]"
        r"\b(no|not|never|without)\s+(ai|llm|gpt|copilot|chatgpt|machine[- ]generated|artificial[- ]intelligence)"
        r"([- ](generated|assisted|created|written|code|contributions?|content|submissions?))?",
        # "do not / must not / ... use/submit/merge/accept/commit/contribute <AI>"
        r"\b(do\s+not|don't|never|must\s+not|shall\s+not|please\s+do\s+not)\s+"
        r"(use|submit|merge|accept|commit|contribute)\s+"
        r"(ai|llm|gpt|copilot|chatgpt|machine[- ]generated|artificial[- ]intelligence)",
        # "prohibit(s|ed)? (the) use/submission/merging/acceptance of <AI>"
        r"\bprohibit\w*\s+(the\s+)?(use|submission|merging|acceptance)\s+of\s+"
        r"(ai|llm|gpt|copilot|chatgpt|machine[- ]generated|artificial[- ]intelligence)",
        # "use/submission/merging/acceptance of <AI> is prohibited/banned/not allowed"
        r"\b(use|submission|merging|acceptance)\s+of\s+"
        r"(ai|llm|gpt|copilot|chatgpt|machine[- ]generated|artificial[- ]intelligence)"
        r"\s+is\s+(prohibited|banned|forbidden|not\s+(allowed|accepted|permitted))\b",
        # "ban/banned/prohibited/... <within 60 chars> <AI>"
        r"\b(ban|banned|banning|forbid|forbidden|prohibit|prohibited|disallow)\w*\b"
        r".{0,60}\b(ai|llm|gpt|copilot|chatgpt|machine[- ]generated|artificial[- ]intelligence)\b",
    ))
# Note: 02 §4 4.2's "Assisted-by"/"AI-generated" commit-trailer convention
# path is not evaluated by this module -- it needs commit message text,
# outside this packet's collection scope (module docstring).


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
    """D-IN-1 over one recorded commit row (delegates to W1-5's typing)."""
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


def _check_evidence(api: RestTransport, owner: str, name: str,
                    merged_prs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Status contexts and check-run names on each merged PR's head commit.

    One ``/commits/{sha}/statuses`` and one ``/commits/{sha}/check-runs``
    call per distinct head SHA (GitHub records EasyCLA as a status context
    and cla-assistant/google-cla as check runs; both are 3.1 evidence).
    """
    checks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pr in merged_prs:
        sha = str(pr.get("head_sha") or "")
        if not sha or sha in seen:
            continue
        seen.add(sha)
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
    return checks


def signoff_share(c365_rows: Optional[Sequence[Mapping[str, Any]]]) -> Optional[dict[str, Any]]:
    """02 §4 3.1: the ``Signed-off-by`` share over the non-bot commits (D-IN-1).

    ``c365_rows`` are the recorded ``commits_365d`` envelope rows (W1-5,
    read by the ``scripts/derive.py`` hook and passed straight through --
    no separate fetch); each row's ``signed_off`` boolean is the W1-6
    ownership extension of :func:`cvi.collect.commits.shape_commit_rows`.
    Returns ``None`` when no ``commits_365d`` envelope was collected at all
    (as opposed to an envelope with zero rows, which returns a share of
    ``None`` inside a real ``{"count": 0, "total": 0, "share": None}``).
    """
    if c365_rows is None:
        return None
    rows = list(c365_rows)
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
    checks = _check_evidence(api, owner, name, merged_prs)
    files: dict[str, Optional[dict[str, Any]]] = {}
    for fname in (*GOVERNANCE_FILES, *AI_POLICY_FILES):
        body = fetch_file(fname)
        files[fname] = None if body is None else {"present": True, "body": body}
    data: dict[str, Any] = {
        "pulls_available": pulls_available,
        "merged_pr_cap": MERGED_PR_CAP,
        "merged_prs": merged_prs,
        "checks": checks,
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
    :data:`cvi.core.constants_v2.LEGAL_IP_VALUES`; the evidence dict is
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
            int(signoff.get("count") or 0) * 2 >= int(signoff["total"]):
        return "DCO", {**evidence, "rule": "signoff_share"}
    return "NONE", {**evidence, "rule": "no_evidence"}


def _orgs_by_login(c365: Optional[Mapping[str, Any]]) -> dict[str, str]:
    """author_login -> email-domain org over the commits_365d envelope rows."""
    orgs: dict[str, str] = {}
    for row in (c365 or []):
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
                              c365: Optional[Any]) -> tuple[str, dict[str, Any]]:
    """02 §4 3.3: the promotion ladder rung.

    ``prw`` is the recorded ``pr_window`` envelope data (W1-3; None when
    absent), ``files`` the governance-file entries, ``c365`` the
    ``commits_365d`` envelope rows (W1-5). Returns ``(value, evidence)``
    with ``value`` in
    :data:`cvi.core.constants_v2.PROMOTION_LADDER_VALUES`; priority is
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


def _ban_match(files: Mapping[str, Optional[Mapping[str, Any]]]
               ) -> Optional[dict[str, str]]:
    """The first explicit AI-ban line in a collected governance/AI file."""
    for fname in (*GOVERNANCE_FILES, *AI_POLICY_FILES):
        entry = files.get(fname)
        if not entry:
            continue
        for line in str(entry.get("body") or "").splitlines():
            for pattern in AI_BAN_PATTERNS:
                if pattern.search(line):
                    return {"file": fname, "line": line.strip()[:200]}
    return None


def classify_ai_posture(files: Mapping[str, Optional[Mapping[str, Any]]]
                        ) -> tuple[str, dict[str, Any]]:
    """02 §4 4.2: the AI policy posture.

    ``files`` are the recorded governance/AI-policy file entries. Returns
    ``(value, evidence)`` with ``value`` in
    :data:`cvi.core.constants_v2.AI_POSTURE_VALUES``; priority is
    PROSCRIBED > REGULATED > AD_HOC (module docstring). The commit-trailer
    REGULATED path of ``02`` §4 4.2 is not evaluated (module docstring:
    outside this packet's collection scope).
    """
    present = sorted(fname for fname, entry in (files or {}).items() if entry)
    ai_policy_present = [f for f in present if f in AI_POLICY_FILES]
    evidence: dict[str, Any] = {
        "ban": _ban_match(files or {}),
        "ai_policy_files_present": ai_policy_present,
    }
    if evidence["ban"]:
        return "PROSCRIBED", {**evidence, "rule": "proscribed_ban"}
    if ai_policy_present:
        return "REGULATED", {**evidence, "rule": "ai_policy_file"}
    return "AD_HOC", {**evidence, "rule": "no_evidence"}


def derive_governance_fields(gov: Optional[Mapping[str, Any]],
                             prw: Optional[Mapping[str, Any]],
                             c365: Optional[Any],
                             foundation_owner: bool = False
                             ) -> dict[str, tuple[Any, str]]:
    """Bundle fields for 02 §4 3.1/3.3/4.2 from the collected envelopes.

    ``gov`` is the ``governance`` envelope data (W1-6; checks + files),
    ``prw`` the ``pr_window`` envelope data (W1-3), ``c365`` the
    ``commits_365d`` envelope rows (W1-5, now carrying ``signed_off`` --
    see :func:`signoff_share`). Returns ``{field: (value, confidence)}``
    for the six fields written by the ``scripts/derive.py`` hook:
    ``legal_ip`` / ``legal_ip_evidence``, ``promotion_ladder_v2`` /
    ``promotion_ladder_v2_evidence`` and ``ai_posture_v2`` /
    ``ai_posture_v2_evidence`` (the ``_v2`` suffix marks the v2 vocabulary
    where the v1 bundle already carries a same-named v1-enum field driving
    the live v1 scoring sweep -- 00-README non-negotiable: never change a
    running sweep). All values are ``derived``: the envelope facts are
    verified, the classification is a documented rule over them.

    The RC defaults ``NONE``/``AD_HOC``/``AD_HOC`` (02 §4 class RC:
    repo-controlled, measured zero) apply only when **neither** a
    governance envelope nor a commits_365d envelope exists -- i.e. nothing
    has been collected for this repository yet. When one of the two
    exists (e.g. a governance-collector failure with commits_365d still
    present, or vice versa), the classifiers below already treat the
    missing side as "no evidence on that path" and derive a real value
    from whichever evidence is present.
    """
    no_envelope: dict[str, Any] = {"reason": "no governance envelope"}
    if gov is None and c365 is None:
        return {
            "legal_ip": ("NONE", "derived"),
            "legal_ip_evidence": (no_envelope, "derived"),
            "promotion_ladder_v2": ("AD_HOC", "derived"),
            "promotion_ladder_v2_evidence": (no_envelope, "derived"),
            "ai_posture_v2": ("AD_HOC", "derived"),
            "ai_posture_v2_evidence": (no_envelope, "derived"),
        }
    files = (gov or {}).get("files") or {}
    check_names = (gov or {}).get("check_names") or []
    signoff = signoff_share(c365)
    legal, legal_ev = classify_legal_ip(check_names, signoff, foundation_owner)
    ladder, ladder_ev = classify_promotion_ladder(prw, files, c365)
    posture, posture_ev = classify_ai_posture(files)
    return {
        "legal_ip": (legal, "derived"),
        "legal_ip_evidence": (legal_ev, "derived"),
        "promotion_ladder_v2": (ladder, "derived"),
        "promotion_ladder_v2_evidence": (ladder_ev, "derived"),
        "ai_posture_v2": (posture, "derived"),
        "ai_posture_v2_evidence": (posture_ev, "derived"),
    }
