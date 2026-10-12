"""Commit author email domains -> elephant factor, bus factor, vitality.

Covers the ``porchlamp.collect.commits`` module (domain org derivation, typed
actor bot exclusion, 365 d collection cap, envelope-row parsing) and the
append-only hooks in ``scripts/derive.py`` (EF block: email-domain EF,
bus factor, vitality) and ``scripts/gather.py`` (``commits_365d`` collector
block). The shared ``tests/test_derive.py`` (login-proxy EF edges and
bundle) is the over-correction guard for the login-proxy path and must stay
green.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from porchlamp.collect import commits as cc  # noqa: E402


def _load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


derive = _load_script("derive_w15", "derive.py")
gather = _load_script("gather_w15", "gather.py")

NOW = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
OWNER, NAME = "acme", "widget"
KEY = f"{OWNER}__{NAME}"


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def rec(email: str, login: str = "", typ: str = "User", days_ago: float = 1.0):
    return cc.CommitRecord(
        cc.CommitActor(login=login, email=email, actor_type=typ),
        NOW - timedelta(days=days_ago),
    )


def row(email: str, login: str = "", typ: str = "User", days_ago: float = 1.0) -> dict:
    return {"date": iso(NOW - timedelta(days=days_ago)),
            "author_email": email, "author_login": login, "author_type": typ}


def _raw(i: int, page: int = 1) -> dict:
    return {"sha": f"sha-{page}-{i}",
            "commit": {"author": {"name": f"User {i}", "email": f"u{i}@corp.com",
                                  "date": "2026-09-01T00:00:00Z"}},
            "author": {"login": f"u{i}", "type": "User"}}


def oracle_one() -> list:
    """60 a@corp.com + 30 b@corp.com + 10 c@gmail.com."""
    return ([rec("a@corp.com", "alice", days_ago=1 + i / 40) for i in range(60)]
            + [rec("b@corp.com", "bob", days_ago=1 + i / 40) for i in range(30)]
            + [rec("c@gmail.com", "carol", days_ago=1 + i / 40) for i in range(10)])


def oracle_three_domains() -> list:
    """40/30/30 across three corporate domains."""
    return ([rec("a@alpha.com", "alice", days_ago=1 + i / 40) for i in range(40)]
            + [rec("b@beta.com", "bob", days_ago=1 + i / 40) for i in range(30)]
            + [rec("c@gamma.com", "carol", days_ago=1 + i / 40) for i in range(30)])


# --------------------------------------------------------------------------- #
# EF oracles (spec 3.2 / D-PARAM 3.2)
# --------------------------------------------------------------------------- #

def test_ef_oracle_corp_dominant():
    assert cc.elephant_factor(oracle_one(), NOW) == 1


def test_ef_oracle_three_domains():
    assert cc.elephant_factor(oracle_three_domains(), NOW) == 2


def test_personal_domains_count_one_org_per_author():
    # 35 corp + 30/20/15 on one personal domain: lumping the provider would
    # give EF 1 (65 >= 50%); one org per author gives 35 + 30 >= 50% -> 2.
    recs = ([rec("a@alpha.com", "alice", days_ago=1 + i / 20) for i in range(35)]
            + [rec("u1@gmail.com", "u1", days_ago=1 + i / 20) for i in range(30)]
            + [rec("u2@gmail.com", "u2", days_ago=1 + i / 20) for i in range(20)]
            + [rec("u3@gmail.com", "u3", days_ago=1 + i / 20) for i in range(15)])
    assert cc.elephant_factor(recs, NOW) == 2


def test_noreply_maps_to_login_and_never_to_the_domain():
    assert cc.org_key(cc.CommitActor("alice", "x@users.noreply.github.com", "User")) == "alice"
    assert (cc.org_key(cc.CommitActor("bob", "52341+bob@users.noreply.github.com", "User"))
            == "bob")
    assert cc.org_key(cc.CommitActor("", "x@users.noreply.github.com", "")) is None
    # recorded rows: two noreply authors (no login on 30 of them) + one corp org
    rows = ([row("1001+alice@users.noreply.github.com", "alice", days_ago=1 + i / 10)
             for i in range(55)]
            + [row("2002@users.noreply.github.com", days_ago=1 + i / 10) for i in range(30)]
            + [row("b@corp.com", "bob", days_ago=1 + i / 10) for i in range(15)])
    recs = cc.records_from_rows(rows)
    assert cc.elephant_factor(recs, NOW) == 1  # alice 55/100; noreply domain is no org


# --------------------------------------------------------------------------- #
# Bot typing (D-IN-1)
# --------------------------------------------------------------------------- #

def test_bot_typed_authors_excluded_from_human_counts():
    # recorded envelope rows: 60 Bot + 40 a@corp.com + 30 c@gmail.com
    rows = ([row("dependabot[bot]@users.noreply.github.com", "dependabot[bot]",
                 typ="Bot", days_ago=1 + i / 10) for i in range(60)]
            + [row("a@corp.com", "alice", days_ago=1 + i / 10) for i in range(40)]
            + [row("c@gmail.com", "carol", days_ago=1 + i / 10) for i in range(30)])
    recs = cc.records_from_rows(rows)
    # humans only: 40/70 >= 50% -> EF 1 (counting the 60 bot commits would give 2)
    assert cc.elephant_factor(recs, NOW) == 1
    assert cc.bus_factor(recs, NOW) == 1


def test_is_bot_exact_rules():
    assert cc.is_bot(cc.CommitActor("d", "d@users.noreply.github.com", "Bot")) is True
    assert cc.is_bot(cc.CommitActor("dependabot[bot]", "", "")) is True
    assert cc.is_bot(cc.CommitActor("Renovate", "", "")) is True
    assert cc.is_bot(cc.CommitActor("alice", "a@corp.com", "User")) is False
    assert cc.is_bot(cc.CommitActor("alice-bot", "a@corp.com", "User")) is False


# --------------------------------------------------------------------------- #
# Bus factor (same fixtures as the EF oracles)
# --------------------------------------------------------------------------- #

def test_bus_factor_oracles_same_fixture():
    assert cc.bus_factor(oracle_one(), NOW) == 1       # alice 60/100 >= 50%
    assert cc.bus_factor(oracle_three_domains(), NOW) == 2  # 40 < 50%, 40+30 >= 50%


def test_bus_factor_counts_individuals_not_orgs():
    recs = ([rec("a@corp.com", "alice", days_ago=1 + i / 20) for i in range(40)]
            + [rec("b@corp.com", "bob", days_ago=1 + i / 20) for i in range(30)]
            + [rec("c@gmail.com", "carol", days_ago=1 + i / 20) for i in range(30)])
    assert cc.elephant_factor(recs, NOW) == 1  # corp.com 70/100 >= 50%
    assert cc.bus_factor(recs, NOW) == 2       # 40 < 50%, 40 + 30 >= 50%


def test_ef_and_bus_floors_and_window():
    # No attributable commits (empty, only-stale, only-unattributable) is an
    # absence -> None (B:commits-cap); it is NOT the worst measured value of 1.
    assert cc.elephant_factor([], NOW) is None
    assert cc.bus_factor([], NOW) is None
    stale = [rec("a@corp.com", "alice", days_ago=400)]  # outside the 365 d window
    assert cc.elephant_factor(stale, NOW) is None
    assert cc.bus_factor(stale, NOW) is None
    unattributable = [rec("", days_ago=5 + i) for i in range(100)]  # no email/login
    assert cc.elephant_factor(unattributable, NOW) is None
    assert cc.bus_factor(unattributable, NOW) is None
    # A genuine single-org project still measures the floor of 1 (see
    # test_single_org_project_still_measures_one).


# --------------------------------------------------------------------------- #
# Zero attributable commits is an ABSENCE, not the worst measured value (B:commits-cap)
# --------------------------------------------------------------------------- #

def test_elephant_factor_no_records_is_absence_not_one():
    # No commit data at all is a different fact from "one org owns everything":
    # elephant_factor must return None so the caller can tell them apart.
    assert cc.elephant_factor([], NOW) is None


def test_bus_factor_no_records_is_absence_not_one():
    assert cc.bus_factor([], NOW) is None


def test_single_org_project_still_measures_one():
    # A genuine single-organisation project: every commit is a@corp.com, so
    # the elephant factor is the real measured floor of 1 (not an absence).
    single_org = [rec("a@corp.com", f"dev{i % 3}", days_ago=1 + i / 40)
                  for i in range(50)]
    assert cc.elephant_factor(single_org, NOW) == 1
    # The bus factor's floor needs a single *author*: one login owns all 50
    # commits, so 50% is reached by one author -> bus factor 1.
    single_author = [rec("a@corp.com", "alice", days_ago=1 + i / 40)
                     for i in range(50)]
    assert cc.bus_factor(single_author, NOW) == 1


# --------------------------------------------------------------------------- #
# Vitality (D-GATE-4: >= 1 non-bot commit in the trailing 180 d)
# --------------------------------------------------------------------------- #

def test_vitality_oracles():
    assert cc.is_vital([rec("a@corp.com", "alice", days_ago=10)], NOW) is True
    assert cc.is_vital([rec("a@corp.com", "alice", days_ago=179)], NOW) is True
    assert cc.is_vital([rec("a@corp.com", "alice", days_ago=200)], NOW) is False
    assert cc.is_vital([rec("a@corp.com", "alice", days_ago=181)], NOW) is False
    assert cc.is_vital([], NOW) is False


def test_vitality_bot_only_recent_commits_is_not_vital():
    recs = [rec("d@users.noreply.github.com", "dependabot[bot]", typ="Bot", days_ago=5),
            rec("a@corp.com", "alice", days_ago=200)]
    assert cc.is_vital(recs, NOW) is False


# --------------------------------------------------------------------------- #
# Collector: 365 d window, cap 1000, transport-boundary paging
# --------------------------------------------------------------------------- #

def test_collect_stops_at_short_page_and_shapes_rows():
    calls: list[tuple] = []

    def fetch(page: int, since: str):
        calls.append((page, since))
        return [_raw(i, page) for i in range(100 if page < 3 else 40)]

    data = cc.collect_commits_365d(fetch, now=NOW)
    rows = data["rows"]
    assert len(rows) == 240
    assert data["truncated"] is False and data["count"] == 240
    assert [p for p, _ in calls] == [1, 2, 3]
    assert all(s == (NOW - timedelta(days=365)).isoformat() for _, s in calls)
    assert rows[0] == {"date": "2026-09-01T00:00:00Z", "author_email": "u0@corp.com",
                       "author_login": "u0", "author_type": "User", "signed_off": False}


def test_collect_caps_at_1000_commits():
    calls: list[int] = []

    def fetch(page: int, since: str):
        calls.append(page)
        return [_raw(i, page) for i in range(100)]

    data = cc.collect_commits_365d(fetch, now=NOW)
    assert len(data["rows"]) == 1000
    assert data["truncated"] is True  # every page full: the fetch stopped on the cap
    assert calls == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]


# --------------------------------------------------------------------------- #
# The cap is a recorded fact (B:commits-cap, second half)                     #
# --------------------------------------------------------------------------- #

def test_collect_records_truncation_when_cap_hit():
    def fetch(page: int, since: str):
        return [_raw(i, page) for i in range(100)]  # every page full

    data = cc.collect_commits_365d(fetch, now=NOW)
    assert data["truncated"] is True
    assert data["count"] == cc.COMMIT_CAP == 1000
    assert data["cap"] == cc.COMMIT_CAP
    assert len(data["rows"]) == 1000


def test_collect_not_truncated_when_under_cap():
    def fetch(page: int, since: str):
        return [_raw(i, page) for i in range(100 if page < 3 else 40)]

    data = cc.collect_commits_365d(fetch, now=NOW)
    assert data["truncated"] is False
    assert data["count"] == 240 < data["cap"]
    assert len(data["rows"]) == 240


def test_statistics_unchanged_on_normal_multiorg_input():
    # Regression guard: the two fixes must not move a correct answer on a
    # normal (untruncated, multi-org, multi-author) input.
    recs = oracle_three_domains()
    assert cc.elephant_factor(recs, NOW) == 2
    assert cc.bus_factor(recs, NOW) == 2


def test_shape_handles_unlinked_and_bot_authors():
    raws = [_raw(1),
            {"sha": "s", "commit": {"author": {"name": "N", "email": "n@corp.com",
                                               "date": "2026-09-02T00:00:00Z"}},
             "author": None},
            {"sha": "s2", "commit": {"author": {"name": "D",
                                                "email": "d@users.noreply.github.com",
                                                "date": "2026-09-03T00:00:00Z"}},
             "author": {"login": "dependabot[bot]", "type": "Bot"}}]
    rows = cc.shape_commit_rows(raws)
    assert rows[1] == {"date": "2026-09-02T00:00:00Z", "author_email": "n@corp.com",
                       "author_login": "", "author_type": "", "signed_off": False}
    assert rows[2]["author_type"] == "Bot" and rows[2]["author_login"] == "dependabot[bot]"


# --- ownership extension: signed_off boolean (02 section 4 3.1) ------------- #

def test_shape_records_signed_off_true_on_trailer():
    raws = [{"sha": "s1", "commit": {
        "author": {"email": "a@corp.com", "date": "2026-09-01T00:00:00Z"},
        "message": "fix: thing\n\nSigned-off-by: A <a@corp.com>\n"},
        "author": {"login": "alice", "type": "User"}}]
    rows = cc.shape_commit_rows(raws)
    assert rows[0]["signed_off"] is True


def test_shape_records_signed_off_false_without_trailer():
    raws = [{"sha": "s1", "commit": {
        "author": {"email": "a@corp.com", "date": "2026-09-01T00:00:00Z"},
        "message": "fix: thing\n"},
        "author": {"login": "alice", "type": "User"}}]
    rows = cc.shape_commit_rows(raws)
    assert rows[0]["signed_off"] is False


def test_shape_never_stores_message_text():
    raws = [{"sha": "s1", "commit": {
        "author": {"email": "a@corp.com", "date": "2026-09-01T00:00:00Z"},
        "message": "a very specific secret-looking message body\n"},
        "author": {"login": "alice", "type": "User"}}]
    rows = cc.shape_commit_rows(raws)
    assert "message" not in rows[0]
    assert all("secret" not in str(v) for v in rows[0].values())


def test_records_from_rows_tolerates_missing_fields():
    recs = cc.records_from_rows([{"author_email": "a@corp.com"},
                                 {"date": "2026-01-02T03:04:05Z", "author_login": "bob"}])
    assert recs[0].actor.email == "a@corp.com" and recs[0].actor.login == ""
    assert recs[0].actor.actor_type == "" and recs[0].date is None
    assert recs[1].date == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert recs[1].actor.email == "" and recs[1].actor.login == "bob"


# --------------------------------------------------------------------------- #
# derive_repo hook: email-domain EF block + bus factor + vitality
# --------------------------------------------------------------------------- #

def _write_envelope(tmp: Path, collector: str, key: str, data) -> None:
    p = tmp / "raw" / collector
    p.mkdir(parents=True, exist_ok=True)
    (p / f"{key}.json").write_text(
        json.dumps({"fetched_at": "2026-09-11T00:00:00Z", "source": "test",
                    "data": data}), encoding="utf-8")


def _run(tmp: Path) -> dict:
    return derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp, fetch=False, now=NOW)


CONTRIBS = [{"login": "alice", "contributions": 40},
            {"login": "bob", "contributions": 40},
            {"login": "carol", "contributions": 20}]  # login-proxy EF would be 2


def test_derive_repo_ef_block_from_email_domains(tmp_path):
    rows = ([row("a@corp.com", "alice", days_ago=1 + i / 40) for i in range(60)]
            + [row("b@corp.com", "bob", days_ago=1 + i / 40) for i in range(30)]
            + [row("c@gmail.com", "carol", days_ago=1 + i / 40) for i in range(10)])
    _write_envelope(tmp_path, "commits_365d", KEY, rows)
    _write_envelope(tmp_path, "contributors", KEY, CONTRIBS)
    out = _run(tmp_path)
    m, conf = out["meta"], out["confidence"]
    assert m["elephant_factor"] == 1  # domains: corp.com 90/100 (contribs path would be 2)
    assert m["bus_factor"] == 1       # alice 60/100
    assert m["vitality"] is True
    assert conf["elephant_factor"] == "derived"
    assert conf["bus_factor"] == "derived" and conf["vitality"] == "derived"
    assert out["provenance"]["bus_factor"]["source"].endswith("commits_365d/acme__widget.json")


def _raw_commit(email: str, login: str, days_ago: float, message: str = "") -> dict:
    """One GitHub REST commit object -- the input ``collect_commits_365d`` shapes."""
    return {"sha": f"sha-{login}-{days_ago}",
            "commit": {"author": {"name": login, "email": email,
                                  "date": iso(NOW - timedelta(days=days_ago))},
                       "message": message},
            "author": {"login": login, "type": "User"}}


def _collected(raw: list[dict]) -> dict:
    """``data`` exactly as ``collect_commits_365d`` records it for ``raw``:
    the producer builds the envelope here, so a change to what it records
    reaches these tests instead of only the collector's own."""
    return cc.collect_commits_365d(lambda page, since: raw if page == 1 else [], now=NOW)


