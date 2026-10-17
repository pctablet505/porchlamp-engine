"""Black-hole evidence on a snapshot built through the real pipeline:
raw envelopes -> ``scripts/derive.py`` -> ``build_snapshot_dir`` -> score ->
``write_scored_snapshot`` -> ``GET /v1/porchlamp/repos/{owner}/{name}``.

The store writes no ``params`` row for a gated repository, so the panel must
be recomputed from the raw ``pr_window`` envelope at the snapshot's own clock
(``snapshots.as_of``) with the verdict's observation rule (the earlier of the
envelope's ``fetched_at`` and ``window_anchor``). Each expected headline is
read from the bundle derive wrote (``gr14_*``), never from the store. The
envelope is read only through the stored snapshot's pins
(``PinnedEvidenceFiles``): a changed file is refused.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from porchlamp.collect.envelope import write_envelope
from porchlamp.serve.app import PinnedEvidenceFiles, create_app
from porchlamp.snapshot.build_cmd import build_snapshot_dir
from porchlamp.snapshot.store_cmd import score_snapshot_dir, write_scored_snapshot
from porchlamp.store.migrations import apply_migrations
from porchlamp.store.schema import connect as connect_store

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("derive_bh_store_built", ROOT / "scripts" / "derive.py")
derive = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(derive)

AS_OF = datetime(2026, 9, 12, tzinfo=timezone.utc)
OWNER = "ghost-org"
#: 12 unanswered + 1 answered counted PRs, plus 1 never-ready draft.
SILENT = "silent"
#: window_anchor 10 d before as_of, fetched 1 d before: 3 PRs 20 d old are
#: under 14 d at the anchor (excluded by the verdict) but 19 d at fetched_at;
#: 2 PRs 185 d old are inside the anchor's 180-d window but outside as_of's.
EARLY_ANCHOR = "early-anchor"
#: PR nodes recorded before the RFC-0008 query fields (no isDraft/timelineItems).
OLD_SHAPE = "old-shape"
LONG_REPLY = "Thanks, this looks good; we will review it in the next release cycle."


def _iso(value: datetime) -> str:
    return value.isoformat()


def _pr(number: int, days_ago: float, *, rfc0008_fields: bool = True, is_draft: bool = False,
        maintainer_reply: bool = False) -> dict[str, Any]:
    created = AS_OF - timedelta(days=days_ago)
    comments = []
    if maintainer_reply:
        comments.append({"author": {"__typename": "User", "login": "maint-secret"},
                         "authorAssociation": "MEMBER", "bodyText": LONG_REPLY,
                         "createdAt": _iso(created + timedelta(days=1))})
    node: dict[str, Any] = {
        "number": number, "createdAt": _iso(created), "closedAt": None, "mergedAt": None,
        "state": "OPEN", "mergedBy": None,
        "author": {"__typename": "User", "login": f"outsider-secret-{number}",
                   "createdAt": "2015-01-01T00:00:00+00:00"},
        "authorAssociation": "NONE",
        "reviews": {"nodes": []}, "comments": {"nodes": comments}, "reviewThreads": {"totalCount": 0},
    }
    if rfc0008_fields:
        node["isDraft"] = is_draft
        node["timelineItems"] = {"nodes": []}
    return node


def _write_pr_window(data_dir: Path, name: str, prs: list[dict[str, Any]], *,
                     window_anchor: datetime, fetched_at: datetime) -> Path:
    path = data_dir / "raw" / "pr_window" / f"{OWNER}__{name}.json"
    data = {"window_anchor": _iso(window_anchor), "window_days": 180, "count": len(prs),
            "truncated": False, "cost_points": 1, "derived": [], "prs": prs}
    return write_envelope(path, "test:pr_window", data, _iso(fetched_at))


def _seed_repo(data_dir: Path, name: str, prs: list[dict[str, Any]], *,
               window_anchor: datetime, fetched_at: datetime) -> None:
    """The envelopes gates 1-4 need to pass (manifest, licence, a human commit) plus the PR window."""
    key = f"{OWNER}__{name}"
    at = _iso(AS_OF - timedelta(days=1))
    write_envelope(data_dir / "raw" / "github_meta" / f"{key}.json", "test:github_meta",
                   {"stars": 500, "forks": 10, "pushed_at": "2026-09-01T00:00:00Z", "archived": False,
                    "license_spdx": "MIT", "default_branch": "main"}, at)
    write_envelope(data_dir / "raw" / "tree" / f"{key}.json", "test:tree",
                   {"paths": ["pyproject.toml", "LICENSE", "README.md"]}, at)
    write_envelope(data_dir / "raw" / "commits_180d" / f"{key}.json", "test:commits_180d",
                   [{"author_login": "maint-secret", "date": "2026-08-01T00:00:00Z"}], at)
    _write_pr_window(data_dir, name, prs, window_anchor=window_anchor, fetched_at=fetched_at)


def _silent_prs(*, answered: bool = False) -> list[dict[str, Any]]:
    prs = [_pr(n, 30 + n, maintainer_reply=answered) for n in range(1, 13)]
    prs.append(_pr(13, 45, maintainer_reply=True))
    prs.append(_pr(14, 50, is_draft=True))
    return prs


def _store(data_dir: Path, root: Path) -> tuple[Path, Path]:
    """Build, score and store the derived bundles under ``root``: ``(snapshot dir, database)``."""
    snapshot_dir = root / "snapshots_v2" / "bh-store-built"
    build_snapshot_dir(data_dir, snapshot_dir, snapshot_id="bh-store-built", as_of=AS_OF,
                       code_commit_sha="0123456")
    scored, frozen = score_snapshot_dir(snapshot_dir)
    db_path = root / "store" / "porchlamp.db"
    db_path.parent.mkdir(parents=True)
    conn = connect_store(db_path)
    try:
        apply_migrations(conn, applied_at=AS_OF.isoformat())
        write_scored_snapshot(conn, db_path, scored, frozen, code_commit="0123456")
    finally:
        conn.close()
    return snapshot_dir, db_path


@pytest.fixture
def built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(derive, "DATA_DIR", data_dir)
    monkeypatch.setenv("PORCHLAMP_DATA_ROOT", str(data_dir))
    _seed_repo(data_dir, SILENT, _silent_prs(), window_anchor=AS_OF, fetched_at=AS_OF - timedelta(days=1))
    early = [_pr(n, 40) for n in range(1, 13)] + [_pr(n, 20) for n in range(13, 16)]
    early += [_pr(n, 185) for n in range(16, 18)]
    _seed_repo(data_dir, EARLY_ANCHOR, early, window_anchor=AS_OF - timedelta(days=10),
               fetched_at=AS_OF - timedelta(days=1))
    _seed_repo(data_dir, OLD_SHAPE, [_pr(n, 30 + n, rfc0008_fields=False) for n in range(1, 13)],
               window_anchor=AS_OF, fetched_at=AS_OF - timedelta(days=1))

    gr14: dict[str, dict[str, Any]] = {}
    (data_dir / "derived").mkdir(parents=True)
    for name in (SILENT, EARLY_ANCHOR, OLD_SHAPE):
        bundle = derive.derive_repo(OWNER, name, derive.Ecosystem.PYPI, data_dir, fetch=False, now=AS_OF)
        path = data_dir / "derived" / f"{OWNER}__{name}.json"
        path.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
        meta = json.loads(path.read_text(encoding="utf-8"))["meta"]
        gr14[name] = {k: v for k, v in meta.items() if k.startswith("gr14_")}

    snapshot_dir, db_path = _store(data_dir, tmp_path)
    files = PinnedEvidenceFiles(snapshot_dir, data_dir)
    client = TestClient(create_app(db_path, web_dir=tmp_path, evidence_files=files))
    return {"client": client, "gr14": gr14, "data_dir": data_dir, "db_path": db_path,
            "snapshot_dir": snapshot_dir, "tmp_path": tmp_path}


def _detail(client: TestClient, name: str) -> dict[str, Any]:
    resp = client.get(f"/v1/porchlamp/repos/{OWNER}/{name}")
    assert resp.status_code == 200
    return resp.json()


def _derive_counts(gr14: dict[str, Any]) -> tuple[int, int]:
    n = gr14["gr14_population_n"]
    return round(gr14["gr14_ghost_rate"] * n), n


def test_store_built_black_hole_has_evidence_equal_to_the_verdict(built: dict[str, Any]) -> None:
    """(a) evidence is not null for a store-built black hole; (b) its headline
    equals what scripts/derive.py computed for the verdict; the window is the
    snapshot clock's; source and hash name the raw file read; no logins."""
    body = _detail(built["client"], SILENT)
    assert body["gate_reason"] == "black_hole"
    ev = body["black_hole_evidence"]
    assert ev is not None
    gr14 = built["gr14"][SILENT]
    assert (ev["unanswered"], ev["population_n"]) == _derive_counts(gr14) == (12, 13)
    assert ev["unanswered_share"] == gr14["gr14_ghost_rate"]
    assert ev["window_start"] == gr14["gr14_window_start"]
    assert ev["window_end"] == gr14["gr14_window_end"] == AS_OF.isoformat()
    raw = built["data_dir"] / "raw" / "pr_window" / f"{OWNER}__{SILENT}.json"
    assert ev["source"] == f"raw/pr_window/{OWNER}__{SILENT}.json"
    assert ev["source_sha256"] == hashlib.sha256(raw.read_bytes()).hexdigest()
    assert ev["fetched_at"] == _iso(AS_OF - timedelta(days=1))
    assert ev["excluded"]["draft"] == 1
    assert len(ev["counted_prs"]) == 13
    assert sum(not pr["answered_within_14d"] for pr in ev["counted_prs"]) == 12
    text = json.dumps(ev)
    assert "secret" not in text and "outsider" not in text


