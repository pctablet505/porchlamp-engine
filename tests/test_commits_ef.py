"""Commit author email domains -> elephant factor, bus factor, vitality.

Covers the ``cvi.collect.commits`` module (domain org derivation, typed
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

from cvi.collect import commits as cc  # noqa: E402


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
    assert cc.elephant_factor([], NOW) == 1
    assert cc.bus_factor([], NOW) == 1
    stale = [rec("a@corp.com", "alice", days_ago=400)]  # outside the 365 d window
    assert cc.elephant_factor(stale, NOW) == 1
    assert cc.bus_factor(stale, NOW) == 1
    unattributable = [rec("", days_ago=5 + i) for i in range(100)]  # no email/login
    assert cc.elephant_factor(unattributable, NOW) == 1
    assert cc.bus_factor(unattributable, NOW) == 1


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

    rows = cc.collect_commits_365d(fetch, now=NOW)
    assert len(rows) == 240
    assert [p for p, _ in calls] == [1, 2, 3]
    assert all(s == (NOW - timedelta(days=365)).isoformat() for _, s in calls)
    assert rows[0] == {"date": "2026-09-01T00:00:00Z", "author_email": "u0@corp.com",
                       "author_login": "u0", "author_type": "User", "signed_off": False}


def test_collect_caps_at_1000_commits():
    calls: list[int] = []

    def fetch(page: int, since: str):
        calls.append(page)
        return [_raw(i, page) for i in range(100)]

    rows = cc.collect_commits_365d(fetch, now=NOW)
    assert len(rows) == 1000
    assert calls == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]


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
                            ("dependents", {}), ("releases", {}),
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
    assert env["data"] == [
        {"date": "2026-09-01T00:00:00Z", "author_email": "u0@corp.com",
         "author_login": "u0", "author_type": "User", "signed_off": False},
        {"date": "2026-09-02T00:00:00Z", "author_email": "n@corp.com",
         "author_login": "", "author_type": "", "signed_off": False},
    ]
    commit_calls = [c for c in calls if c[0] == "repos/acme/widget/commits"]
    assert len(commit_calls) == 1
    assert any(a.startswith("since=") for a in commit_calls[0]) and "-f" in commit_calls[0]
