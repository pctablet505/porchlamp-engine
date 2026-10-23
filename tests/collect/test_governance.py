"""Tests for governance evidence (legal IP, promotion ladder, AI posture).

Subject: ``porchlamp.collect.governance`` — the governance envelope collector
(check-run names / status contexts on the 20 most recent merged PRs and the
GOVERNANCE/MAINTAINERS/CONTRIBUTING/SECURITY + AI-policy file bodies), the
classifiers, the ownership extension of
``porchlamp.collect.commits`` (a per-commit ``signed_off`` boolean, no message
text), and the append-only hooks at the end of ``scripts/gather.py`` (the
``governance`` status entry) and ``scripts/derive.py`` (the six ``legal_ip``
/ ``promotion_ladder`` / ``ai_posture`` value + evidence fields).
Recorded-response oracles at the REST transport boundary (no live calls)
assert the exact classification value and evidence for every path, including
the no-evidence ``NONE``/``AD_HOC``/``AD_HOC`` defaults. The Signed-off-by
share is *not* fetched by this collector: it is computed from the
already-collected ``commits_365d`` envelope, so ``collect_governance``
never fetches commits and never stores commit message text.
"""
from __future__ import annotations

import importlib.util
import re
import sys

import pytest
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

from porchlamp.collect import governance as gov
from porchlamp.collect.envelope import envelope_hash, read_envelope, write_envelope

ROOT = Path(__file__).resolve().parent.parent.parent
NOW = datetime(2026, 9, 12, 0, 0, 0, tzinfo=timezone.utc)
KEY = "acme__widget"
OWNER, NAME = "acme", "widget"