_WINDOW_RAW = ([_raw_commit("a@corp.com", "alice", 1 + i / 40, "Signed-off-by: A") for i in range(60)]
               + [_raw_commit("b@other.com", "bob", 1 + i / 40) for i in range(30)]
               + [_raw_commit("c@gmail.com", "carol", 1 + i / 40) for i in range(10)])


def test_derive_repo_reads_the_collectors_window_dict(tmp_path):
    """``gather.py`` writes ``collect_commits_365d``'s window dict as the
    envelope ``data``, not a row list. derive_repo iterated the dict's four
    keys and raised ``AttributeError`` on ``str.get`` -- every repo whose
    commits had been re-collected (5,752 of 5,755 at the 2026-09-22 L3c
    layer) failed to derive, and the layer was built from stale bundles."""
    data = _collected(_WINDOW_RAW)
    assert isinstance(data, dict) and data["truncated"] is False  # the shape under test
    _write_envelope(tmp_path, "commits_365d", KEY, data)
    out = _run(tmp_path)
    m, conf = out["meta"], out["confidence"]
    assert m["elephant_factor"] == 1 and conf["elephant_factor"] == "derived"  # corp.com 60/100
    assert m["bus_factor"] == 1 and conf["bus_factor"] == "derived"            # alice 60/100
    assert m["vitality"] is True and conf["vitality"] == "derived"
    # the governance block reads the same rows (sign-off share, 3.1):
    assert m["legal_ip"] == "DCO"
    assert m["legal_ip_evidence"]["signoff"] == {"count": 60, "total": 100, "share": 0.6}


