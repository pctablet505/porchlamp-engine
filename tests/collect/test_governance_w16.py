"""Tests for W1-6: governance evidence (legal IP, promotion ladder, AI posture).

Subject: ``cvi.collect.governance`` — the governance envelope collector
(check-run names / status contexts on the 20 most recent merged PRs and the
GOVERNANCE/MAINTAINERS/CONTRIBUTING/SECURITY + AI-policy file bodies), the
``02`` §4 3.1/3.3/4.2 classifiers, the W1-6 ownership extension of
``cvi.collect.commits`` (a per-commit ``signed_off`` boolean, no message
text), and the append-only hooks at the end of ``scripts/gather.py`` (the
``governance`` status entry) and ``scripts/derive.py`` (the six ``legal_ip``
/ ``promotion_ladder`` / ``ai_posture`` value + evidence fields).
Recorded-response oracles at the REST transport boundary (no live calls)
assert the exact classification value and evidence for every path, including
the no-evidence ``NONE``/``AD_HOC``/``AD_HOC`` defaults. The Signed-off-by
share is *not* fetched by this collector: it is computed from the
already-collected ``commits_365d`` envelope (W1-5), so ``collect_governance``
never fetches commits and never stores commit message text.
"""
from __future__ import annotations

import importlib.util
import sys

import pytest
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

from cvi.collect import governance as gov
from cvi.collect.envelope import envelope_hash, read_envelope, write_envelope

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


derive = _load_script("cvi_derive_w16", "derive.py")
gather = _load_script("cvi_gather_w16", "gather.py")


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
    """One ``commits_365d`` envelope row (W1-5 + the W1-6 ``signed_off`` field)."""
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
    runs = {f"h{i:02d}": [_checkrun("cla")] for i in range(0, 20, 7)}
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
    assert "EasyCLA" in d["check_names"] and "tide" in d["check_names"] and "cla" in d["check_names"]
    assert d["check_names"] == sorted(set(d["check_names"]))
    kinds = {(c["name"], c["kind"]) for c in d["checks"]}
    assert ("EasyCLA", "status") in kinds and ("cla", "check_run") in kinds
    assert d["files"]["GOVERNANCE.md"]["body"] == "governance body\n"
    assert d["files"]["MAINTAINERS.md"] is None
    assert "commits" not in d, "no commit fetch: the sign-off share reuses commits_365d"


def test_collect_governance_per_file_sha256_matches_independent_recomputation(tmp_path):
    """W6: every one of the 12 GOVERNANCE_FILES + AI_POLICY_FILES entries
    carries a ``sha256`` computed over its own body bytes -- recomputed here
    with a plain ``hashlib.sha256`` call (never importing
    :func:`cvi.collect.envelope.sha256_hex`, so a bug shared by both sides
    could not cancel out), independently of the collector. Kills mutant M8
    (``sha256_hex(body)`` -> ``sha256_hex(fname)``)."""
    import hashlib

    all_files = gov.GOVERNANCE_FILES + gov.AI_POLICY_FILES
    assert len(all_files) == 12
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
    assert checked == 12


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


def test_collect_governance_transport_error_propagates(tmp_path):
    fake = FakeApi([[]], fetch_error="gh api rc=1: 404 Not Found")
    try:
        gov.collect_governance(tmp_path / "raw", OWNER, NAME, now=NOW,
                               api=fake, fetch_file=lambda p: None)
        raise AssertionError("transport error must propagate to the gather hook")
    except RuntimeError as e:
        assert "404" in str(e)
    assert not (tmp_path / "raw" / "governance").exists(), "no envelope on failure"


# --- 3.1 signoff_share oracles (W1-6 ownership extension of commits.py) ------- #

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


# --- Blocker B1 round 2: a bare foundation-name substring is never a CLA --- #

#: The six check names the round-1 matcher wrongly derived FOUNDATION_CLA
#: from (O2 in the review): common CI/build/license-badge job names on an
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
    """B1: each false-positive name alongside a real DCO check -- DCO must
    win, not the bare foundation-name substring (round-1 defect: DCO 100 ->
    FOUNDATION_CLA 75 on a mis-read build-job name)."""
    value, ev = gov.classify_legal_ip([name, "DCO"], None)
    assert value == "DCO", f"{name!r} alongside DCO wrongly derived {value!r}"
    assert ev["rule"] == "dco_check"


def test_legal_ip_foundation_branded_cla_checks_still_derive_foundation_cla():
    """Over-correction guard: a genuine foundation-branded CLA check (not
    just EasyCLA) must still classify FOUNDATION_CLA after the B1 tightening."""
    for name in ("ASF CLA", "apache-cla", "cncf-cla", "eclipse-cla/validation"):
        value, ev = gov.classify_legal_ip([name], None)
        assert value == "FOUNDATION_CLA", f"{name!r} derived {value!r}"
        assert ev["rule"] == "foundation_cla_check"