def _load_script(modname: str, fname: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(modname, ROOT / "scripts" / fname)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    return module


derive = _load_script("porchlamp_derive_governance", "derive.py")
gather = _load_script("porchlamp_gather_governance", "gather.py")


# --- recorded-response transport --------------------------------------------- #

def _pr(number: int, created: str, merged_at: Optional[str],
        author: str = "alice", head: str = "sha-h") -> dict:
    return {"number": number, "created_at": created, "merged_at": merged_at,
            "user": {"login": author} if author else None,
            "head": {"sha": head}}


class FakeApi:
    """Recorded-response REST transport: scripted pages by path.

    ``pulls`` is a paged list; ``statuses_by_sha`` and ``checkruns_by_sha``
    are the recorded per-commit answers. Any path without a recording is a
    test failure, not a default. ``collect_governance`` never calls the
    ``commits`` endpoint (module docstring: the sign-off share comes from
    the already-collected ``commits_365d`` envelope), so this fake carries
    no commit-listing state.
    """

    def __init__(self, pulls: list[list[dict]],
                 statuses_by_sha: Optional[dict[str, list[dict]]] = None,
                 checkruns_by_sha: Optional[dict[str, list[dict]]] = None,
                 fetch_error: Optional[str] = None) -> None:
        self.pulls = pulls
        self.statuses_by_sha = statuses_by_sha or {}
        self.checkruns_by_sha = checkruns_by_sha or {}
        self.fetch_error = fetch_error
        self.calls: list[tuple[str, dict]] = []

    @staticmethod
    def _sha(path: str) -> str:
        return path.split("/commits/", 1)[1].split("/", 1)[0]

    def call(self, path: str, params: Mapping[str, str]) -> Any:
        if self.fetch_error:
            raise RuntimeError(self.fetch_error)
        self.calls.append((path, dict(params)))
        page = int(params.get("page", "1"))
        if path.endswith("/pulls"):
            assert page <= len(self.pulls), "pulls page beyond recording"
            return self.pulls[page - 1]
        if path.endswith("/statuses"):
            return self.statuses_by_sha.get(self._sha(path), [])
        if path.endswith("/check-runs"):
            runs = self.checkruns_by_sha.get(self._sha(path), [])
            return {"total_count": len(runs), "check_runs": runs}
        raise AssertionError(f"unexpected REST path: {path}")


def _status(name: str, state: str = "success") -> dict:
    return {"context": name, "state": state}


def _checkrun(name: str, conclusion: str = "success") -> dict:
    return {"name": name, "status": "completed", "conclusion": conclusion}


def _gov_data(merged_prs, checks, check_names, files) -> dict:
    return {"pulls_available": True,
            "merged_pr_cap": gov.MERGED_PR_CAP,
            "merged_prs": merged_prs,
            "checks": checks,
            "check_names": check_names,
            "files": files}


def _c365_row(login: str, email: str, signed_off: bool = False,
              typ: str = "User", date: str = "2026-09-01T00:00:00Z") -> dict:
    """One ``commits_365d`` envelope row."""
    return {"date": date, "author_email": email, "author_login": login,
            "author_type": typ, "signed_off": signed_off}


# --- collector ---------------------------------------------------------------- #

def test_collect_governance_envelope_evidence(tmp_path):
    # recorded page in REST order: newest created first (closed-unmerged,
    # then the 22 merged of August newest-first).
    pulls = [_pr(100 + i, f"2026-09-{i % 28 + 1:02d}T00:00:00Z", None)
             for i in range(80, 0, -1)]
    pulls += [_pr(i, f"2026-08-{i:02d}T00:00:00Z", f"2026-08-{i + 1:02d}T00:00:00Z",
                  head=f"h{i:02d}") for i in range(22, 0, -1)]
    statuses = {f"h{i:02d}": [_status("EasyCLA"), _status("tide")]
                for i in range(0, 20, 3)}
    # h20 is inside the newest-CHECK_EVIDENCE_SHA_CAP window (h22..h18), so this
    # fixture still exercises BOTH evidence kinds under D-COL-12's cap; the
    # older entries (h00/h07/h14) are deliberately outside it.
    runs = {f"h{i:02d}": [_checkrun("cla")] for i in (0, 7, 14, 20)}
    fake = FakeApi([pulls], statuses, runs)
    bodies = {"GOVERNANCE.md": "governance body\n", "AI_POLICY.md": "ai body\n"}

    def fetch_file(path: str) -> Optional[str]:
        return bodies.get(path)

    assert gov.collect_governance(tmp_path / "raw", OWNER, NAME, now=NOW,
                                  api=fake, fetch_file=fetch_file) == "ok"
    env = read_envelope(tmp_path / "raw" / "governance" / f"{KEY}.json")
    assert env.hash_matches_data
    assert env.recorded_sha256 == envelope_hash(env.data)
    assert env.source == "github-rest:governance"
    d = env.data
    assert len(d["merged_prs"]) == gov.MERGED_PR_CAP
    assert [p["number"] for p in d["merged_prs"]] == list(range(22, 2, -1))
    assert d["merged_prs"][0]["head_sha"] == "h22"
    # D-COL-12: only the newest CHECK_EVIDENCE_SHA_CAP distinct head SHAs are
    # probed. Evidence on older commits is no longer collected -- that is the
    # trade the cap makes -- so the fixture places one check-run inside the
    # probed window to keep both evidence kinds covered. What must NOT change
    # is the verdict, asserted directly below: the capped sample classifies
    # this repository identically to the full uncapped set, which is the whole
    # argument for the cap (twenty probes bought near-duplicate evidence at
    # four times the API cost).
    assert "EasyCLA" in d["check_names"] and "tide" in d["check_names"] and "cla" in d["check_names"]
    assert d["checks_shas_probed"] == gov.CHECK_EVIDENCE_SHA_CAP
    assert d["checks_shas_available"] == gov.MERGED_PR_CAP
    assert (gov.classify_legal_ip(d["check_names"], None, False)[0]
            == gov.classify_legal_ip(["EasyCLA", "tide", "cla"], None, False)[0]), \
        "the capped sample must reach the same legal-IP verdict as the full one"
    assert d["check_names"] == sorted(set(d["check_names"]))
    kinds = {(c["name"], c["kind"]) for c in d["checks"]}
    assert ("EasyCLA", "status") in kinds and ("cla", "check_run") in kinds
    assert d["files"]["GOVERNANCE.md"]["body"] == "governance body\n"
    assert d["files"]["MAINTAINERS.md"] is None
    assert "commits" not in d, "no commit fetch: the sign-off share reuses commits_365d"


def test_collect_governance_per_file_sha256_matches_independent_recomputation(tmp_path):
    """Every one of the 14 POLICY_FILES entries (GOVERNANCE_FILES +
    AI_POLICY_FILES + PR_TEMPLATE_FILES) carries a ``sha256`` computed over its
    own body bytes -- recomputed here with a plain ``hashlib.sha256`` call
    independently of the collector."""
    import hashlib

    all_files = gov.POLICY_FILES
    assert len(all_files) == 14
    bodies = {fname: f"distinct body #{i} for {fname}\n" for i, fname in enumerate(all_files)}

    def fetch_file(path: str) -> Optional[str]:
        return bodies.get(path)

    fake = FakeApi([[]])
    assert gov.collect_governance(tmp_path / "raw", OWNER, NAME, now=NOW,
                                  api=fake, fetch_file=fetch_file) == "ok"
    d = read_envelope(tmp_path / "raw" / "governance" / f"{KEY}.json").data
    checked = 0
    for fname in all_files:
        entry = d["files"][fname]
        assert entry is not None, fname
        expected = hashlib.sha256(bodies[fname].encode("utf-8")).hexdigest()
        assert entry["sha256"] == expected, fname
        assert entry["body"] == bodies[fname]
        checked += 1
    assert checked == 14


def test_collect_governance_pages_until_20_merged(tmp_path):
    page1 = [_pr(100 + i, f"2026-09-{i % 28 + 1:02d}T00:00:00Z", None)
             for i in range(90, 0, -1)]
    page1 += [_pr(i, f"2026-08-{i:02d}T00:00:00Z",
                  f"2026-08-{i + 1:02d}T00:00:00Z", head=f"h{i:02d}")
              for i in range(10, 0, -1)]
    page2 = [_pr(200 + i, f"2026-07-{i:02d}T00:00:00Z",
                 f"2026-07-{i + 1:02d}T00:00:00Z", head=f"m{i:02d}")
             for i in range(15, 0, -1)]
    fake = FakeApi([page1, page2])
    assert gov.collect_governance(tmp_path / "raw", OWNER, NAME, now=NOW,
                                  api=fake, fetch_file=lambda p: None) == "ok"
    d = read_envelope(tmp_path / "raw" / "governance" / f"{KEY}.json").data
    assert [p["number"] for p in d["merged_prs"]] == list(range(10, 0, -1)) + list(range(215, 205, -1))
    pull_calls = [c for c in fake.calls if c[0].endswith("/pulls")]
    assert len(pull_calls) == 2


def test_collect_governance_fewer_than_cap_merged(tmp_path):
    page1 = [_pr(200 + i, f"2026-09-{i % 28 + 1:02d}T00:00:00Z", None)
             for i in range(95, 0, -1)]
    page1 += [_pr(i, f"2026-08-{i:02d}T00:00:00Z", f"2026-08-{i + 1:02d}T00:00:00Z",
                  head=f"h{i}") for i in range(5, 0, -1)]
    fake = FakeApi([page1, []])
    assert gov.collect_governance(tmp_path / "raw", OWNER, NAME, now=NOW,
                                  api=fake, fetch_file=lambda p: None) == "ok"
    d = read_envelope(tmp_path / "raw" / "governance" / f"{KEY}.json").data
    assert len(d["merged_prs"]) == 5


def test_collect_governance_skip_and_force(tmp_path):
    fake = FakeApi([[]])
    assert gov.collect_governance(tmp_path / "raw", OWNER, NAME, now=NOW,
                                  api=fake, fetch_file=lambda p: None) == "ok"
    assert len(fake.calls) == 1  # pulls page 1 only: no commit fetch
    assert gov.collect_governance(tmp_path / "raw", OWNER, NAME, now=NOW,
                                  api=fake, fetch_file=lambda p: None) == "skip"
    assert len(fake.calls) == 1
    assert gov.collect_governance(tmp_path / "raw", OWNER, NAME, now=NOW,
                                  api=fake, fetch_file=lambda p: None,
                                  force=True) == "ok"
    assert len(fake.calls) == 2


def test_collect_governance_pulls_disabled_repo(tmp_path):
    """A repo with issues/PRs disabled (torvalds/linux): pulls 404 is a fact.

    The collector records ``pulls_available: false`` and no merged PRs,
    while the file evidence is still collected.
    """

    class PullsDisabled(FakeApi):
        def call(self, path: str, params: Mapping[str, str]) -> Any:
            if path.endswith("/pulls"):
                raise gov.GhApiError("gh api rc=1: gh: Not Found (HTTP 404)", 404)
            return super().call(path, params)

    fake = PullsDisabled([[]])
    bodies = {"SECURITY.md": "report privately\n"}
    assert gov.collect_governance(tmp_path / "raw", OWNER, NAME, now=NOW,
                                  api=fake, fetch_file=lambda p: bodies.get(p)) == "ok"
    d = read_envelope(tmp_path / "raw" / "governance" / f"{KEY}.json").data
    assert d["pulls_available"] is False
    assert d["merged_prs"] == []
    assert d["check_names"] == []
    assert d["files"]["SECURITY.md"]["body"] == "report privately\n"


def test_collect_governance_check_evidence_sha_404_skipped(tmp_path):
    """A 404 on one head commit's statuses (GC'd PR head) skips that SHA."""

    class Sha404(FakeApi):
        def call(self, path: str, params: Mapping[str, str]) -> Any:
            if path.endswith("/statuses") and "h1" in path:
                raise gov.GhApiError("gh api rc=1: gh: Not Found (HTTP 404)", 404)
            return super().call(path, params)

    page1 = [_pr(1, "2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z", head="h1"),
             _pr(2, "2026-08-03T00:00:00Z", "2026-08-04T00:00:00Z", head="h2")]
    fake = Sha404([page1, []], statuses_by_sha={"h2": [_status("DCO")]})
    assert gov.collect_governance(tmp_path / "raw", OWNER, NAME, now=NOW,
                                  api=fake, fetch_file=lambda p: None) == "ok"
    d = read_envelope(tmp_path / "raw" / "governance" / f"{KEY}.json").data
    assert d["pulls_available"] is True
    assert d["check_names"] == ["DCO"]
    assert [c["sha"] for c in d["checks"]] == ["h2"]
    assert d["checks_shas_probed"] == 1
    assert d["checks_shas_available"] == 2


def test_collect_governance_transport_error_propagates(tmp_path):
    fake = FakeApi([[]], fetch_error="gh api rc=1: 404 Not Found")
    try:
        gov.collect_governance(tmp_path / "raw", OWNER, NAME, now=NOW,
                               api=fake, fetch_file=lambda p: None)
        raise AssertionError("transport error must propagate to the gather hook")
    except RuntimeError as e:
        assert "404" in str(e)
    assert not (tmp_path / "raw" / "governance").exists(), "no envelope on failure"


# --- signoff_share oracles --------------------------------------------------- #

def test_signoff_share_none_without_commits_365d_envelope():
    assert gov.signoff_share(None) is None


def test_signoff_share_empty_rows():
    assert gov.signoff_share([]) == {"count": 0, "total": 0, "share": None}


def test_signoff_share_counts_signed_off_true_rows():
    rows = [_c365_row("a", "a@corp.com", signed_off=True) for _ in range(81)] + \
           [_c365_row("b", "b@corp.com", signed_off=False) for _ in range(19)]
    assert gov.signoff_share(rows) == {"count": 81, "total": 100, "share": 0.81}


def test_signoff_share_excludes_bot_rows():
    rows = [_c365_row("github-actions", "b@bot.com", signed_off=True, typ="Bot"),
            _c365_row("alice", "a@corp.com", signed_off=True),
            _c365_row("carol", "c@corp.com", signed_off=False)]
    assert gov.signoff_share(rows) == {"count": 1, "total": 2, "share": 0.5}


# --- 3.1 legal IP oracles ------------------------------------------------------- #

def test_legal_ip_foundation_cla_easycla_check():
    value, ev = gov.classify_legal_ip(
        ["EasyCLA/LgtmEnabled", "tide", "build"],
        {"count": 90, "total": 100, "share": 0.9}, foundation_owner=False)
    assert value == "FOUNDATION_CLA"
    assert ev["rule"] == "foundation_cla_check"
    assert ev["check_names"] == ["EasyCLA/LgtmEnabled", "build", "tide"]


def test_legal_ip_foundation_owner_without_checks():
    value, ev = gov.classify_legal_ip([], None, foundation_owner=True)
    assert value == "FOUNDATION_CLA"
    assert ev["rule"] == "foundation_owner"
    assert ev["foundation_owner"] is True


def test_legal_ip_corp_cla_assistant():
    value, ev = gov.classify_legal_ip(["cla-assistant"], None)
    assert value == "CORP_CLA"
    assert ev["rule"] == "corp_cla_check"


def test_legal_ip_corp_cla_google_and_bare_cla():
    value, _ = gov.classify_legal_ip(["Google CLA", "ci"], None)
    assert value == "CORP_CLA"
    value, _ = gov.classify_legal_ip(["cla"], None)
    assert value == "CORP_CLA"


def test_legal_ip_cla_check_beats_dco_evidence():
    value, ev = gov.classify_legal_ip(
        ["EasyCLA", "DCO"], {"count": 90, "total": 100, "share": 0.9})
    assert value == "FOUNDATION_CLA"
    assert ev["rule"] == "foundation_cla_check"
    value, _ = gov.classify_legal_ip(
        ["cla", "DCO"], {"count": 90, "total": 100, "share": 0.9})
    assert value == "CORP_CLA"


def test_legal_ip_dco_check():
    value, ev = gov.classify_legal_ip(["DCO"], None)
    assert value == "DCO"
    assert ev["rule"] == "dco_check"


def test_legal_ip_dco_signoff_share_ge_half():
    value, ev = gov.classify_legal_ip(
        ["ci"], {"count": 81, "total": 100, "share": 0.81})
    assert value == "DCO"
    assert ev["rule"] == "signoff_share"
    assert ev["signoff"] == {"count": 81, "total": 100, "share": 0.81}


def test_legal_ip_signoff_share_exactly_half():
    value, _ = gov.classify_legal_ip([], {"count": 50, "total": 100, "share": 0.5})
    assert value == "DCO"


def test_legal_ip_signoff_share_below_half():
    value, ev = gov.classify_legal_ip(
        [], {"count": 49, "total": 100, "share": 0.49})
    assert value == "NONE"
    assert ev["rule"] == "no_evidence"


def test_legal_ip_none_without_any_evidence():
    value, ev = gov.classify_legal_ip(["ci"], None)
    assert value == "NONE"
    assert ev["rule"] == "no_evidence"
    assert ev["signoff"] is None


# --- A bare foundation-name substring is never a CLA ------------------------- #

#: Common CI/build/license-badge job names on an
#: Apache/Eclipse/CNCF/ASF-licensed repo, none of which is a CLA check.
FOUNDATION_SUBSTRING_FALSE_POSITIVES: tuple[str, ...] = (
    "license/apache-2.0", "apache-rat", "asf-jenkins-build",
    "cncf-conformance-e2e", "build (eclipse-temurin-17)", "test-on-apache-spark",
)


def test_legal_ip_foundation_substrings_alone_derive_none():
    """None of the six false-positive names is CLA evidence by itself."""
    for name in FOUNDATION_SUBSTRING_FALSE_POSITIVES:
        value, ev = gov.classify_legal_ip([name], None)
        assert value == "NONE", f"{name!r} wrongly derived {value!r}"
        assert ev["rule"] == "no_evidence"


@pytest.mark.parametrize("name", FOUNDATION_SUBSTRING_FALSE_POSITIVES)
def test_legal_ip_foundation_substrings_never_shadow_a_real_dco_check(name):
    """Each false-positive name alongside a real DCO check -- DCO must win."""
    value, ev = gov.classify_legal_ip([name, "DCO"], None)
    assert value == "DCO", f"{name!r} alongside DCO wrongly derived {value!r}"
    assert ev["rule"] == "dco_check"


def test_legal_ip_foundation_branded_cla_checks_still_derive_foundation_cla():
    """Over-correction guard: a genuine foundation-branded CLA check (not
    just EasyCLA) must still classify FOUNDATION_CLA."""
    for name in ("ASF CLA", "apache-cla", "cncf-cla", "eclipse-cla/validation"):
        value, ev = gov.classify_legal_ip([name], None)
        assert value == "FOUNDATION_CLA", f"{name!r} derived {value!r}"
        assert ev["rule"] == "foundation_cla_check"


def test_legal_ip_generic_cla_bot_names_derive_corp_cla():
    """Positive control: a real (non-foundation) CLA check name still
    classifies CORP_CLA."""
    for name in ("EasyCLA", "cla-assistant"):
        value, _ = gov.classify_legal_ip([name], None)
        assert value in ("FOUNDATION_CLA", "CORP_CLA")
    value, ev = gov.classify_legal_ip(["cla-assistant"], None)
    assert value == "CORP_CLA" and ev["rule"] == "corp_cla_check"
    value, ev = gov.classify_legal_ip(["EasyCLA"], None)
    assert value == "FOUNDATION_CLA" and ev["rule"] == "foundation_cla_check"


# --- SIGNOFF_THRESHOLD must actually be read -------------------------------- #

def test_legal_ip_signoff_threshold_is_read_from_the_constant(monkeypatch):
    """The sign-off threshold is caught by a test that changes the *constant*,
    not the inline literal."""
    monkeypatch.setattr(gov, "SIGNOFF_THRESHOLD", 0.9)
    value, _ = gov.classify_legal_ip([], {"count": 81, "total": 100, "share": 0.81})
    assert value == "NONE"  # 0.81 no longer clears the raised 0.9 threshold
    monkeypatch.setattr(gov, "SIGNOFF_THRESHOLD", 0.0)
    value, _ = gov.classify_legal_ip([], {"count": 0, "total": 100, "share": 0.0})
    assert value == "DCO"  # a lowered threshold now accepts zero sign-off


# --- 3.3 promotion ladder oracles ----------------------------------------------- #

def _prw(prs: list[dict]) -> dict:
    return {"prs": prs, "derived": []}


def _raw_pr(number: int, created: str, merged: Optional[str], login: str,
            assoc: str, typ: str = "User",
            merged_by: Optional[str] = None,
            merged_by_type: str = "User",
            author_created: Optional[str] = "2020-01-01T00:00:00Z",
            closed_events: Optional[list[dict]] = None) -> dict:
    author: dict[str, Any] = {"__typename": typ, "login": login}
    if author_created is not None:
        author["createdAt"] = author_created
    pr: dict[str, Any] = {
        "number": number,
        "createdAt": created,
        "mergedAt": merged,
        "author": author,
        "authorAssociation": assoc,
    }
    if merged_by is not None:
        pr["mergedBy"] = {"__typename": merged_by_type, "login": merged_by}
    if closed_events is not None:
        pr["closedEvents"] = closed_events
    return pr


def test_promotion_verified_external_to_member():
    # RFC-0016: even when GitHub returns the same current authorAssociation
    # ("MEMBER") on every PR by alice, action-based promotion detects that
    # alice first had an age-eligible PR merged by another human ("maint")
    # and >= 30d later merged another author's PR (#110 by "other").
    prw = _prw([_raw_pr(100, "2026-03-01T00:00:00Z", "2026-03-05T00:00:00Z",
                        "alice", "MEMBER", merged_by="maint"),
                _raw_pr(110, "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z",
                        "other", "NONE", merged_by="alice")])
    value, ev = gov.classify_promotion_ladder(prw, {}, [])
    assert value == "VERIFIED"
    assert ev["rule"] == "verified_transition"
    assert ev["promoted_accounts"] == [{
        "login": "alice",
        "first_outside_pr": 100,
        "first_outside_merged_at": "2026-03-05T00:00:00+00:00",
        "first_outside_merged_by": "maint",
        "outside_merged_count": 1,
        "first_maintainer_action": "merge",
        "first_maintainer_pr": 110,
        "first_maintainer_at": "2026-09-02T00:00:00+00:00",
        "outside_days": 181.0,
    }]


def test_promotion_no_transition_same_association():
    # alice has merged PRs in the window, but never performs a maintainer
    # action on another author's PR.
    prw = _prw([_raw_pr(100, "2026-03-01T00:00:00Z", "2026-03-05T00:00:00Z",
                        "alice", "MEMBER", merged_by="maint"),
                _raw_pr(110, "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z",
                        "alice", "MEMBER", merged_by="maint")])
    value, ev = gov.classify_promotion_ladder(prw, {}, [])
    assert value == "AD_HOC"
    assert ev["rule"] == "no_evidence"
    assert ev["promoted_accounts"] == []


def test_promotion_bot_author_never_counts():
    prw = _prw([_raw_pr(100, "2026-03-01T00:00:00Z", "2026-03-05T00:00:00Z",
                        "dep[bot]", "NONE", typ="Bot", merged_by="maint"),
                _raw_pr(110, "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z",
                        "other", "NONE", merged_by="dep[bot]", merged_by_type="Bot")])
    value, _ = gov.classify_promotion_ladder(prw, {}, [])
    assert value == "AD_HOC"


def test_promotion_member_to_external_is_not_promotion():
    # bob merges another author's PR first (2026-03-02), and only later has
    # his own PR merged by maint (2026-09-02): no outside phase before first
    # maintainer action.
    prw = _prw([_raw_pr(100, "2026-03-01T00:00:00Z", "2026-03-02T00:00:00Z",
                        "other", "NONE", merged_by="bob"),
                _raw_pr(110, "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z",
                        "bob", "NONE", merged_by="maint")])
    value, _ = gov.classify_promotion_ladder(prw, {}, [])
    assert value == "AD_HOC"


def _corp365(*logins_emails: tuple[str, str]) -> list[dict]:
    return [{"date": "2026-09-01T00:00:00Z", "author_email": email,
             "author_login": login, "author_type": "User", "signed_off": False}
            for login, email in logins_emails]


def test_promotion_closed_single_org_no_external_merges():
    prw = _prw([_raw_pr(1, "2026-06-01T00:00:00Z", "2026-06-02T00:00:00Z",
                        "bob", "MEMBER"),
                _raw_pr(2, "2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z",
                        "carol", "COLLABORATOR")])
    value, ev = gov.classify_promotion_ladder(
        prw, {}, _corp365(("bob", "bob@acme-corp.com"), ("carol", "carol@acme-corp.com")))
    assert value == "CLOSED"
    assert ev["rule"] == "closed_single_org"
    assert ev["merged_pr_count"] == 2
    assert ev["external_merges"] == 0
    assert ev["merged_author_orgs"] == ["acme-corp.com"]


def test_promotion_external_merge_blocks_closed():
    prw = _prw([_raw_pr(1, "2026-06-01T00:00:00Z", "2026-06-02T00:00:00Z",
                        "bob", "MEMBER"),
                _raw_pr(2, "2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z",
                        "dana", "NONE")])
    value, ev = gov.classify_promotion_ladder(
        prw, {}, _corp365(("bob", "bob@acme-corp.com"), ("dana", "dana@acme-corp.com")))
    assert value == "AD_HOC"
    assert ev["external_merges"] == 1


def test_promotion_two_orgs_not_closed():
    prw = _prw([_raw_pr(1, "2026-06-01T00:00:00Z", "2026-06-02T00:00:00Z",
                        "bob", "MEMBER"),
                _raw_pr(2, "2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z",
                        "carol", "COLLABORATOR")])
    value, ev = gov.classify_promotion_ladder(
        prw, {}, _corp365(("bob", "bob@acme-corp.com"), ("carol", "carol@other.com")))
    assert value == "AD_HOC"
    assert ev["merged_author_orgs"] == ["acme-corp.com", "other.com"]


def test_promotion_unattributable_author_blocks_closed():
    prw = _prw([_raw_pr(1, "2026-06-01T00:00:00Z", "2026-06-02T00:00:00Z",
                        "bob", "MEMBER"),
                _raw_pr(2, "2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z",
                        "ghost", "OWNER")])
    value, ev = gov.classify_promotion_ladder(prw, {}, _corp365(("bob", "bob@acme.com")))
    assert value == "AD_HOC"
    assert ev["merged_author_orgs"] is None


def test_promotion_documented_nomination_process():
    files = {"GOVERNANCE.md": {"present": True, "sha256": "h",
                               "body": "Submitters can become a maintainer after\nthree releases.\n"}}
    prw = _prw([_raw_pr(1, "2026-06-01T00:00:00Z", None, "zed", "NONE")])
    value, ev = gov.classify_promotion_ladder(prw, files, [])
    assert value == "DOCUMENTED"
    assert ev["rule"] == "documented_nomination"
    assert ev["document_match"]["file"] == "GOVERNANCE.md"
    assert "become a maintainer" in ev["document_match"]["line"]


def test_promotion_no_nomination_wording_stays_ad_hoc():
    files = {"MAINTAINERS.md": {"present": True, "sha256": "h",
                                "body": "The maintainers are listed here.\n"}}
    value, _ = gov.classify_promotion_ladder(_prw([]), files, [])
    assert value == "AD_HOC"


def test_promotion_verified_beats_closed_and_documented():
    prw = _prw([_raw_pr(1, "2026-06-01T00:00:00Z", "2026-06-02T00:00:00Z",
                        "alice", "MEMBER", merged_by="bob"),
                _raw_pr(2, "2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z",
                        "bob", "COLLABORATOR", merged_by="alice")])
    files = {"GOVERNANCE.md": {"present": True, "sha256": "h",
                               "body": "become a maintainer\n"}}
    value, ev = gov.classify_promotion_ladder(
        prw, files, _corp365(("alice", "a@one.org"), ("bob", "b@one.org")))
    assert value == "VERIFIED"
    assert ev["rule"] == "verified_transition"


def test_promotion_closed_beats_documented():
    prw = _prw([_raw_pr(1, "2026-06-01T00:00:00Z", "2026-06-02T00:00:00Z",
                        "bob", "MEMBER")])
    files = {"CONTRIBUTING.md": {"present": True, "sha256": "h",
                                 "body": "you may become a maintainer\n"}}
    value, ev = gov.classify_promotion_ladder(
        prw, files, _corp365(("bob", "bob@solo.org")))
    assert value == "CLOSED"
    assert ev["rule"] == "closed_single_org"


def test_promotion_without_pr_window_envelope():
    files = {"MAINTAINERS.md": {"present": True, "sha256": "h",
                                "body": "nominations open quarterly\n"}}
    value, ev = gov.classify_promotion_ladder(None, files, [])
    assert value == "DOCUMENTED"
    assert ev["pr_window_envelope"] is False
    value, ev = gov.classify_promotion_ladder(None, {}, [])
    assert value == "AD_HOC"
    assert ev["pr_window_envelope"] is False



# --- 4.2 AI posture oracles ------------------------------------------------------ #

def test_ai_posture_regulated_policy_file_present():
    files = {"AGENTS.md": {"present": True, "sha256": "h",
                           "body": "Agents: run the tests.\n"}}
    value, ev = gov.classify_ai_posture(files)
    assert value == "REGULATED"
    assert ev["rule"] == "ai_policy_file"
    assert ev["ai_policy_files_present"] == ["AGENTS.md"]


def test_ai_posture_ad_hoc_default():
    value, ev = gov.classify_ai_posture({})
    assert value == "AD_HOC"
    assert ev["rule"] == "no_evidence"
    assert ev["ai_policy_files_present"] == []
    assert "ban" not in ev


# --- RFC-0019: PROSCRIBED and REGULATED (ai_rule_text) oracles --------------- #

#: Ordinary CONTRIBUTING.md sentences to ensure false positives are not triggered:
ORDINARY_CONTRIBUTING_SENTENCES: tuple[str, ...] = (
    "Do not worry if you used AI to draft your PR",
    "We do not require disclosure of AI assistance",
    "Our CI does not lint AI-generated markdown",
    "AI reviewers never see your draft PRs",
    "The AI triage bot must not be assigned manually",
    "We never merge on Fridays, including AI-assisted patches",
    "Contributors do not need an AI policy to contribute here",
    "Do not open an issue just to ask whether AI is allowed",
    "Do not @-mention the maintainers; the AI bot will triage",
)


@pytest.mark.parametrize("sentence", ORDINARY_CONTRIBUTING_SENTENCES)
def test_ai_posture_ordinary_contributing_prose_is_ad_hoc_never_proscribed(sentence):
    files = {"CONTRIBUTING.md": {"present": True, "sha256": "h", "body": sentence + "\n"}}
    value, ev = gov.classify_ai_posture(files)
    assert value == "AD_HOC"
    assert value != "PROSCRIBED"
    assert ev["rule"] == "no_evidence"


def test_ai_posture_explicit_ai_policy_section_is_regulated():
    """A genuine AI-policy file (AI_POLICY.md, in AI_POLICY_FILES) with an
    explicit policy section: presence is sufficient for REGULATED when the body
    contains no unqualified ban, same mechanism as
    test_ai_posture_regulated_policy_file_present."""
    files = {"AI_POLICY.md": {"present": True, "sha256": "h",
                              "body": "## AI usage policy\n\n"
                                      "AI-assisted contributions are welcome "
                                      "if disclosed in the PR description.\n"}}
    value, ev = gov.classify_ai_posture(files)
    assert value == "REGULATED"
    assert ev["rule"] == "ai_policy_file"
    assert ev["ai_policy_files_present"] == ["AI_POLICY.md"]


def test_ai_posture_never_proscribed_over_all_twelve_files():
    """Every one of GOVERNANCE_FILES + AI_POLICY_FILES present at once, each
    carrying one of the nine ordinary sentences above (cycled to fill all
    twelve): none of the nine sentences is an unqualified ban, so PROSCRIBED
    does not fire, and REGULATED fires from the AI_POLICY_FILES entries'
    presence."""
    all_files = gov.GOVERNANCE_FILES + gov.AI_POLICY_FILES
    assert len(all_files) == 12
    files = {
        fname: {"present": True, "sha256": "h",
                "body": ORDINARY_CONTRIBUTING_SENTENCES[i % len(ORDINARY_CONTRIBUTING_SENTENCES)] + "\n"}
        for i, fname in enumerate(all_files)
    }
    value, ev = gov.classify_ai_posture(files)
    assert value != "PROSCRIBED"
    assert value == "REGULATED"
    assert ev["rule"] == "ai_policy_file"
    assert sorted(ev["ai_policy_files_present"]) == sorted(gov.AI_POLICY_FILES)


EXPLICIT_BAN_SENTENCES: tuple[str, ...] = (
    "We do not accept AI-generated PRs.",
    "Contributions must not include content generated by large language models or other probabilistic tools, including but not limited to Copilot or ChatGPT.",
    "The QEMU project thus requires that contributors refrain from using AI content generators on patches intended to be submitted to the project.",
    "No LLMs for pull requests.",
    "It is expressly forbidden to contribute to Gentoo any content that has been created with the assistance of Natural Language Processing artificial intelligence tools.",
    "AI-generated pull requests will be closed without review.",
    "Submissions written using generative AI (even partially) are not accepted.",
    "AI tools must not be used to write code for this project.",
    "Pull requests written by ChatGPT are not welcome.",
    "The use of Copilot is forbidden in this repository.",
)


@pytest.mark.parametrize("sentence", EXPLICIT_BAN_SENTENCES)
def test_ai_posture_proscribed_on_explicit_ban_sentences(sentence):
    """RFC-0019: an unqualified sentence banning AI-generated code or pull
    requests in CONTRIBUTING.md or a PR template classifies as PROSCRIBED and
    records the file and matching sentence."""
    for doc_name in ("CONTRIBUTING.md", ".github/PULL_REQUEST_TEMPLATE.md"):
        files = {doc_name: {"present": True, "sha256": "h", "body": f"# Policy\n\n{sentence}\n"}}
        value, ev = gov.classify_ai_posture(files)
        assert value == "PROSCRIBED", (doc_name, sentence)
        assert ev["rule"] == "explicit_ban"
        assert ev["ban"] == {"file": doc_name, "sentence": sentence}


def test_ai_posture_explicit_ban_in_ai_policy_file_beats_file_presence():
    """RFC-0019: an AGENTS.md or AI_POLICY.md file that exists to prohibit AI
    contributions must classify as PROSCRIBED rather than REGULATED."""
    sentence = "AI must not be used to generate code for contributions to this project."
    files = {"AGENTS.md": {"present": True, "sha256": "h", "body": sentence + "\n"}}
    value, ev = gov.classify_ai_posture(files)
    assert value == "PROSCRIBED"
    assert ev["rule"] == "explicit_ban"
    assert ev["ai_policy_files_present"] == ["AGENTS.md"]
    assert ev["ban"] == {"file": "AGENTS.md", "sentence": sentence}


QUALIFIED_OR_NON_CODE_BAN_SENTENCES: tuple[str, ...] = (
    "We do not accept AI-generated code that you do not understand.",
    "Fully AI-generated pull requests are not accepted.",
    "AI-generated PRs without disclosure will be closed.",
    "Low-quality AI-generated PRs will be closed.",
    "Do not submit AI-generated issues or comments.",
    "We do not accept AI-generated contributions unless they are disclosed.",
    "Undisclosed use of AI tools is prohibited.",
    "Code generated by a large language model or similar technology is presumed to be tainted code, and must not be committed without prior written approval by core.",
    "Do not post output from Large Language Models or similar generative AI as comments on GitHub.",
    "Do not submit AI-generated content that may introduce copyright issues.",
)


@pytest.mark.parametrize("sentence", QUALIFIED_OR_NON_CODE_BAN_SENTENCES)
def test_ai_posture_qualified_or_non_code_bans_are_regulated_not_proscribed(sentence):
    """RFC-0019: a ban narrowed by a condition (undisclosed, unreviewed,
    untested, low-quality, not understood) or scoped solely to non-code
    surfaces (issues, comments, reports, translations, artwork) regulates AI
    contributions rather than proscribing them."""
    files = {"CONTRIBUTING.md": {"present": True, "sha256": "h", "body": sentence + "\n"}}
    value, ev = gov.classify_ai_posture(files)
    assert value == "REGULATED", sentence
    assert ev["rule"] == "ai_rule_text"
    assert ev["rule_text"] == {"file": "CONTRIBUTING.md", "text": sentence}


@pytest.mark.parametrize(
    "doc_name, body, expected_text",
    [
        ("CONTRIBUTING.md", "## AI Usage Policy\n\nBe careful with tools.\n", "## AI Usage"),
        (".github/PULL_REQUEST_TEMPLATE.md", "- [ ] I have disclosed any AI tools used\n",
         "- [ ] I have disclosed any AI tools"),
        (".github/pull_request_template.md",
         "Please disclose any use of AI tools in the PR description.\n",
         "Please disclose any use of AI tools in the PR description."),
        (".github/CONTRIBUTING.md",
         "If you used an AI assistant, you must review and test the code yourself.\n",
         "If you used an AI assistant, you must review and test the code yourself."),
        ("docs/CONTRIBUTING.md",
         "Commits written with AI help must carry an Assisted-by: trailer.\n",
         "Commits written with AI help must carry an Assisted-by: trailer."),
    ],
)
def test_ai_posture_regulated_by_rule_text_in_contributing_and_pr_template(
    doc_name: str, body: str, expected_text: str
):
    """RFC-0019: headings, checkboxes, disclosure directives, review/test
    imperatives, and commit-trailer conventions in CONTRIBUTING.md or
    PULL_REQUEST_TEMPLATE.md classify as REGULATED with rule='ai_rule_text'."""
    files = {doc_name: {"present": True, "sha256": "h", "body": body}}
    value, ev = gov.classify_ai_posture(files)
    assert value == "REGULATED"
    assert ev["rule"] == "ai_rule_text"
    assert ev["rule_text"] == {"file": doc_name, "text": expected_text}


def test_ai_posture_ignores_non_ai_governance_files_for_prose_rules():
    """RFC-0019: GOVERNANCE.md, MAINTAINERS.md, and SECURITY.md are not scanned
    for AI prose rules (a vulnerability-reporting ban in SECURITY.md is about
    security reports, not code contributions)."""
    files = {
        "SECURITY.md": {
            "present": True,
            "sha256": "h",
            "body": "We do not accept AI-generated PRs or reports.\n",
        },
        "GOVERNANCE.md": {
            "present": True,
            "sha256": "h",
            "body": "## AI Usage Policy\n",
        },
    }
    value, ev = gov.classify_ai_posture(files)
    assert value == "AD_HOC"
    assert ev["rule"] == "no_evidence"


@pytest.mark.parametrize(
    "sentence",
    [
        "You don't need to disclose AI usage.",
        "Contributors are not required to disclose AI use.",
        "We do not ban AI-generated contributions.",
        "Nothing here forbids using AI tooling.",
        "AI-assisted contributions are welcome.",
        "May use AI tools for local development.",
    ],
)
def test_ai_posture_negated_requirement_and_permissive_prose_stay_ad_hoc(sentence: str):
    files = {"CONTRIBUTING.md": {"present": True, "sha256": "h", "body": sentence + "\n"}}
    value, ev = gov.classify_ai_posture(files)
    assert value == "AD_HOC", sentence
    assert ev["rule"] == "no_evidence"


def test_ai_posture_proscribed_wires_to_ai_hard_ban_persona_gate(tmp_path):
    """RFC-0019 end-to-end: a CONTRIBUTING.md ban sentence classifies as
    PROSCRIBED in derive_repo, resolves to Measured('PROSCRIBED') in
    bundle.py, and fires the D-PERS-2 ai_assisted hard-ban exclusion gate."""
    from porchlamp.core.persona import persona_gates
    from porchlamp.derive.bundle import load_bundle

    raw = tmp_path / "raw"
    files = {f: None for f in gov.POLICY_FILES}
    files["CONTRIBUTING.md"] = {
        "present": True,
        "sha256": "h",
        "body": "We do not accept AI-generated pull requests.\n",
    }
    gov_data = _gov_data(merged_prs=[], checks=[], check_names=[], files=files)
    write_envelope(raw / "governance" / f"{KEY}.json", source="test",
                   data=gov_data, fetched_at="2026-09-11T00:00:00Z")
    doc = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path,
                             fetch=False, now=NOW)
    assert doc["meta"]["ai_posture"] == "PROSCRIBED"
    assert doc["meta"]["ai_posture_evidence"]["rule"] == "explicit_ban"
    import json
    doc_path = tmp_path / f"{KEY}.json"
    doc_path.write_text(json.dumps(doc), encoding="utf-8")
    import types
    fb = load_bundle(doc_path, raw_root=raw)
    gates = persona_gates(types.SimpleNamespace(pillars={}), fb.inputs)
    assert gates.get("ai_assisted") == "ai_hard_ban"