def test_derive_repo_window_dict_and_row_list_derive_identically(tmp_path):
    """Over-correction guard: the row-list envelopes the v1 collectors wrote
    (and every ``commits_180d`` envelope still is) derive exactly what a
    window dict holding the same rows derives -- the unwrap changes which
    shape is read, never a value (EF, bus factor, vitality, Pike signals,
    governance: the whole meta and confidence)."""
    data = _collected(_WINDOW_RAW)
    _write_envelope(tmp_path / "dict", "commits_365d", KEY, data)
    _write_envelope(tmp_path / "list", "commits_365d", KEY, data["rows"])
    as_dict, as_list = _run(tmp_path / "dict"), _run(tmp_path / "list")
    assert as_dict["meta"] == as_list["meta"]
    assert as_dict["confidence"] == as_list["confidence"]
    assert as_dict["meta"]["elephant_factor"] == 1  # not two equal failures


def test_envelope_rows_accepts_both_recorded_shapes_only():
    rows = [{"author_login": "alice", "date": "2026-09-01T00:00:00Z"}]
    assert cc.envelope_rows(rows) is rows
    assert cc.envelope_rows({"count": 1, "cap": 1000, "truncated": True, "rows": rows}) is rows
    assert cc.envelope_rows(_collected([])) == []   # a measured empty window stays a list
    assert cc.envelope_rows(None) is None
    assert cc.envelope_rows({"bob": "acme.com"}) is None   # a mapping without rows is not a window
    assert cc.envelope_rows({"rows": "not-a-list"}) is None
    assert cc.envelope_rows("rows") is None