def test_legal_ip_generic_cla_bot_names_derive_corp_cla():
    """Positive control: a real (non-foundation) CLA check name still
    classifies CORP_CLA after the B1 tightening."""
    for name in ("EasyCLA", "cla-assistant"):
        value, _ = gov.classify_legal_ip([name], None)
        assert value in ("FOUNDATION_CLA", "CORP_CLA")
    value, ev = gov.classify_legal_ip(["cla-assistant"], None)
    assert value == "CORP_CLA" and ev["rule"] == "corp_cla_check"
    value, ev = gov.classify_legal_ip(["EasyCLA"], None)
    assert value == "FOUNDATION_CLA" and ev["rule"] == "foundation_cla_check"


# --- W1: SIGNOFF_THRESHOLD must actually be read ---------------------------- #

def test_legal_ip_signoff_threshold_is_read_from_the_constant(monkeypatch):
    """The lead's named mutant ("drop the sign-off threshold to 0.0") must be
    caught by a test that changes the *constant*, not the inline literal."""
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
            assoc: str, typ: str = "User") -> dict:
    return {"number": number, "createdAt": created, "mergedAt": merged,
            "author": {"__typename": typ, "login": login},
            "authorAssociation": assoc}


def test_promotion_verified_external_to_member():
    prw = _prw([_raw_pr(100, "2026-03-01T00:00:00Z", "2026-03-05T00:00:00Z",
                        "alice", "NONE"),
                _raw_pr(110, "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z",
                        "alice", "MEMBER")])
    value, ev = gov.classify_promotion_ladder(prw, {}, [])
    assert value == "VERIFIED"
    assert ev["rule"] == "verified_transition"
    assert ev["promoted_accounts"] == [
        {"login": "alice", "from": "NONE", "to": "MEMBER"}]


def test_promotion_no_transition_same_association():
    prw = _prw([_raw_pr(100, "2026-03-01T00:00:00Z", "2026-03-05T00:00:00Z",
                        "alice", "MEMBER"),
                _raw_pr(110, "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z",
                        "alice", "MEMBER")])
    value, ev = gov.classify_promotion_ladder(prw, {}, [])
    assert value == "AD_HOC"
    assert ev["rule"] == "no_evidence"
    assert ev["promoted_accounts"] == []


def test_promotion_bot_author_never_counts():
    prw = _prw([_raw_pr(100, "2026-03-01T00:00:00Z", None, "dep[bot]", "NONE",
                        typ="Bot"),
                _raw_pr(110, "2026-09-01T00:00:00Z", None, "dep[bot]", "MEMBER",
                        typ="Bot")])
    value, _ = gov.classify_promotion_ladder(prw, {}, [])
    assert value == "AD_HOC"


def test_promotion_member_to_external_is_not_promotion():
    prw = _prw([_raw_pr(100, "2026-03-01T00:00:00Z", None, "bob", "MEMBER"),
                _raw_pr(110, "2026-09-01T00:00:00Z", None, "bob", "NONE")])
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
                        "alice", "NONE"),
                _raw_pr(2, "2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z",
                        "alice", "COLLABORATOR")])
    files = {"GOVERNANCE.md": {"present": True, "sha256": "h",
                               "body": "become a maintainer\n"}}
    value, ev = gov.classify_promotion_ladder(
        prw, files, _corp365(("alice", "a@one.org")))
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


# --- Round 4 de-scope: PROSCRIBED detection re-planned as W1-6b ------------- #
#
# Three rounds of ban-detection schemes (six regexes -> a line-level
# negation guard -> a 19-row sentence table) each fixed the previous
# round's false positives while introducing new ones of the opposite
# polarity. The round-3 review's own root-cause finding: co-occurrence of
# an AI token and a negation/ban token in the same span cannot separate
# "Do not submit AI-generated code" from "Do not worry if you used AI",
# nor "AI is prohibited" from "undisclosed AI use is prohibited" -- both
# need the ban's *object*, which no round ever bound. `classify_ai_posture`
# no longer attempts ban detection at all (BAN_TABLE / PERMISSION_TABLE /
# the sentence splitter / `_ban_match` are deleted, module docstring):
# REGULATED is AI_POLICY_FILES presence only; everything else is AD_HOC.
# PROSCRIBED is never returned -- these tests assert that promise directly
# against the round-3 review's own measured false-positive class.