def test_store_built_evidence_uses_the_verdicts_observation_time(built: dict[str, Any]) -> None:
    """(c) window_anchor earlier than fetched_at: the verdict judges age at the
    anchor and the window at as_of (12 PRs); fetched_at alone would count 15,
    the anchor's clock 14. The panel says 12, over as_of's window."""
    body = _detail(built["client"], EARLY_ANCHOR)
    assert body["gate_reason"] == "black_hole"
    ev = body["black_hole_evidence"]
    assert ev is not None
    assert (ev["unanswered"], ev["population_n"]) == _derive_counts(built["gr14"][EARLY_ANCHOR]) == (12, 12)
    assert ev["excluded"]["under_14d"] == 3
    assert ev["window_start"] == built["gr14"][EARLY_ANCHOR]["gr14_window_start"]


def test_a_raw_file_changed_after_the_snapshot_was_stored_is_refused(built: dict[str, Any]) -> None:
    """(d) the raw envelope changes after the store so every PR is answered.
    Its hash no longer matches the one the stored bundle pinned, so the
    evidence reader refuses it (HTTP 409, naming the file) instead of computing
    a panel from data the verdict was not scored on."""
    client = built["client"]
    assert _detail(client, SILENT)["black_hole_evidence"] is not None
    _write_pr_window(built["data_dir"], SILENT, _silent_prs(answered=True),
                     window_anchor=AS_OF, fetched_at=AS_OF - timedelta(days=1))
    resp = client.get(f"/v1/porchlamp/repos/{OWNER}/{SILENT}")
    assert resp.status_code == 409
    detail = resp.json()["error"]["detail"]
    assert f"raw/pr_window/{OWNER}__{SILENT}.json changed after snapshot bh-store-built was stored" in detail