def test_derive_repo_fallback_without_commits_365d(tmp_path):
    _write_envelope(tmp_path, "contributors", KEY, CONTRIBS)
    out = _run(tmp_path)
    m, conf = out["meta"], out["confidence"]
    assert m["elephant_factor"] == 2          # pre-change login-proxy path unchanged
    assert conf["elephant_factor"] == "derived"
    assert m["bus_factor"] == 1 and conf["bus_factor"] == "missing"
    assert m["vitality"] is False and conf["vitality"] == "missing"


# --------------------------------------------------------------------------- #
# gather_repo hook: commits_365d collector block
# --------------------------------------------------------------------------- #

def _seed_raw(tmp: Path, collector: str, key: str, data) -> None:
    # gather.py DATA is the data/raw tree itself: files live at <DATA>/<collector>/
    p = tmp / collector
    p.mkdir(parents=True, exist_ok=True)
    (p / f"{key}.json").write_text(
        json.dumps({"fetched_at": "2026-09-11T00:00:00Z", "source": "test",
                    "data": data}), encoding="utf-8")


def test_gather_hook_writes_commits_365d(tmp_path, monkeypatch, block_live_network):
    for collector, data in (("github_meta", {"default_branch": "main", "stars": 1}),
                            ("pr_events", []), ("pr_reviews", {}),
                            ("contributors", []), ("gfi", []),
                            ("actions_runs", []), ("tree", {"paths": []}),
                            ("commits_180d", []),
                            ("depsdev", {"stars": 1}), ("scorecard", {"score": 5.0}),
                            # Every collector other than commits_365d is
                            # marked done: unseeded, they make live calls
                            # (api.deps.dev, packages.ecosyste.ms, 3x gh
                            # api) that the block_live_network guard fails.
                            ("repo_object", {}), ("pr_window", {}), ("governance", {}),
                            ("dependents", {"package": NAME, "guess_linked": True}),
                            ("releases", {}),
                            ("issues_counts", {}), ("runs", {}),
                            # The author_history hook is network-backed too,
                            # so it is seeded alongside the rest.
                            ("author_history", {})):
        _seed_raw(tmp_path, collector, KEY, data)
    _seed_raw(tmp_path, "downloads", f"pypi__{NAME}", {"monthly": 123})
    monkeypatch.setattr(gather, "DATA", tmp_path)
    raw = [_raw(0),
           {"sha": "x", "commit": {"author": {"name": "N", "email": "n@corp.com",
                                              "date": "2026-09-02T00:00:00Z"}},
            "author": None}]
    calls: list[tuple] = []

    def fake_gh(*args):
        calls.append(args)
        return raw

    monkeypatch.setattr(gather, "_gh", fake_gh)
    st = gather.gather_repo(OWNER, NAME, "pypi")
    assert st["commits_365d"] == "ok"
    assert st["contributors"] == "skip" and st["downloads"] == "skip"
    env = json.loads((tmp_path / "commits_365d" / f"{KEY}.json").read_text())
    # collect_commits_365d now returns a window-metadata dict (the cap is a
    # recorded fact); the shaped rows live under "rows", count < cap so the
    # window is not truncated.
    assert env["data"]["truncated"] is False and env["data"]["count"] == 2
    assert env["data"]["cap"] == cc.COMMIT_CAP
    assert env["data"]["rows"] == [
        {"date": "2026-09-01T00:00:00Z", "author_email": "u0@corp.com",
         "author_login": "u0", "author_type": "User", "signed_off": False},
        {"date": "2026-09-02T00:00:00Z", "author_email": "n@corp.com",
         "author_login": "", "author_type": "", "signed_off": False},
    ]
    commit_calls = [c for c in calls if c[0] == "repos/acme/widget/commits"]
    assert len(commit_calls) == 1
    assert any(a.startswith("since=") for a in commit_calls[0]) and "-f" in commit_calls[0]