# --- c365 must be a row sequence, never a bare mapping ----------------------- #

def test_orgs_by_login_dict_shaped_c365_does_not_crash():
    """A dict-shaped c365 iterates over its keys, not row mappings; must
    be treated as no usable rows, never crash with AttributeError."""
    assert gov._orgs_by_login({"bob": "acme.com"}) == {}


def test_signoff_share_dict_shaped_c365_does_not_crash():
    assert gov.signoff_share({"bob": "acme.com"}) is None


def test_signoff_share_reads_the_collectors_window_dict():
    """The one mapping that IS rows: ``collect_commits_365d`` records
    ``{"count", "cap", "truncated", "rows"}``. It was treated like the
    malformed mapping above, so every re-collected repo lost its sign-off
    evidence silently."""
    from porchlamp.collect.commits import collect_commits_365d
    raw = [{"commit": {"author": {"email": f"u{i}@corp.com", "date": "2026-09-01T00:00:00Z"},
                       "message": "Signed-off-by: U" if i % 4 else ""},
            "author": {"login": f"u{i}", "type": "User"}} for i in range(8)]
    window = collect_commits_365d(lambda page, since: raw if page == 1 else [])
    assert isinstance(window, dict)  # the producer's own shape, not a hand-built one
    assert gov.signoff_share(window) == {"count": 6, "total": 8, "share": 0.75}
    assert gov.signoff_share(window) == gov.signoff_share(window["rows"])