#: The nine ordinary CONTRIBUTING.md sentences the round-3 review measured
#: (item 1, section 2.3): 7 of 9 mis-derived PROSCRIBED under the deleted
#: table-driven scheme; the remaining two were already AD_HOC even then.
#: All nine are AD_HOC now, uniformly -- CONTRIBUTING.md is not in
#: AI_POLICY_FILES, and REGULATED no longer reads body content at all, so
#: none of the nine can ever become REGULATED from this text.
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
    explicit policy section: presence is the whole signal now (content is
    irrelevant), same mechanism as test_ai_posture_regulated_policy_file_present."""
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
    carrying one of the nine sentences that used to misfire (cycled to fill
    all twelve): PROSCRIBED is structurally unreachable (classify_ai_posture
    has no code path that returns it) and REGULATED fires from the
    AI_POLICY_FILES entries' mere presence, exactly as it would for any
    other body text."""
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

# --- W8: c365 must be a row sequence, never a bare mapping ------------------- #

def test_orgs_by_login_dict_shaped_c365_does_not_crash():
    """O8: a dict-shaped c365 iterates over its keys, not row mappings; must
    be treated as no usable rows, never crash with AttributeError."""
    assert gov._orgs_by_login({"bob": "acme.com"}) == {}


def test_signoff_share_dict_shaped_c365_does_not_crash():
    assert gov.signoff_share({"bob": "acme.com"}) is None


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
    """02 §4 3.1/3.3/4.2: no evidence at all is Unmeasured, not a measured
    NONE/AD_HOC/AD_HOC (Blocker B2 round 2: a collector failure must not
    score better than the truth -- confidence ``"missing"`` is this
    v1-format document's spelling of Unmeasured(collector_failure)).

    Fail-before: pre-change ``derive_repo`` carries no ``legal_ip`` field and
    the legacy default ``legal_ip_model`` is DCO, so the first assert fails
    with ``assert 'DCO' == 'NONE'``.
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
    """Over-correction guard: ``legal_ip_model`` is a genuinely separate v1
    field (distinct name from the v2 ``legal_ip``) and this packet must
    never touch it. ``promotion_ladder`` / ``ai_posture`` are bare v2 names
    (W7) that deliberately supersede the v1 regex heuristic of the same
    name -- in this no-evidence fixture the heuristic's own default and the
    evidence-based Unmeasured default both spell AD_HOC, so the value is
    unchanged, but the source is not: it is the W1-6 classifier now, not
    the CONTRIBUTING/CODE_OF_CONDUCT regex (see
    test_derive_repo_no_governance_evidence_defaults_none_ad_hoc_ad_hoc for
    the confidence proof).
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
                        "alice", "NONE"),
                _raw_pr(110, "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z",
                        "alice", "MEMBER")])
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
    assert m["promotion_ladder_evidence"]["promoted_accounts"] == [
        {"login": "alice", "from": "NONE", "to": "MEMBER"}]
    assert m["ai_posture"] == "AD_HOC"
    assert m["ai_posture_evidence"]["rule"] == "no_evidence"
    for field in ("legal_ip", "legal_ip_evidence", "promotion_ladder",
                  "promotion_ladder_evidence", "ai_posture",
                  "ai_posture_evidence"):
        assert bundle["confidence"][field] == "derived"
        assert bundle["provenance"][field]["source"] == \
            str(raw / "governance" / f"{KEY}.json")


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
        str(raw / "commits_365d" / f"{KEY}.json")
    # no governance envelope -> no file/check evidence for the other two,
    # and both are Unmeasured (B2: neither prw nor gov exists for the
    # ladder; gov alone is absent for posture):
    assert m["promotion_ladder"] == "AD_HOC"
    assert m["ai_posture"] == "AD_HOC"
    assert bundle["confidence"]["legal_ip"] == "derived"
    assert bundle["confidence"]["promotion_ladder"] == "missing"
    assert bundle["confidence"]["ai_posture"] == "missing"


# --- Blocker B2 round 2: Unmeasured(collector_failure) vs measured zero --- #

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
    # W1-1's per-label gfi collector treats a bare list as legacy and
    # re-fetches (needs_gather); the new shape's metadata-first record is
    # what actually marks it done.
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
                   "source_used": "deps.dev"},
    "pr_window": {"count": 0, "truncated": False, "cost_points": 0,
                  "prs": [], "derived": []},
    # W1-7 postdates this file's own governance-status tests and is not
    # under test here; marking it done avoids live gh api calls (Warning
    # WW2 round 3: these three tests leaked releases/issues_counts/runs/
    # labels for "acme/widget" with no seed and no block_live_network).
    "releases": {}, "issues_counts": {}, "runs": {},
    # W1-4's author_history hook also postdates this file and is not under
    # test here; marking it done avoids a live AuthorWindow gh api call —
    # surfaced by W1-6's landed network guard (round-3 fix).
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