def test_a_stored_bundle_changed_after_the_store_is_refused(built: dict[str, Any]) -> None:
    bundle = built["snapshot_dir"] / "bundles" / f"{OWNER}__{SILENT}.json"
    doc = json.loads(bundle.read_text(encoding="utf-8"))
    doc["inputs"]["gr14_ghost_rate"]["source_hash"] = "0" * 64
    bundle.write_text(json.dumps(doc), encoding="utf-8")
    resp = built["client"].get(f"/v1/porchlamp/repos/{OWNER}/{SILENT}")
    assert resp.status_code == 409
    assert "bundle" in resp.json()["error"]["detail"]


def test_without_evidence_files_no_evidence_is_served(built: dict[str, Any]) -> None:
    """No guessed data directory: PORCHLAMP_DATA_ROOT points at the data, but
    an app given no evidence files serves no black-hole evidence."""
    client = TestClient(create_app(built["db_path"], web_dir=built["tmp_path"]))
    body = _detail(client, SILENT)
    assert body["gate_reason"] == "black_hole"
    assert body["black_hole_evidence"] is None


def test_the_export_refuses_a_stored_black_hole_whose_rule_inputs_were_not_measured(
    built: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A store built before D-GATE-5 read the rule inputs named OLD_SHAPE (PR records without isDraft and the
    timeline) a black hole. Its evidence says rule_inputs_measured false, and the export stops, naming the
    count, instead of publishing it."""
    from porchlamp.snapshot import build_cmd

    reads_inputs = build_cmd.gate_evidence_for
    monkeypatch.setattr(build_cmd, "gate_evidence_for", lambda *args, **kwargs: dataclasses.replace(
        reads_inputs(*args, **kwargs), pr_rule_inputs_recorded=None))
    snapshot_dir, db_path = _store(built["data_dir"], built["tmp_path"] / "earlier")
    files = PinnedEvidenceFiles(snapshot_dir, built["data_dir"])
    client = TestClient(create_app(db_path, web_dir=built["tmp_path"], evidence_files=files))
    old = _detail(client, OLD_SHAPE)
    assert old["gate_reason"] == "black_hole"
    assert old["black_hole_evidence"]["rule_inputs_measured"] is False
    spec = importlib.util.spec_from_file_location("export_static_bh", ROOT / "scripts" / "export_static.py")
    export_static = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(export_static)
    with pytest.raises(SystemExit, match=r"1 of 3 black-hole verdicts lack the exclusion rules' inputs"):
        export_static.export(db_path, built["tmp_path"] / "site", snapshot_dir=snapshot_dir, data_dir=built["data_dir"])


def test_a_window_without_the_rule_inputs_is_stored_undecided_never_a_black_hole(built: dict[str, Any]) -> None:
    """The pipeline end to end: OLD_SHAPE's twelve unanswered PRs were recorded before the query asked for drafts
    and close actors, so the exclusion rules could not run. D-GATE-5 is stored UNDECIDED, not failed, and no
    black-hole evidence is served for it; SILENT's records carry the fields, and its evidence says so."""
    client = built["client"]
    assert _detail(client, SILENT)["black_hole_evidence"]["rule_inputs_measured"] is True
    old = _detail(client, OLD_SHAPE)
    assert old["gate_reason"] != "black_hole"
    assert old["black_hole_evidence"] is None
    conn = sqlite3.connect(f"file:{built['db_path']}?mode=ro&immutable=1", uri=True)
    (undecided,) = conn.execute(
        "SELECT gate_undecided FROM repositories WHERE owner = ? AND name = ?", (OWNER, OLD_SHAPE)).fetchone()
    conn.close()
    assert "D-GATE-5" in json.loads(undecided)