def test_promotion_ladder_dict_shaped_c365_does_not_crash():
    value, ev = gov.classify_promotion_ladder(None, {}, {"bob": "acme.com"})
    assert value == "AD_HOC"
    assert ev["merged_author_orgs"] is None


def test_derive_governance_fields_dict_shaped_c365_does_not_crash():
    result = gov.derive_governance_fields(
        None, None, {"bob": "acme.com"}, foundation_owner=False)
    assert result["promotion_ladder"] == ("AD_HOC", "missing")


# --- derive_repo integration ------------------------------------------------------ #

def test_derive_repo_no_governance_evidence_defaults_none_ad_hoc_ad_hoc(tmp_path):
    """No evidence at all is Unmeasured, not a measured NONE/AD_HOC/AD_HOC
    (a collector failure must not score better than the truth -- confidence
    ``"missing"`` is the representation of Unmeasured collector failure).
    """
    bundle = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    m = bundle["meta"]
    assert m.get("legal_ip", m.get("legal_ip_model")) == "NONE"
    assert m.get("promotion_ladder", m.get("promotion_ladder")) == "AD_HOC"
    assert m.get("ai_posture", m.get("ai_posture")) == "AD_HOC"
    assert m["legal_ip_evidence"] == {"reason": "no governance or commits_365d envelope"}
    assert m["promotion_ladder_evidence"] == {"reason": "no pr_window or governance envelope"}
    assert m["ai_posture_evidence"] == {"reason": "no governance envelope"}
    for field in ("legal_ip", "legal_ip_evidence", "promotion_ladder",
                  "promotion_ladder_evidence", "ai_posture",
                  "ai_posture_evidence"):
        assert bundle["confidence"][field] == "missing"


def test_derive_repo_v1_governance_fields_unchanged(tmp_path):
    """Over-correction guard: ``legal_ip_model`` is a separate field
    (distinct name from ``legal_ip``). ``promotion_ladder`` / ``ai_posture``
    deliberately supersede the older regex heuristic of the same
    name -- in this no-evidence fixture the heuristic default and the
    evidence-based Unmeasured default both spell AD_HOC, so the value is
    preserved.
    """
    bundle = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    m = bundle["meta"]
    assert m["legal_ip_model"] == "DCO"
    assert m["promotion_ladder"] == "AD_HOC"
    assert m["ai_posture"] == "AD_HOC"


def test_derive_repo_governance_evidence_fields(tmp_path):
    raw = tmp_path / "raw"
    gov_data = _gov_data(
        merged_prs=[], checks=[], check_names=["EasyCLA/LgtmEnabled"],
        files={f: None for f in gov.GOVERNANCE_FILES + gov.AI_POLICY_FILES})
    write_envelope(raw / "governance" / f"{KEY}.json", source="test",
                   data=gov_data, fetched_at="2026-09-11T00:00:00Z")
    prw = _prw([_raw_pr(100, "2026-03-01T00:00:00Z", "2026-03-05T00:00:00Z",
                        "alice", "MEMBER", merged_by="maint"),
                _raw_pr(110, "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z",
                        "other", "NONE", merged_by="alice")])
    write_envelope(raw / "pr_window" / f"{KEY}.json", source="test",
                   data=prw, fetched_at="2026-09-11T00:00:00Z")
    write_envelope(raw / "commits_365d" / f"{KEY}.json", source="test",
                   data=_corp365(("alice", "alice@corp.com")),
                   fetched_at="2026-09-11T00:00:00Z")
    bundle = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    m = bundle["meta"]
    assert m["legal_ip"] == "FOUNDATION_CLA"
    assert m["legal_ip_evidence"]["rule"] == "foundation_cla_check"
    assert m["promotion_ladder"] == "VERIFIED"
    assert m["promotion_ladder_evidence"]["promoted_accounts"][0]["login"] == "alice"
    assert m["ai_posture"] == "AD_HOC"
    assert m["ai_posture_evidence"]["rule"] == "no_evidence"
    for field in ("legal_ip", "legal_ip_evidence", "promotion_ladder",
                  "promotion_ladder_evidence", "ai_posture",
                  "ai_posture_evidence"):
        assert bundle["confidence"][field] == "derived"
        assert bundle["provenance"][field]["source"] == \
            f"raw/governance/{KEY}.json"


def test_derive_repo_governance_envelope_without_pr_window(tmp_path):
    raw = tmp_path / "raw"
    files = {f: None for f in gov.GOVERNANCE_FILES + gov.AI_POLICY_FILES}
    files["GOVERNANCE.md"] = {"present": True, "sha256": "h",
                              "body": "maintainers are nominated by the TSC\n"}
    gov_data = _gov_data(merged_prs=[], checks=[], check_names=[], files=files)
    write_envelope(raw / "governance" / f"{KEY}.json", source="test",
                   data=gov_data, fetched_at="2026-09-11T00:00:00Z")
    bundle = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    m = bundle["meta"]
    assert m["legal_ip"] == "NONE"
    assert m["legal_ip_evidence"]["rule"] == "no_evidence"
    assert m["promotion_ladder"] == "DOCUMENTED"
    assert m["promotion_ladder_evidence"]["pr_window_envelope"] is False
    assert m["ai_posture"] == "AD_HOC"


def test_derive_repo_signoff_share_alone_without_governance_envelope(tmp_path):
    """Mixed-evidence case: no governance envelope, but commits_365d exists
    (e.g. the governance collector failed while commits_365d succeeded).

    legal_ip still derives a real value (DCO) from the sign-off share alone
    -- the NONE/AD_HOC/AD_HOC default is reserved for "nothing collected at
    all" (neither envelope), not "this one envelope is missing".
    """
    raw = tmp_path / "raw"
    rows = [_c365_row(f"u{i}", f"u{i}@corp.com", signed_off=(i % 2 == 0))
            for i in range(100)]
    write_envelope(raw / "commits_365d" / f"{KEY}.json", source="test",
                   data=rows, fetched_at="2026-09-11T00:00:00Z")
    bundle = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    m = bundle["meta"]
    assert m["legal_ip"] == "DCO"
    assert m["legal_ip_evidence"]["rule"] == "signoff_share"
    assert m["legal_ip_evidence"]["signoff"] == {"count": 50, "total": 100, "share": 0.5}
    assert bundle["provenance"]["legal_ip"]["source"] == \
        f"raw/commits_365d/{KEY}.json"
    # no governance envelope -> no file/check evidence for the other two,
    # and both are Unmeasured (B2: neither prw nor gov exists for the
    # ladder; gov alone is absent for posture):
    assert m["promotion_ladder"] == "AD_HOC"
    assert m["ai_posture"] == "AD_HOC"
    assert bundle["confidence"]["legal_ip"] == "derived"
    assert bundle["confidence"]["promotion_ladder"] == "missing"
    assert bundle["confidence"]["ai_posture"] == "missing"


# --- Unmeasured(collector_failure) vs measured zero ------------------------- #

def test_legal_ip_unmeasured_when_both_envelopes_absent():
    """Fetch failure (neither envelope exists) -> Unmeasured ("missing")."""
    result = gov.derive_governance_fields(None, None, None, foundation_owner=False)
    assert result["legal_ip"] == ("NONE", "missing")
    assert result["legal_ip_evidence"][1] == "missing"


def test_legal_ip_measured_zero_when_governance_envelope_fetched_empty():
    """Fetch success + no evidence -> measured zero ("derived"), NOT
    Unmeasured -- a governance envelope that genuinely found no checks."""
    gov_data = _gov_data(merged_prs=[], checks=[], check_names=[], files={})
    result = gov.derive_governance_fields(gov_data, None, None, foundation_owner=False)
    assert result["legal_ip"] == ("NONE", "derived")
    assert result["legal_ip_evidence"][0]["rule"] == "no_evidence"


def test_promotion_ladder_unmeasured_when_pr_window_and_governance_absent():
    """Fetch failure (neither pr_window nor governance exists) -> Unmeasured."""
    result = gov.derive_governance_fields(None, None, None, foundation_owner=False)
    assert result["promotion_ladder"] == ("AD_HOC", "missing")
    assert result["promotion_ladder_evidence"][1] == "missing"


def test_promotion_ladder_measured_zero_when_pr_window_fetched_empty():
    """Fetch success + no evidence -> measured zero: an empty pr_window
    envelope genuinely found no PRs, so AD_HOC is a real answer."""
    result = gov.derive_governance_fields(None, _prw([]), None, foundation_owner=False)
    assert result["promotion_ladder"] == ("AD_HOC", "derived")
    assert result["promotion_ladder_evidence"][0]["rule"] == "no_evidence"


def test_ai_posture_unmeasured_when_governance_envelope_absent():
    """Fetch failure (governance envelope, the only file source, is absent)
    -> Unmeasured, even when other envelopes (prw/c365) exist."""
    result = gov.derive_governance_fields(
        None, _prw([]), [_c365_row("a", "a@corp.com")], foundation_owner=False)
    assert result["ai_posture"] == ("AD_HOC", "missing")
    assert result["ai_posture_evidence"][1] == "missing"


def test_ai_posture_measured_zero_when_governance_envelope_fetched_empty():
    """Fetch success + no evidence -> measured zero: a governance envelope
    that genuinely found no policy files is a real AD_HOC, not a guess."""
    gov_data = _gov_data(merged_prs=[], checks=[], check_names=[],
                         files={f: None for f in gov.GOVERNANCE_FILES + gov.AI_POLICY_FILES})
    result = gov.derive_governance_fields(gov_data, None, None, foundation_owner=False)
    assert result["ai_posture"] == ("AD_HOC", "derived")
    assert result["ai_posture_evidence"][0]["rule"] == "no_evidence"


# --- the append-only gather hook --------------------------------------------------- #

GATHER_DONE = {
    "github_meta": {"stars": 1, "forks": 0, "pushed_at": "2026-01-01T00:00:00Z",
                    "archived": False, "created_at": "2020-01-01T00:00:00Z",
                    "license_spdx": "MIT", "default_branch": "main"},
    "pr_events": [],
    "pr_reviews": {},
    "contributors": [],
    # Per-label gfi collector metadata-first record marks it done:
    "gfi": [{"gfi_newcomer_v2": True, "labels_matched": [], "queries": [],
             "truncated": False}],
    "actions_runs": [],
    "tree": {"paths": ["pyproject.toml"], "truncated": False},
    "commits_180d": [],
    "depsdev": None,
    "scorecard": None,
    "repo_object": {"archived": False, "mirror_url": None, "fork": False,
                    "license_spdx_id": "MIT", "pull_requests_total": 12,
                    "tree_paths_le2": ["pyproject.toml"],
                    "tree_truncated": False, "offplatform_paths": []},
    "commits_365d": [],
    "dependents": {"ecosystem": "pypi", "package": "widget", "system": "pypi",
                   "version": "1.0", "dependent_count": 1,
                   "direct_dependent_count": 1, "indirect_dependent_count": 0,
                   "deduped_count": 1, "dependents": None,
                   "source_used": "deps.dev", "guess_linked": True},
    "pr_window": {"count": 0, "truncated": False, "cost_points": 0,
                  "prs": [], "derived": []},
    # Marking other collectors done avoids taking live calls when gather_repo runs:
    "releases": {}, "issues_counts": {}, "runs": {},
    "author_history": {},
}


def write_env(raw: Path, collector: str, key: str, data,
              at: str = "2026-09-11T00:00:00Z") -> None:
    write_envelope(raw / collector / f"{key}.json", source="test", data=data,
                   fetched_at=at)


def seed_gather_done(raw: Path) -> None:
    for collector, data in GATHER_DONE.items():
        write_env(raw, collector, KEY, data)
    write_env(raw, "downloads", "pypi__widget", {"monthly": 10})


def test_gather_repo_governance_status_ok(tmp_path, monkeypatch, block_live_network):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)
    monkeypatch.setattr(gov, "collect_governance", lambda *a, **k: "ok")
    status = gather.gather_repo(OWNER, NAME, "pypi")
    assert status["governance"] == "ok"
    assert status["github_meta"] == "skip"
    assert status["pr_window"] == "skip"


def test_gather_repo_governance_status_skip(tmp_path, monkeypatch, block_live_network):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)
    write_env(gather.DATA, "governance", KEY,
              {"pulls_available": True, "merged_prs": [],
               "checks": [], "check_names": [], "files": {}})

    def must_not_fetch(*a, **k):
        raise AssertionError("collector must be skipped when the envelope exists")

    monkeypatch.setattr(gov, "collect_governance", must_not_fetch)
    status = gather.gather_repo(OWNER, NAME, "pypi")
    assert status["governance"] == "skip"


def test_gather_repo_governance_status_fail(tmp_path, monkeypatch, block_live_network):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)

    def raise_transport_error(*a, **k):
        raise RuntimeError("gh api rc=1: 404 Not Found")

    monkeypatch.setattr(gov, "collect_governance", raise_transport_error)
    status = gather.gather_repo(OWNER, NAME, "pypi")
    assert status["governance"] == "fail"


# =========================================================================== #
# D-COL-12: the check-evidence probe is capped, and a thin sample cannot
# support the negative finding "this project has no CLA".
# =========================================================================== #

def _check_evidence_calls(fake: "FakeApi") -> list[str]:
    """The probe's own REST calls, i.e. everything but the pulls listing."""
    return [path for path, _ in fake.calls if "/commits/" in path]


def _twenty_distinct_sha_prs() -> list[dict]:
    """20 merged PRs with 20 distinct head SHAs, newest merge LAST in the list
    so a correct implementation cannot pass by taking the first five."""
    return [
        _pr(i, f"2026-08-{i:02d}T00:00:00Z", f"2026-09-{i:02d}T00:00:00Z", head=f"sha{i:02d}")
        for i in range(1, 21)
    ]


def test_check_evidence_probe_is_capped(tmp_path):
    """D-COL-12: at most CHECK_EVIDENCE_SHA_CAP distinct SHAs are probed, so
    the collector costs 2*cap calls rather than 2 per merged PR."""
    prs = _twenty_distinct_sha_prs()
    recorded = {f"sha{i:02d}": [_status("ci/build")] for i in range(1, 21)}
    fake = FakeApi([prs, []], statuses_by_sha=recorded, checkruns_by_sha={})
    assert gov.collect_governance(tmp_path / "raw", OWNER, NAME, now=NOW,
                                  api=fake, fetch_file=lambda p: None) == "ok"
    calls = _check_evidence_calls(fake)
    assert len(calls) <= 2 * gov.CHECK_EVIDENCE_SHA_CAP, (
        f"probe made {len(calls)} calls for 20 distinct SHAs; the cap is "
        f"{gov.CHECK_EVIDENCE_SHA_CAP} SHAs = {2 * gov.CHECK_EVIDENCE_SHA_CAP} calls"
    )


def test_check_evidence_probes_the_newest_merges(tmp_path):
    """Newest by merged_at, not by creation order or list order: the pulls
    listing is sorted by CREATION date, and a PR opened earlier can merge later."""
    prs = _twenty_distinct_sha_prs()  # merged_at ascending, so the newest are last
    recorded = {f"sha{i:02d}": [_status("ci/build")] for i in range(1, 21)}
    fake = FakeApi([prs, []], statuses_by_sha=recorded, checkruns_by_sha={})
    gov.collect_governance(tmp_path / "raw", OWNER, NAME, now=NOW,
                           api=fake, fetch_file=lambda p: None)
    probed = {path.split("/commits/")[1].split("/")[0] for path in _check_evidence_calls(fake)}
    newest = {f"sha{i:02d}" for i in range(21 - gov.CHECK_EVIDENCE_SHA_CAP, 21)}
    assert probed == newest, f"probed {sorted(probed)}, expected the newest merges {sorted(newest)}"


def test_check_evidence_collapses_duplicate_shas(tmp_path):
    """Twenty PRs sharing three head SHAs probe three, not the cap."""
    prs = [_pr(i, f"2026-08-{i:02d}T00:00:00Z", f"2026-09-{i:02d}T00:00:00Z",
               head=f"dup{i % 3}") for i in range(1, 21)]
    recorded = {f"dup{i}": [_status("ci/build")] for i in range(3)}
    fake = FakeApi([prs, []], statuses_by_sha=recorded, checkruns_by_sha={})
    gov.collect_governance(tmp_path / "raw", OWNER, NAME, now=NOW,
                           api=fake, fetch_file=lambda p: None)
    probed = {path.split("/commits/")[1].split("/")[0] for path in _check_evidence_calls(fake)}
    assert probed == {"dup0", "dup1", "dup2"}
    data = read_envelope(tmp_path / "raw" / "governance" / f"{KEY}.json").data
    assert data["checks_shas_probed"] == 3
    assert data["checks_shas_available"] == 3


def test_envelope_records_what_the_evidence_rests_on(tmp_path):
    """A reader must be able to tell "5 of 5" from "5 of 20"."""
    prs = _twenty_distinct_sha_prs()
    recorded = {f"sha{i:02d}": [_status("ci/build")] for i in range(1, 21)}
    fake = FakeApi([prs, []], statuses_by_sha=recorded, checkruns_by_sha={})
    gov.collect_governance(tmp_path / "raw", OWNER, NAME, now=NOW,
                           api=fake, fetch_file=lambda p: None)
    data = read_envelope(tmp_path / "raw" / "governance" / f"{KEY}.json").data
    assert data["check_sha_cap"] == gov.CHECK_EVIDENCE_SHA_CAP
    assert data["checks_shas_probed"] == gov.CHECK_EVIDENCE_SHA_CAP
    assert data["checks_shas_available"] == 20


def _gov_with_probe(probed: int, available: int, check_names: list[str]) -> dict:
    """A governance envelope body carrying only what row 3.1 reads."""
    return {"files": {}, "check_names": check_names,
            "checks_shas_probed": probed, "checks_shas_available": available}


def test_enough_probes_and_no_cla_is_a_measurement(tmp_path):
    """Evidence of absence: the cap's worth of SHAs carrying no CLA check
    measures NONE. It is not a gap -- we looked, and there was nothing."""
    fields = gov.derive_governance_fields(
        _gov_with_probe(gov.CHECK_EVIDENCE_SHA_CAP, 20, ["ci/build", "lint"]), None, None, False)
    value, confidence = fields["legal_ip"]
    assert value == "NONE"
    assert confidence == "derived", "a probed-and-empty sample is a measurement, not a gap"


def test_too_few_probes_and_no_cla_is_an_evidence_gap(tmp_path):
    """Absence of evidence: below CHECK_EVIDENCE_MIN_SHAS the negative finding
    is not supported, so 3.1 is Unmeasured rather than a measured NONE."""
    probed = gov.CHECK_EVIDENCE_MIN_SHAS - 1
    fields = gov.derive_governance_fields(
        _gov_with_probe(probed, 1, ["ci/build"]), None, None, False)
    value, confidence = fields["legal_ip"]
    assert confidence == "missing", (
        f"{probed} probed SHAs cannot support 'no CLA'; expected an evidence gap, got {value!r}"
    )
    assert str(probed) in str(fields["legal_ip_evidence"][0]["reason"])


def test_a_positive_cla_signal_stands_on_one_probe(tmp_path):
    """A positive finding needs no minimum: seeing an EasyCLA context proves a
    CLA exists however few commits were examined."""
    fields = gov.derive_governance_fields(
        _gov_with_probe(1, 1, ["EasyCLA"]), None, None, False)
    value, confidence = fields["legal_ip"]
    assert confidence == "derived"
    assert value != "NONE", "a CLA check is evidence regardless of sample size"


def test_an_envelope_without_the_probe_count_keeps_the_old_behaviour(tmp_path):
    """A governance envelope written before D-COL-12 records no count; that is
    'not recorded', not 'too few', so it must not be retroactively gapped."""
    fields = gov.derive_governance_fields({"files": {}, "check_names": []}, None, None, False)
    value, confidence = fields["legal_ip"]
    assert (value, confidence) == ("NONE", "derived")


# =========================================================================== #
# GraphQL governance probe: the same sample and the same check evidence as REST
# =========================================================================== #


def _sample_repo() -> list[dict]:
    """30 closed PRs, created i=1..30. Merge order differs from creation order and the
    UPDATED order differs from both, so a different sort picks different PRs. The 8
    newest-created are closed unmerged; the 22 older ones merged."""
    prs = []
    for i in range(1, 31):
        merged = i <= 22
        prs.append({
            "number": i,
            "created_at": f"2026-08-{i:02d}T00:00:00Z",
            # PR 3 was created early but merged last; PR 20 was commented on last
            "merged_at": (f"2026-09-{(30 - i) % 28 + 1:02d}T00:00:00Z" if merged else None),
            "updated_at": f"2026-10-{(i * 7) % 28 + 1:02d}T00:00:00Z",
            "author": f"user{i % 3}",
            "head_sha": f"{i:040x}",
        })
    return prs


class FakeGitHubRepo:
    """One repository answering BOTH the REST pulls/statuses/check-runs endpoints and the
    GraphQL governance queries, from the same fixture. The GraphQL side honours the
    ``states`` and ``orderBy`` it finds in the query text, so a changed query changes the
    sample."""

    def __init__(self, prs: list[dict], statuses: dict[str, list[dict]],
                 runs: dict[str, list[dict]], runs_per_suite: int = 50) -> None:
        self.prs = prs
        self.statuses = statuses
        self.runs = runs
        self.runs_per_suite = runs_per_suite
        self.gql_calls: list[tuple[str, dict]] = []
        self.rest_calls: list[str] = []

    # ---- REST ---- #
    def call(self, path: str, params: Mapping[str, str]) -> Any:
        self.rest_calls.append(path)
        if path.endswith("/pulls"):
            assert params["state"] == "closed" and params["sort"] == "created"
            rows = sorted(self.prs, key=lambda p: p["created_at"], reverse=params["direction"] == "desc")
            size, page = int(params["per_page"]), int(params["page"])
            return [{"number": p["number"], "created_at": p["created_at"], "merged_at": p["merged_at"],
                     "user": {"login": p["author"]}, "head": {"sha": p["head_sha"]}}
                    for p in rows[(page - 1) * size: page * size]]
        sha = path.split("/commits/", 1)[1].split("/", 1)[0]
        if path.endswith("/statuses"):
            return [dict(x) for x in self.statuses.get(sha, [])]
        runs = self.runs.get(sha, [])
        return {"total_count": len(runs), "check_runs": [dict(x) for x in runs[:100]]}

    # ---- GraphQL ---- #
    def execute(self, query: str, variables: Optional[Mapping[str, Any]] = None) -> dict:
        v = dict(variables or {})
        self.gql_calls.append((query, v))
        if "GovernanceMergedPrs" in query:
            states = re.search(r"states: \[([A-Z, ]+)\]", query).group(1)
            field, direction = re.search(r"orderBy: \{field: (\w+), direction: (\w+)\}", query).groups()
            assert "CLOSED" in states and "MERGED" in states
            key = {"CREATED_AT": "created_at", "UPDATED_AT": "updated_at"}[field]
            rows = sorted(self.prs, key=lambda p: p[key], reverse=direction == "DESC")
            start = int(v.get("after") or 0)
            page = rows[start: start + v["first"]]
            end = start + len(page)
            return {"data": {"rateLimit": {"cost": 1, "remaining": 4000}, "r": {"pullRequests": {
                "pageInfo": {"hasNextPage": end < len(rows), "endCursor": str(end)},
                "nodes": [{"number": p["number"], "createdAt": p["created_at"],
                           "mergedAt": p["merged_at"], "author": {"login": p["author"]},
                           "headRefOid": p["head_sha"]} for p in page]}}}}
        assert "GovernanceChecks" in query
        repo: dict[str, Any] = {}
        i = 0
        while f"s{i}" in v:
            sha = v[f"s{i}"]
            known = any(p["head_sha"] == sha for p in self.prs)
            if not known:
                repo[f"c{i}"] = None
            else:
                runs = self.runs.get(sha, [])
                size = self.runs_per_suite
                suites = [runs[j: j + size] for j in range(0, len(runs), size)] or []
                repo[f"c{i}"] = {
                    "status": {"contexts": [{"context": x["context"], "state": x["state"].upper()}
                                            for x in self.statuses.get(sha, [])]},
                    "checkSuites": {"pageInfo": {"hasNextPage": len(suites) > gov.GOVERNANCE_CHECK_SUITES_FIRST},
                                    "nodes": [{"checkRuns": {
                                        "pageInfo": {"hasNextPage": False},
                                        "nodes": [{"name": r["name"], "status": r["status"].upper(),
                                                   "conclusion": (r["conclusion"] or "").upper() or None}
                                                  for r in chunk]}} for chunk in suites[: gov.GOVERNANCE_CHECK_SUITES_FIRST]]},
                }
            i += 1
        return {"data": {"rateLimit": {"cost": 1, "remaining": 4000}, "r": repo}}


def _probed_shas(prs: list[dict]) -> list[str]:
    """The SHAs the collectors probe: the 20 newest-created merged PRs, then the
    CHECK_EVIDENCE_SHA_CAP newest merges among them."""
    sample = [p for p in sorted(prs, key=lambda p: p["created_at"], reverse=True)
              if p["merged_at"]][: gov.MERGED_PR_CAP]
    rows = [{"merged_at": p["merged_at"], "number": p["number"], "head_sha": p["head_sha"]}
            for p in sample]
    return [sha for sha, _ in gov._distinct_head_shas(rows)][: gov.CHECK_EVIDENCE_SHA_CAP]


def _sample_fake(**kw: Any) -> FakeGitHubRepo:
    prs = _sample_repo()
    probed = _probed_shas(prs)
    assert len(probed) == 5
    statuses = {sha: [_status("EasyCLA"), _status("tide", "pending")] for sha in probed[:3]}
    runs = {sha: [_checkrun("cla/google"), _checkrun("build", "failure")] for sha in probed[:4]}
    return FakeGitHubRepo(prs, statuses, runs, **kw)


def _collect(tmp_path, fake, *, graphql: bool, tag: str):
    raw = tmp_path / tag
    kwargs: dict[str, Any] = {"graphql_transport": fake} if graphql else {"api": fake}
    assert gov.collect_governance(raw, OWNER, NAME, now=NOW, fetch_file=lambda p: None,
                                  **kwargs, **({"api": fake} if graphql else {})) == "ok"
    return read_envelope(raw / "governance" / f"{KEY}.json")


def test_governance_graphql_probes_the_same_prs_shas_and_checks_as_rest(tmp_path):
    fake = _sample_fake()
    rest = _collect(tmp_path, fake, graphql=False, tag="rest")
    gql = _collect(tmp_path, fake, graphql=True, tag="gql")
    assert gql.source == "github-graphql:governance"
    for field in ("pulls_available", "merged_prs", "checks_shas_probed", "checks_shas_available",
                  "check_names", "merged_pr_cap", "check_sha_cap"):
        assert gql.data[field] == rest.data[field], field
    # same rows, in the same order, kind/state/conclusion included
    assert gql.data["checks"] == rest.data["checks"]
    assert [p["number"] for p in gql.data["merged_prs"]] == list(range(22, 2, -1))
    assert gql.data["check_names"] == ["EasyCLA", "build", "cla/google", "tide"]


def test_governance_graphql_sample_is_ordered_by_creation_not_by_update(tmp_path):
    fake = _sample_fake()
    gql = _collect(tmp_path, fake, graphql=True, tag="gql")
    pr_query = next(q for q, _ in fake.gql_calls if "GovernanceMergedPrs" in q)
    assert "field: CREATED_AT, direction: DESC" in pr_query
    assert "UPDATED_AT" not in pr_query
    updated_order = [p["number"] for p in sorted(fake.prs, key=lambda p: p["updated_at"], reverse=True)
                     if p["merged_at"]][: gov.MERGED_PR_CAP]
    assert [p["number"] for p in gql.data["merged_prs"]] != updated_order


def test_governance_graphql_lists_every_check_run_across_suites_without_a_rollup(tmp_path):
    """100 check runs spread over several suites: all of them are listed by GraphQL (a
    ``statusCheckRollup`` keeps 100 contexts of any kind and drops runs REST lists), with
    no REST call, and the rows equal the REST path's."""
    newest = _probed_shas(_sample_repo())[0]
    runs = {newest: [_checkrun(f"job-{i}") for i in range(99)] + [_checkrun("cla/google")]}
    fake = FakeGitHubRepo(_sample_repo(), {}, runs, runs_per_suite=40)
    gql = _collect(tmp_path, fake, graphql=True, tag="gql")
    rest = _collect(tmp_path, FakeGitHubRepo(fake.prs, {}, runs), graphql=False, tag="rest")
    assert "cla/google" in gql.data["check_names"]
    assert sum(1 for c in gql.data["checks"] if c["kind"] == "check_run") == 100
    assert gql.data["checks"] == rest.data["checks"]
    assert not any(path.endswith("/check-runs") for path in fake.rest_calls)
    assert "statusCheckRollup" not in "".join(q for q, _ in fake.gql_calls)


def test_governance_graphql_defers_to_rest_for_a_commit_rest_would_truncate(tmp_path):
    """REST reads 100 check runs per commit and no more. For a commit with more, GraphQL
    would return extra names the REST path never sees; the REST path is used instead so
    the two stay identical."""
    newest = _probed_shas(_sample_repo())[0]
    runs = {newest: [_checkrun(f"job-{i}") for i in range(150)] + [_checkrun("cla/google")]}
    fake = FakeGitHubRepo(_sample_repo(), {}, runs, runs_per_suite=40)
    gql = _collect(tmp_path, fake, graphql=True, tag="gql")
    rest = _collect(tmp_path, FakeGitHubRepo(fake.prs, {}, runs), graphql=False, tag="rest")
    assert gql.data["checks"] == rest.data["checks"]
    assert "cla/google" not in gql.data["check_names"]  # past REST's 100: not read by either path
    assert any(path.endswith(f"/commits/{newest}/check-runs") for path in fake.rest_calls)


def test_governance_graphql_falls_back_to_rest_for_a_sha_it_cannot_list_in_full(tmp_path):
    big = _probed_shas(_sample_repo())[0]
    n_runs = gov.GOVERNANCE_CHECK_SUITES_FIRST + 5  # one run per suite: more suites than one page
    fake = FakeGitHubRepo(_sample_repo(), {}, {big: [_checkrun(f"job-{i}") for i in range(n_runs)]},
                          runs_per_suite=1)
    gql = _collect(tmp_path, fake, graphql=True, tag="gql")
    assert any(path.endswith(f"/commits/{big}/check-runs") for path in fake.rest_calls)
    rest = _collect(tmp_path, FakeGitHubRepo(fake.prs, {}, fake.runs), graphql=False, tag="rest")
    assert gql.data["checks"] == rest.data["checks"]


def test_governance_graphql_unknown_sha_is_skipped_like_a_rest_404(tmp_path):
    fake = _sample_fake()
    fake.statuses.clear()
    fake.runs.clear()
    real_execute = fake.execute

    def hide_newest(query, variables=None):
        v = dict(variables or {})
        if "GovernanceChecks" in query:
            v["s0"] = "f" * 40  # no such commit
        return real_execute(query, v)

    fake.execute = hide_newest  # type: ignore[method-assign]
    gql = _collect(tmp_path, fake, graphql=True, tag="gql")
    assert gql.data["checks_shas_probed"] == gov.CHECK_EVIDENCE_SHA_CAP - 1
    assert gql.data["checks_shas_available"] == 20


def test_governance_graphql_missing_repo_is_pulls_unavailable(tmp_path):
    class Gone:
        def execute(self, query, variables=None):
            return {"data": {"r": None}, "errors": [{"type": "NOT_FOUND", "path": ["r"]}]}

    env = _collect(tmp_path, Gone(), graphql=True, tag="gql")
    assert env.data["pulls_available"] is False and env.data["merged_prs"] == []


def test_governance_graphql_defers_to_rest_for_a_commit_with_100_status_contexts(tmp_path):
    newest = _probed_shas(_sample_repo())[0]
    statuses = {newest: [_status(f"ctx-{i}") for i in range(gov.REST_CHECKS_PER_PAGE)]}
    fake = FakeGitHubRepo(_sample_repo(), statuses, {})
    gql = _collect(tmp_path, fake, graphql=True, tag="gql")
    rest = _collect(tmp_path, FakeGitHubRepo(fake.prs, statuses, {}), graphql=False, tag="rest")
    assert gql.data["checks"] == rest.data["checks"]
    assert any(path.endswith(f"/commits/{newest}/statuses") for path in fake.rest_calls)


def test_collect_governance_skips_fetch_for_files_absent_from_non_truncated_tree(tmp_path):
    raw_dir = tmp_path / "raw"
    tree_dir = raw_dir / "tree"
    tree_dir.mkdir(parents=True, exist_ok=True)
    # Tree contains only CONTRIBUTING.md and CLAUDE.md
    write_envelope(
        tree_dir / f"{KEY}.json",
        source="test-tree",
        data={"paths": ["README.md", "CONTRIBUTING.md", "CLAUDE.md"], "truncated": False},
        fetched_at="2026-09-12T00:00:00Z",
    )

    fetched_files: list[str] = []

    def fetch_file(path: str) -> Optional[str]:
        fetched_files.append(path)
        return f"# Content of {path}\n"

    fake = FakeApi([[]])
    res = gov.collect_governance(raw_dir, OWNER, NAME, now=NOW, api=fake, fetch_file=fetch_file)
    assert res == "ok"

    # Only files present in the tree should be fetched
    assert set(fetched_files) == {"CONTRIBUTING.md", "CLAUDE.md"}
    d = read_envelope(raw_dir / "governance" / f"{KEY}.json").data
    assert d["files"]["CONTRIBUTING.md"]["present"] is True
    assert d["files"]["CLAUDE.md"]["present"] is True
    assert d["files"]["GOVERNANCE.md"] is None
    assert d["files"]["SECURITY.md"] is None


def test_collect_governance_falls_back_when_tree_truncated_or_missing(tmp_path):
    raw_dir = tmp_path / "raw"
    fetched_files: list[str] = []

    def fetch_file(path: str) -> Optional[str]:
        fetched_files.append(path)
        return None

    fake = FakeApi([[]])

    # 1. Missing tree envelope -> fetches all POLICY_FILES
    gov.collect_governance(raw_dir, OWNER, NAME, now=NOW, api=fake, fetch_file=fetch_file, force=True)
    assert len(fetched_files) == len(gov.POLICY_FILES)
    assert set(fetched_files) == set(gov.POLICY_FILES)

    # 2. Truncated tree envelope -> falls back and fetches all POLICY_FILES
    fetched_files.clear()
    tree_dir = raw_dir / "tree"
    tree_dir.mkdir(parents=True, exist_ok=True)
    write_envelope(
        tree_dir / f"{KEY}.json",
        source="test-tree",
        data={"paths": ["README.md"], "truncated": True},
        fetched_at="2026-09-12T00:00:00Z",
    )
    gov.collect_governance(raw_dir, OWNER, NAME, now=NOW, api=fake, fetch_file=fetch_file, force=True)
    assert len(fetched_files) == len(gov.POLICY_FILES)
    assert set(fetched_files) == set(gov.POLICY_FILES)


def test_promotion_closed_holds_when_commit_authors_have_no_merged_pr_in_the_window():
    """Over-correction guard (#51 review): commits_365d spans 365 days and direct pushes,
    the PR window 180 days, so a commit author with no merged PR in the window is ordinary.
    It must not lift a closed single-org repo out of CLOSED into DOCUMENTED."""
    prw = _prw([
        _raw_pr(1, "2026-06-01T00:00:00Z", "2026-06-02T00:00:00Z", "bob", "MEMBER"),
        _raw_pr(2, "2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z", "carol", "COLLABORATOR"),
    ])
    c365 = _corp365(("bob", "bob@acme-corp.com"), ("carol", "carol@acme-corp.com"),
                    ("dave", "dave@outside.org"))
    files = {"GOVERNANCE.md": {"present": True, "sha256": "h",
                               "body": "Contributors can become a maintainer through nomination.\n"}}
    value, ev = gov.classify_promotion_ladder(prw, files, c365)
    assert value == "CLOSED"
    assert ev["rule"] == "closed_single_org"
    assert "pr_window_cut_short" not in ev


def test_promotion_on_a_cut_pr_window_refuses_every_rung():
    """#51 rule 5 + RFC-0016: on a record cut short of its 180d window, every rung
    (VERIFIED, CLOSED, DOCUMENTED, AD_HOC) rests on an absence the unrecorded part
    could refute, so all are refused when cut and measured when uncut."""
    def cut(prs: list[dict]) -> dict:
        return {"window_anchor": "2026-09-12T00:00:00Z", "window_days": 180, "truncated": True,
                "prs": prs, "derived": []}

    closed_prs = [_raw_pr(1, "2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z", "bob", "MEMBER"),
                  _raw_pr(2, "2026-08-10T00:00:00Z", "2026-08-11T00:00:00Z", "carol", "COLLABORATOR")]
    c365 = _corp365(("bob", "bob@acme-corp.com"), ("carol", "carol@acme-corp.com"))
    value, ev = gov.classify_promotion_ladder(cut(closed_prs), {}, c365)
    assert value == "AD_HOC"  # CLOSED is never claimed from a cut record
    assert ev["pr_window_cut_short"] is True
    docs = _gov_data(merged_prs=[], checks=[], check_names=[], files={
        "GOVERNANCE.md": {"present": True, "sha256": "h",
                          "body": "Contributors may become a maintainer by nomination.\n"}})
    for gov_data in (None, docs):  # AD_HOC, then DOCUMENTED: neither is measured
        fields = gov.derive_governance_fields(gov_data, cut(closed_prs), c365)
        assert fields["promotion_ladder"][1] == "missing"
        assert fields["promotion_ladder_evidence"][0]["reason"] == (
            "pr_window truncated before reaching 180d window start")
    promoted = [_raw_pr(3, "2026-06-01T00:00:00Z", "2026-06-02T00:00:00Z",
                        "alice", "MEMBER", merged_by="maint"),
                _raw_pr(4, "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z",
                        "other", "NONE", merged_by="alice")]
    # Cut window: VERIFIED is also refused because an earlier maintainer action
    # in the unrecorded half of the 180d window would refute promotion.
    assert gov.classify_promotion_ladder(cut(promoted), {}, c365)[0] == "AD_HOC"
    cut_promoted_fields = gov.derive_governance_fields(docs, cut(promoted), c365)
    assert cut_promoted_fields["promotion_ladder"] == ("DOCUMENTED", "missing")
    assert cut_promoted_fields["promotion_ladder_evidence"][0]["reason"] == (
        "pr_window truncated before reaching 180d window start")
    # Uncut window: VERIFIED and CLOSED are both measured.
    uncut_promoted = {**cut(promoted), "truncated": False}
    assert gov.derive_governance_fields(docs, uncut_promoted, c365)["promotion_ladder"] == (
        "VERIFIED", "derived")
    uncut_closed = {**cut(closed_prs), "truncated": False}
    assert gov.derive_governance_fields(None, uncut_closed, c365)["promotion_ladder"] == (
        "CLOSED", "derived")


# =========================================================================== #
# RFC-0016 (F2a): Action-based promotion_ladder VERIFIED unit tests
# =========================================================================== #

def test_promotion_phase_floor_30_days_boundary(monkeypatch):
    """PROMOTION_MIN_OUTSIDE_DAYS (30d) separates short-gap maintainer rotation
    from genuine outsider-to-maintainer promotion."""
    # 29 days between outside merge (2026-06-01) and maintainer action (2026-06-30): rejected
    prs_29d = [
        _raw_pr(1, "2026-05-30T00:00:00Z", "2026-06-01T00:00:00Z",
                "alice", "MEMBER", merged_by="maint"),
        _raw_pr(2, "2026-06-29T00:00:00Z", "2026-06-30T00:00:00Z",
                "other", "NONE", merged_by="alice"),
    ]
    assert gov.classify_promotion_ladder(_prw(prs_29d), {}, [])[0] == "AD_HOC"

    # Exactly 30 days (2026-06-01 -> 2026-07-01): accepted
    prs_30d = [
        _raw_pr(1, "2026-05-30T00:00:00Z", "2026-06-01T00:00:00Z",
                "alice", "MEMBER", merged_by="maint"),
        _raw_pr(2, "2026-06-30T00:00:00Z", "2026-07-01T00:00:00Z",
                "other", "NONE", merged_by="alice"),
    ]
    val, ev = gov.classify_promotion_ladder(_prw(prs_30d), {}, [])
    assert val == "VERIFIED"
    assert ev["promoted_accounts"][0]["outside_days"] == 30.0

    # Raising PROMOTION_MIN_OUTSIDE_DAYS to 45 rejects the 30-day fixture
    monkeypatch.setattr(gov, "PROMOTION_MIN_OUTSIDE_DAYS", 45)
    assert gov.classify_promotion_ladder(_prw(prs_30d), {}, [])[0] == "AD_HOC"


def test_promotion_account_age_floor_d_in_3_90_days():
    """D-IN-3: outside PR author's GitHub account must be >= MIN_ACCOUNT_AGE_DAYS
    (90d) old at pr.createdAt."""
    # Account created 89 days before PR #1 createdAt (2026-03-03 -> 2026-05-31 is 89d)
    prs_young = [
        _raw_pr(1, "2026-05-31T00:00:00Z", "2026-06-01T00:00:00Z",
                "alice", "MEMBER", merged_by="maint",
                author_created="2026-03-03T00:00:00Z"),
        _raw_pr(2, "2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z",
                "other", "NONE", merged_by="alice"),
    ]
    assert gov.classify_promotion_ladder(_prw(prs_young), {}, [])[0] == "AD_HOC"

    # Account created exactly 90 days before PR #1 createdAt (2026-03-02 -> 2026-05-31 is 90d)
    prs_old_enough = [
        _raw_pr(1, "2026-05-31T00:00:00Z", "2026-06-01T00:00:00Z",
                "alice", "MEMBER", merged_by="maint",
                author_created="2026-03-02T00:00:00Z"),
        _raw_pr(2, "2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z",
                "other", "NONE", merged_by="alice"),
    ]
    assert gov.classify_promotion_ladder(_prw(prs_old_enough), {}, [])[0] == "VERIFIED"


def test_promotion_self_merge_before_outside_merge_disqualifies_existing_maintainer():
    """A self-merge proves write access at that timestamp, so a later PR merged by
    a teammate does not make the account an outside contributor."""
    prs = [
        # alice self-merges PR #1 on 2026-04-01 -> write access already held
        _raw_pr(1, "2026-04-01T00:00:00Z", "2026-04-02T00:00:00Z",
                "alice", "MEMBER", merged_by="alice"),
        # teammate merges alice's PR #2 on 2026-05-01
        _raw_pr(2, "2026-05-01T00:00:00Z", "2026-05-02T00:00:00Z",
                "alice", "MEMBER", merged_by="maint"),
        # alice merges other's PR #3 on 2026-08-01
        _raw_pr(3, "2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z",
                "other", "NONE", merged_by="alice"),
    ]
    val, ev = gov.classify_promotion_ladder(_prw(prs), {}, [])
    assert val == "AD_HOC"
    assert ev["promoted_accounts"] == []


def test_promotion_closed_event_by_non_author_counts_as_maintainer_action():
    """Closing another author's PR (D-IN-2) is a maintainer triage action; closing
    one's own PR is not."""
    # Closing own PR does not count as maintainer action
    prs_self_close = [
        _raw_pr(1, "2026-04-01T00:00:00Z", "2026-04-02T00:00:00Z",
                "alice", "NONE", merged_by="maint"),
        _raw_pr(2, "2026-07-01T00:00:00Z", None,
                "alice", "NONE",
                closed_events=[{"createdAt": "2026-07-02T00:00:00Z",
                                "actor": {"__typename": "User", "login": "alice"}}]),
    ]
    assert gov.classify_promotion_ladder(_prw(prs_self_close), {}, [])[0] == "AD_HOC"

    # Closing another author's PR >= 30d after outside merge counts as VERIFIED
    prs_other_close = [
        _raw_pr(1, "2026-04-01T00:00:00Z", "2026-04-02T00:00:00Z",
                "alice", "COLLABORATOR", merged_by="maint"),
        _raw_pr(2, "2026-07-01T00:00:00Z", None,
                "other", "NONE",
                closed_events=[{"createdAt": "2026-07-02T00:00:00Z",
                                "actor": {"__typename": "User", "login": "alice"}}]),
    ]
    val, ev = gov.classify_promotion_ladder(_prw(prs_other_close), {}, [])
    assert val == "VERIFIED"
    assert ev["promoted_accounts"][0]["first_maintainer_action"] == "close"
    assert ev["promoted_accounts"][0]["first_maintainer_pr"] == 2


# =========================================================================== #
# RFC-0017 (F5a): Legal IP precedence & FOUNDATION_ORGS unit tests
# =========================================================================== #

def test_legal_ip_foundation_owner_with_generic_cla_check_derives_foundation_cla():
    """RFC-0017: a generic CLA check (license/cla, cla-signed, cla-assistant) on a
    FOUNDATION_ORGS owner derives FOUNDATION_CLA via foundation_owner_cla_check."""
    for check_name in ("license/cla", "cla-signed", "cla-assistant", "CLA"):
        value, ev = gov.classify_legal_ip(
            [check_name, "ci"],
            {"count": 55, "total": 60, "share": 0.9167},
            foundation_owner=True,
        )
        assert value == "FOUNDATION_CLA", check_name
        assert ev["rule"] == "foundation_owner_cla_check", check_name


def test_legal_ip_dco_evidence_beats_foundation_owner_without_cla_check():
    """RFC-0017: when a FOUNDATION_ORGS repo has no CLA check on recent PRs,
    measured DCO evidence (dco_check or signoff_share >= 0.5) wins over the
    blanket foundation_owner fallback (e.g. apache/hbase, apache/beam,
    apache/zeppelin, apache/arrow)."""
    val_check, ev_check = gov.classify_legal_ip(
        ["DCO", "ci"], {"count": 10, "total": 60, "share": 0.1667},
        foundation_owner=True,
    )
    assert val_check == "DCO"
    assert ev_check["rule"] == "dco_check"

    val_share, ev_share = gov.classify_legal_ip(
        ["ci"], {"count": 58, "total": 60, "share": 0.9667},
        foundation_owner=True,
    )
    assert val_share == "DCO"
    assert ev_share["rule"] == "signoff_share"

    # Without CLA check and with signoff share < 0.5, foundation_owner fallback fires
    val_owner, ev_owner = gov.classify_legal_ip(
        ["ci"], {"count": 2, "total": 60, "share": 0.0333},
        foundation_owner=True,
    )
    assert val_owner == "FOUNDATION_CLA"
    assert ev_owner["rule"] == "foundation_owner"


def test_legal_ip_eclipsefdn_eca_is_foundation_cla_check():
    """Eclipse Foundation's eclipsefdn/eca status context is recognised as a
    foundation CLA check regardless of owner."""
    value, ev = gov.classify_legal_ip(["eclipsefdn/eca", "ci"], None, foundation_owner=False)
    assert value == "FOUNDATION_CLA"
    assert ev["rule"] == "foundation_cla_check"


def test_derive_repo_foundation_orgs_and_no_foundation_suffix_heuristic(tmp_path):
    """RFC-0017: SeleniumHQ and trinodb are in FOUNDATION_ORGS, while arbitrary
    '*-foundation' owners (e.g. x402-foundation) do not trigger foundation_owner
    in v2 legal_ip."""
    from porchlamp.core.constants import FOUNDATION_ORGS
    assert "seleniumhq" in FOUNDATION_ORGS
    assert "trinodb" in FOUNDATION_ORGS
    assert "x402-foundation" not in FOUNDATION_ORGS

    raw = tmp_path / "raw"
    empty_files = {f: None for f in gov.GOVERNANCE_FILES + gov.AI_POLICY_FILES}
    # x402-foundation/x402 with 5 probed SHAs, no CLA check, 0 signoff -> NONE (not FOUNDATION_CLA)
    write_envelope(
        raw / "governance" / "x402-foundation__x402.json",
        source="test",
        data={
            **_gov_data(merged_prs=[], checks=[], check_names=["ci"], files=empty_files),
            "checks_shas_probed": 5,
            "checks_shas_available": 5,
        },
        fetched_at="2026-09-11T00:00:00Z",
    )
    bundle = derive.derive_repo("x402-foundation", "x402", derive.Ecosystem.NPM,
                                tmp_path, fetch=False, now=NOW)
    assert bundle["meta"]["legal_ip"] == "NONE"
    assert bundle["meta"]["legal_ip_evidence"]["rule"] == "no_evidence"
    assert bundle["meta"]["legal_ip_evidence"]["foundation_owner"] is False


# =========================================================================== #
# C5b: Governance file path resolution (.github > root > docs, extensions, org)
# =========================================================================== #

def test_resolve_governance_path_directory_and_extension_precedence():
    """C5b: .github/ > root > docs/, and .md > .rst > .txt > extensionless within
    each directory, matched case-insensitively."""
    # .github beats root and docs even when .github has .rst and root has .md
    assert gov.resolve_governance_path(
        "CONTRIBUTING.md",
        ["docs/CONTRIBUTING.md", "CONTRIBUTING.md", ".github/contributing.rst"],
    ) == ".github/contributing.rst"

    # Root beats docs
    assert gov.resolve_governance_path(
        "GOVERNANCE.md",
        ["docs/GOVERNANCE.md", "governance.txt"],
    ) == "governance.txt"

    # Within a directory, .md > .rst > .txt > ""
    assert gov.resolve_governance_path(
        "MAINTAINERS.md",
        ["docs/MAINTAINERS", "docs/maintainers.txt", "docs/Maintainers.rst"],
    ) == "docs/Maintainers.rst"


def test_collect_governance_resolves_tree_variants_and_nomination_match(tmp_path):
    """C5b: collect_governance resolves non-root / non-.md governance files from
    the tree envelope and _nomination_match reads them."""
    raw_dir = tmp_path / "raw"
    write_envelope(
        raw_dir / "tree" / f"{KEY}.json",
        source="test-tree",
        data={
            "paths": ["README.md", ".github/governance.rst", "docs/contributing.txt"],
            "truncated": False,
        },
        fetched_at="2026-09-12T00:00:00Z",
    )
    bodies = {
        ".github/governance.rst": "Contributors can become a maintainer via TSC vote.\n",
        "docs/contributing.txt": "See governance.\n",
    }
    fetched: list[str] = []

    def fetch_file(path: str) -> Optional[str]:
        fetched.append(path)
        return bodies.get(path)

    assert gov.collect_governance(
        raw_dir, OWNER, NAME, now=NOW, api=FakeApi([[]]), fetch_file=fetch_file
    ) == "ok"
    assert set(fetched) == {".github/governance.rst", "docs/contributing.txt"}
    d = read_envelope(raw_dir / "governance" / f"{KEY}.json").data
    assert d["files"]["GOVERNANCE.md"]["path"] == ".github/governance.rst"
    assert d["files"]["CONTRIBUTING.md"]["path"] == "docs/contributing.txt"
    value, ev = gov.classify_promotion_ladder(None, d["files"], [])
    assert value == "DOCUMENTED"
    assert ev["document_match"] == {
        "file": "GOVERNANCE.md",
        "path": ".github/governance.rst",
        "line": "Contributors can become a maintainer via TSC vote.",
    }


def test_collect_governance_org_default_fallback_for_contributing_and_security_only(tmp_path):
    """C5b: <owner>/.github fallback applies to CONTRIBUTING.md and SECURITY.md
    when absent from the repo, but never to GOVERNANCE.md or MAINTAINERS.md."""
    raw_dir = tmp_path / "raw"
    write_envelope(
        raw_dir / "tree" / f"{KEY}.json",
        source="test-tree",
        data={"paths": ["README.md"], "truncated": False},
        fetched_at="2026-09-12T00:00:00Z",
    )
    org_tree = [".github/CONTRIBUTING.md", "SECURITY.rst", "GOVERNANCE.md", "MAINTAINERS.md"]
    org_bodies = {
        ".github/CONTRIBUTING.md": "You can become a maintainer by nomination.\n",
        "SECURITY.rst": "Report vulnerabilities privately.\n",
        "GOVERNANCE.md": "Org governance should not be inherited.\n",
        "MAINTAINERS.md": "Org maintainers should not be inherited.\n",
    }
    org_fetched: list[str] = []

    def fetch_org(path: str) -> Optional[str]:
        org_fetched.append(path)
        return org_bodies.get(path)

    assert gov.collect_governance(
        raw_dir,
        OWNER,
        NAME,
        now=NOW,
        api=FakeApi([[]]),
        fetch_file=lambda p: None,
        org_tree_paths=org_tree,
        fetch_org_file=fetch_org,
    ) == "ok"
    assert set(org_fetched) == {".github/CONTRIBUTING.md", "SECURITY.rst"}
    d = read_envelope(raw_dir / "governance" / f"{KEY}.json").data
    assert d["files"]["CONTRIBUTING.md"]["org_default"] is True
    assert d["files"]["CONTRIBUTING.md"]["path"] == f"{OWNER}/.github:.github/CONTRIBUTING.md"
    assert d["files"]["SECURITY.md"]["org_default"] is True
    assert d["files"]["SECURITY.md"]["path"] == f"{OWNER}/.github:SECURITY.rst"
    assert d["files"]["GOVERNANCE.md"] is None
    assert d["files"]["MAINTAINERS.md"] is None
    value, ev = gov.classify_promotion_ladder(None, d["files"], [])
    assert value == "DOCUMENTED"
    assert ev["document_match"]["file"] == "CONTRIBUTING.md"
    assert ev["document_match"]["path"] == f"{OWNER}/.github:.github/CONTRIBUTING.md"



def test_signoff_share_refuses_cut_window_and_reads_sampled_window():
    """RFC-0021: signoff_share refuses a cut commits_365d record (truncated dict
    or 1,000-row bare list) and reads an evenly spaced sample unweighted."""
    rows = [_c365_row(f"u{i}", f"u{i}@corp.com", signed_off=(i < 60)) for i in range(100)]
    assert gov.signoff_share({"rows": rows, "truncated": True, "count": 100, "cap": 1000}) is None
    assert gov.signoff_share([_c365_row("a", "a@corp.com", signed_off=True) for _ in range(1000)]) is None

    sampled = {"rows": rows, "sampled": True, "total": 12000, "truncated": False, "count": 100, "cap": 5000}
    assert gov.signoff_share(sampled) == {"count": 60, "total": 100, "share": 0.6}


def test_derive_governance_fields_cut_commits_withholds_no_evidence_but_keeps_foundation_and_cla():
    """RFC-0021: on a cut commits_365d record, the negative NONE verdict rests
    on an unmeasured Signed-off-by share and is withheld (missing), whereas a
    foundation owner or CLA check still derives."""
    from porchlamp.core import commit_domains

    cut_c365 = {"rows": [_c365_row("a", "a@corp.com", signed_off=False) for _ in range(100)],
                "truncated": True, "count": 100, "cap": 1000}
    gov_empty = _gov_with_probe(gov.CHECK_EVIDENCE_SHA_CAP, 10, ["ci/build"])
    fields_none = gov.derive_governance_fields(gov_empty, None, cut_c365, foundation_owner=False)
    assert fields_none["legal_ip"] == ("NONE", "missing")
    assert fields_none["legal_ip_evidence"][0]["reason"] == commit_domains.COMMIT_WINDOW_CUT_DETAIL

    # Foundation owner and CLA check do not depend on the sign-off share:
    fields_fnd = gov.derive_governance_fields(gov_empty, None, cut_c365, foundation_owner=True)
    assert fields_fnd["legal_ip"] == ("FOUNDATION_CLA", "derived")

    gov_cla = _gov_with_probe(1, 1, ["cla/google"])
    fields_cla = gov.derive_governance_fields(gov_cla, None, cut_c365, foundation_owner=False)
    assert fields_cla["legal_ip"] == ("CORP_CLA", "derived")


