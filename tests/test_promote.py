"""Tests for scripts/promote.py: verified-bundle -> OperationalStore promotion.

Focus: the promote path must materialize the spec 12.2 deep-dive payload so the
UI's raw-metrics table, survival curve, and maintainer playbook render on real
data (the repo-detail endpoint degrades to pillars-only without it).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from porchlamp.collect.envelope import write_envelope
from porchlamp.lakehouse import OperationalStore, repositories_dump_hash
from porchlamp.snapshot.manifest import build_manifest, envelope_entries, write_manifest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("promote", ROOT / "scripts" / "promote.py")
promote = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(promote)

_verify_spec = importlib.util.spec_from_file_location("verify", ROOT / "scripts" / "verify.py")
verify = importlib.util.module_from_spec(_verify_spec)
_verify_spec.loader.exec_module(verify)

_derive_spec = importlib.util.spec_from_file_location("derive", ROOT / "scripts" / "derive.py")
derive = importlib.util.module_from_spec(_derive_spec)
_derive_spec.loader.exec_module(derive)

NOW = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)

#: A real repository's envelopes, used to build genuine, non-placeholder
#: manifests (round 3, ): round 2's ``build_manifest([], ...)``
#: covered zero envelopes, which is exactly the shape that hid 
#: from every test that used it.  these are committed
#: under ``tests/fixtures/`` (32,721 bytes, numpy/numpy's own envelopes
#: copied verbatim from the read-only sample) rather than read from
#: ``~/Projects/cvi-scratch/sample-200``, which is absent on any machine but
#: this one -- including CI -- and turned 20 of this file's tests from
#: FAIL to a clean run everywhere.
SAMPLE_RAW = Path(__file__).resolve().parent / "fixtures" / "numpy-envelopes"
SAMPLE_OWNER, SAMPLE_NAME, SAMPLE_ECO = "numpy", "numpy", "pypi"
SAMPLE_KEY = f"{SAMPLE_OWNER}__{SAMPLE_NAME}"


def _bundle(owner="acme", name="widget", eco="pypi"):
    created = (NOW - timedelta(days=10)).isoformat()
    merged = (NOW - timedelta(days=9)).isoformat()
    reviewed = (NOW - timedelta(days=9, hours=20)).isoformat()
    return {
        "meta": {
            "owner": owner, "name": name, "ecosystem": eco,
            "stars": 5000, "forks": 400, "monthly_downloads": 100000,
            "dependents_count": 2000, "elephant_factor": 3,
            "legal_ip_model": "DCO", "ai_posture": "REGULATED",
            "scorecard_score": 8.5, "active_nonbot_commits_180d": 250,
            "off_platform_reviews": False, "hard_ai_ban": False,
            "promotion_ladder": "DOCUMENTED_PATHWAY", "mean_inline_comments": 3.0,
            "active_unclaimed_gfis": 8, "rr_90": 0.6, "dx_ci_minutes": 12.0,
            "openssf_signals": None,
        },
        "pr_events": [
            {
                "repo_owner": owner, "repo_name": name, "pr_number": 1,
                "created_at": created, "author_login": "dev",
                "author_association": "NONE", "author_prior_merged_commits": 0,
                "closed_at": merged, "merged_at": merged, "is_external": True,
                "ci_status_green": True, "first_human_review_at": reviewed,
                "response_latency_hours": 4.0, "maintainer_review_comments": 2,
            }
        ],
    }


def _deep_dive_json(db_path: str, owner: str, name: str):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT deep_dive_json FROM repositories WHERE owner=? AND name=?",
            (owner, name),
        ).fetchone()
    finally:
        conn.close()
    return None if row is None or row[0] is None else json.loads(row[0])


def test_promote_materializes_deep_dive(tmp_path):
    db = str(tmp_path / "porchlamp.db")
    n = promote.promote([_bundle()], db, NOW, tmp_path)
    assert n == 1
    dd = _deep_dive_json(db, "acme", "widget")
    assert dd is not None, "promote must populate deep_dive_json for the UI"
    # The UI reads exactly these sections (web/index.html).
    assert set(dd) >= {"raw_metrics", "survival_curve", "maintainer_playbook",
                       "legal_ip", "elephant_breakdown", "good_first_issues"}
    assert set(dd["raw_metrics"]) >= {"ttfhr_hours", "ghost_rate_14d",
                                      "first_timer_merge_rate",
                                      "tail_merge_latency_days"}
    # Playbook is a dict with an actions list (the double-count fix keeps it sane).
    pb = dd["maintainer_playbook"]
    assert isinstance(pb["actions"], list)
    assert pb["potential_porchlamp"] <= 100.0


def test_promote_deep_dive_survives_empty_pr_events(tmp_path):
    # A repo with no PR events must still get a deep-dive (empty metrics), not
    # crash promotion or leave the column NULL.
    b = _bundle(owner="quiet", name="library")
    b["pr_events"] = []
    db = str(tmp_path / "porchlamp.db")
    assert promote.promote([b], db, NOW, tmp_path) == 1
    dd = _deep_dive_json(db, "quiet", "library")
    assert dd is not None
    # Kaplan-Meier baseline: with no events the curve is the single t=0, S=1 point.
    assert dd["survival_curve"] == [[0.0, 1.0]]


def test_missing_created_at_falls_back_to_as_of_not_the_wall_clock():
    """An event with no usable created_at is anchored to the snapshot time."""
    row = {
        "repo_owner": "acme", "repo_name": "widget", "pr_number": 7,
        "created_at": "", "author_login": "dev",
    }
    assert promote._pr_events([row], NOW)[0].created_at == NOW
    earlier = NOW - timedelta(days=1)
    assert promote._pr_events([row], earlier)[0].created_at == earlier


# --------------------------------------------------------------------------- #
# , : `scripts/` is not a package. Running promote.py by path
# (as its own Usage line and scripts/serve.py's own refusal message both tell
# an operator to) must not depend on the repo root already being on
# sys.path -- that is exactly the layout a fresh `python scripts/promote.py`
# invocation has.
# --------------------------------------------------------------------------- #


def test_promote_help_runs_from_a_temp_cwd_with_only_porchlamp_importable(tmp_path):
    # Simulates the deployed layout: `porchlamp` is importable (however it got
    # there -- an editable install, in this worktree's case a symlink built
    # here so the test does not depend on which checkout the venv's
    # editable install happens to point at), but the repo root/`scripts` is
    # NOT separately on sys.path. Before the fix, `from scripts.verify
    # import ...` crashed here with "ModuleNotFoundError: No module named
    # 'scripts'" -- reproduced below on the round-3 source, byte for byte.
    only_porchlamp = tmp_path / "only_porchlamp_on_path"
    only_porchlamp.mkdir()
    (only_porchlamp / "porchlamp").symlink_to(ROOT / "porchlamp")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(only_porchlamp)

    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / "scripts" / "promote.py"), "--help"],
        cwd=str(cwd), env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr
    assert "usage: promote.py" in result.stdout


# --------------------------------------------------------------------------- #
#  promotion is a pure function of its inputs --
# the repositories table is truncated first, so no prior write survives it.
# --------------------------------------------------------------------------- #


def test_promote_atomically_replaces_a_stray_row_not_merges_with_it(tmp_path):
    # regression, now proven at the atomic-swap level (round 4
    # subtracted promote()'s own in-place truncate -- W-001): a row from a
    # prior write (a seeded demo catalog, a stray manual insert) must not
    # coexist with what this promotion writes. The temp file
    # promote_atomically builds starts empty and REPLACES the old file
    # outright, so purity no longer depends on an in-place DELETE.
    db = str(tmp_path / "porchlamp.db")
    store = OperationalStore(db)
    store.connection.execute(
        "INSERT INTO repositories (owner, name, ecosystem, domain_category, "
        "stars, forks, openssf_criticality, elephant_factor, legal_ip_model, "
        "ai_posture, score_responsiveness, score_criticality, score_governance, "
        "score_rigor_ai, score_accessibility, leverage_score, friction_score, "
        "matrix_quadrant, overall_porchlamp, rank_tier) VALUES "
        "('stray', 'row', 'pypi', '', 1, 1, 0, 1, 'DCO', 'AD_HOC', "
        "0, 0, 0, 0, 0, 0, 0, 'SWEET_SPOT', 0, 'A')")
    store.connection.commit()
    store.close()

    manifest = _freeze_real(tmp_path)
    n = promote.promote_atomically([_bundle()], db, NOW, tmp_path, manifest)
    assert n == 1

    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT owner, name FROM repositories").fetchall()
    finally:
        conn.close()
    assert rows == [("acme", "widget")]  # the stray row is gone, not coexisting


# --------------------------------------------------------------------------- #
# /
# promotion requires a frozen snapshot, is bound to its envelope set, and
# must agree with its as_of.
# --------------------------------------------------------------------------- #


def _copy_sample_envelopes(data_dir: Path) -> None:
    """Copy numpy/numpy's real envelopes from the read-only sample into
    ``data_dir/raw/`` (round 3, )."""
    for collector_dir in sorted(p for p in SAMPLE_RAW.iterdir() if p.is_dir()):
        src = collector_dir / f"{SAMPLE_KEY}.json"
        if src.is_file():
            dest = data_dir / "raw" / collector_dir.name / f"{SAMPLE_KEY}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)


def _real_clean_bundle(data_dir: Path) -> dict:
    """Derive + verify numpy/numpy for real from a copy of the read-only
    sample's own envelopes; writes ``derived/`` and ``verified/`` and
    returns the clean bundle. A manifest frozen from the SAME ``data_dir``
    covers exactly this bundle's provenance."""
    _copy_sample_envelopes(data_dir)
    bundle = derive.derive_repo(SAMPLE_OWNER, SAMPLE_NAME, derive.Ecosystem(SAMPLE_ECO),
                                data_dir, fetch=False)
    (data_dir / "derived").mkdir(parents=True, exist_ok=True)
    (data_dir / "derived" / f"{SAMPLE_KEY}.json").write_text(
        json.dumps(bundle, sort_keys=True), encoding="utf-8")
    report = verify.verify_repo(SAMPLE_OWNER, SAMPLE_NAME, data_dir)
    assert report["clean"], f"sample bundle is not clean: {report['flags']}"
    return bundle


def _synthetic_clean_bundle(data_dir: Path, owner: str, name: str, eco: str = "pypi") -> dict:
    """Derive + verify a second, unrelated CLEAN bundle from one hand-written
    ``github_meta`` envelope -- lighter than a sample copy, used only as the
    SECOND, unaffected repo in a two-bundle manifest test."""
    key = f"{owner}__{name}"
    write_envelope(data_dir / "raw" / "github_meta" / f"{key}.json",
                   "github:repository", {"stars": 42, "forks": 3}, "2026-09-11T00:00:00Z")
    bundle = derive.derive_repo(owner, name, derive.Ecosystem(eco), data_dir, fetch=False)
    (data_dir / "derived").mkdir(parents=True, exist_ok=True)
    (data_dir / "derived" / f"{key}.json").write_text(
        json.dumps(bundle, sort_keys=True), encoding="utf-8")
    report = verify.verify_repo(owner, name, data_dir)
    assert report["clean"], f"synthetic bundle is not clean: {report['flags']}"
    return bundle


def _freeze_real(data_dir: Path, as_of: str = NOW.isoformat()) -> dict:
    """A real frozen manifest over ``data_dir``'s own ``raw/`` envelopes
    (round 3, ): replaces the round-2 ``build_manifest([], ...)``
    placeholder, the envelope-less shape that hid ."""
    scan = envelope_entries(data_dir)
    manifest = build_manifest(scan.entries, as_of=as_of, code_commit="unknown",
                              canonical_dump_hash="pending")
    write_manifest(data_dir, manifest)
    return manifest


def test_resolve_manifest_without_a_freeze_names_the_freeze_command(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        promote.resolve_manifest(tmp_path, "whatever-id")
    assert "porchlamp.snapshot freeze" in str(excinfo.value)
    assert str(tmp_path) in str(excinfo.value)


def test_resolve_manifest_with_explicit_snapshot_id(tmp_path):
    first = _freeze_real(tmp_path, as_of="2026-09-12T00:00:00+00:00")
    resolved = promote.resolve_manifest(tmp_path, first["snapshot_id"])
    assert resolved["snapshot_id"] == first["snapshot_id"]


def test_resolve_manifest_with_unknown_snapshot_id_names_the_freeze_command(tmp_path):
    _freeze_real(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        promote.resolve_manifest(tmp_path, "does-not-exist")
    assert "porchlamp.snapshot freeze" in str(excinfo.value)


def test_write_snapshot_meta_records_the_bound_identity(tmp_path):
    bundle = _real_clean_bundle(tmp_path)
    db = str(tmp_path / "porchlamp.db")
    promote.promote([bundle], db, NOW, tmp_path)
    manifest = _freeze_real(tmp_path)
    dump_hash = repositories_dump_hash(db)
    promote.write_snapshot_meta(db, manifest, dump_hash)

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT snapshot_id, as_of, benchmark_version, constants_hash, "
            "manifest_root, canonical_dump_hash, promoted_at FROM snapshot_meta"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == manifest["snapshot_id"]
    assert row[2] == manifest["benchmark_version"]
    assert row[3] == manifest["constants_hash"]
    assert row[4] == manifest["merkle_root"]
    assert row[5] == dump_hash
    assert row[1] == row[6] == manifest["as_of"]


def test_write_snapshot_meta_hash_ignores_its_own_row(tmp_path):
    # The circularity round 2 must avoid: snapshot_meta lives in the same
    # database file it is recording a hash about, so the hash it stores must
    # not include its own row -- otherwise every promoted-then-served
    # database would invalidate itself the instant the row is written.
    bundle = _real_clean_bundle(tmp_path)
    db = str(tmp_path / "porchlamp.db")
    promote.promote([bundle], db, NOW, tmp_path)
    manifest = _freeze_real(tmp_path)
    before = repositories_dump_hash(db)
    promote.write_snapshot_meta(db, manifest, before)
    after = repositories_dump_hash(db)
    assert after == before


def test_main_requires_snapshot_id_flag(monkeypatch, capsys, tmp_path):
    # : --snapshot-id has no default any more.
    monkeypatch.setattr(sys, "argv", ["promote.py", "--data-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as excinfo:
        promote.main()
    assert excinfo.value.code != 0
    err = capsys.readouterr().err
    assert "--snapshot-id" in err


def test_main_refuses_promotion_for_an_unknown_snapshot_id(tmp_path, monkeypatch):
    bundle = _real_clean_bundle(tmp_path)
    db = str(tmp_path / "porchlamp.db")
    monkeypatch.setattr(sys, "argv", [
        "promote.py", "--db", db, "--data-dir", str(tmp_path),
        "--snapshot-id", "does-not-exist", "--as-of", NOW.isoformat()])
    with pytest.raises(SystemExit) as excinfo:
        promote.main()
    assert "porchlamp.snapshot freeze" in str(excinfo.value)
    assert not Path(db).exists()
    del bundle  # only needed to prove a clean bundle existing doesn't matter


def test_main_promotes_and_binds_after_a_freeze(tmp_path, monkeypatch):
    _real_clean_bundle(tmp_path)
    db = str(tmp_path / "porchlamp.db")
    manifest = _freeze_real(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "promote.py", "--db", db, "--data-dir", str(tmp_path),
        "--snapshot-id", manifest["snapshot_id"], "--as-of", NOW.isoformat()])
    assert promote.main() == 0

    conn = sqlite3.connect(db)
    try:
        snap = conn.execute("SELECT snapshot_id, canonical_dump_hash FROM snapshot_meta").fetchone()
        rows = conn.execute("SELECT owner, name FROM repositories").fetchall()
    finally:
        conn.close()
    assert snap is not None
    assert snap[0] == manifest["snapshot_id"]
    assert snap[1] == repositories_dump_hash(db)
    assert rows == [(SAMPLE_OWNER, SAMPLE_NAME)]


# --------------------------------------------------------------------------- #
# ,  (`03` §5): "promotion reads only envelopes in the
# manifest" -- the manifest is the promotion boundary, not a label.
# --------------------------------------------------------------------------- #


def test_hollow_manifest_covers_zero_clean_bundles_and_is_refused(tmp_path, monkeypatch, capsys):
    # The review's exact reproduction: a hand-built manifest with ZERO
    # envelopes must cover nothing and refuse outright, not silently
    # promote the whole universe.
    _real_clean_bundle(tmp_path)
    hollow = build_manifest([], as_of="2026-01-01T00:00:00+00:00",
                            code_commit="unknown", canonical_dump_hash="pending")
    write_manifest(tmp_path, hollow)
    db = str(tmp_path / "porchlamp.db")
    monkeypatch.setattr(sys, "argv", [
        "promote.py", "--db", db, "--data-dir", str(tmp_path),
        "--snapshot-id", hollow["snapshot_id"], "--as-of", "2026-01-01T00:00:00Z"])
    rc = promote.main()
    assert rc == 1
    out = capsys.readouterr().out
    assert f"manifest {hollow['snapshot_id']} covers 0 of 1 clean bundles" in out
    assert not Path(db).exists()


def test_real_manifest_promotes_exactly_the_covered_bundle(tmp_path, monkeypatch):
    bundle = _real_clean_bundle(tmp_path)
    manifest = _freeze_real(tmp_path)
    covered, not_in_manifest = promote.split_by_manifest([bundle], tmp_path, manifest)
    assert covered == [bundle]
    assert not_in_manifest == []

    db = str(tmp_path / "porchlamp.db")
    monkeypatch.setattr(sys, "argv", [
        "promote.py", "--db", db, "--data-dir", str(tmp_path),
        "--snapshot-id", manifest["snapshot_id"], "--as-of", NOW.isoformat()])
    assert promote.main() == 0
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT owner, name FROM repositories").fetchall()
    finally:
        conn.close()
    assert rows == [(SAMPLE_OWNER, SAMPLE_NAME)]


def test_manifest_with_one_altered_hash_drops_exactly_that_bundle(tmp_path):
    bundle = _real_clean_bundle(tmp_path)
    manifest = _freeze_real(tmp_path)
    tampered = json.loads(json.dumps(manifest))
    altered = next(e for e in tampered["envelopes"]
                   if e["collector"] == "github_meta" and e["key"] == SAMPLE_KEY)
    altered["sha256"] = "0" * 64

    covered, not_in_manifest = promote.split_by_manifest([bundle], tmp_path, tampered)
    assert covered == []
    assert not_in_manifest == [bundle]

    # Over-correction guard: the UNALTERED manifest still covers it.
    covered_ok, not_in_manifest_ok = promote.split_by_manifest([bundle], tmp_path, manifest)
    assert covered_ok == [bundle]
    assert not_in_manifest_ok == []


def test_bundle_without_a_recorded_hash_is_not_covered_by_any_manifest(tmp_path):
    # , W-002 (mutant M9): a provenance entry naming a real envelope
    # path that the manifest DOES list, but carrying no recorded sha256
    # ("claimed is None"), must fail closed -- there is nothing to compare,
    # which is not the same as a match. M9 mutated the coverage predicate
    # from `claimed is not None and manifest_index.get(...) == claimed` to
    # `claimed is None or manifest_index.get(...) == claimed`, which treats
    # a missing hash as an automatic match; with the suite otherwise green
    # that mutant let a HOLLOW manifest promote the whole universe again
    # (round 2's , restored) -- this bundle's "forks" reference
    # still resolves and matches, so ONLY the missing-hash arm is exercised.
    bundle = _real_clean_bundle(tmp_path)
    manifest = _freeze_real(tmp_path)
    del bundle["provenance"]["stars"]["sha256"]

    covered, not_in_manifest = promote.split_by_manifest([bundle], tmp_path, manifest)
    assert covered == []
    assert not_in_manifest == [bundle]


def test_manifest_covering_one_of_two_bundles_drops_only_that_one(tmp_path, monkeypatch, capsys):
    numpy_bundle = _real_clean_bundle(tmp_path)
    acme_bundle = _synthetic_clean_bundle(tmp_path, "acme", "widget")
    manifest = _freeze_real(tmp_path)

    manifest_path = tmp_path / "snapshots" / manifest["snapshot_id"] / "manifest.json"
    tampered = json.loads(manifest_path.read_text(encoding="utf-8"))
    altered = next(e for e in tampered["envelopes"]
                   if e["collector"] == "github_meta" and e["key"] == SAMPLE_KEY)
    altered["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(tampered), encoding="utf-8")

    covered, not_in_manifest = promote.split_by_manifest(
        [numpy_bundle, acme_bundle], tmp_path, tampered)
    assert covered == [acme_bundle]
    assert not_in_manifest == [numpy_bundle]

    db = str(tmp_path / "porchlamp.db")
    monkeypatch.setattr(sys, "argv", [
        "promote.py", "--db", db, "--data-dir", str(tmp_path),
        "--snapshot-id", manifest["snapshot_id"], "--as-of", NOW.isoformat()])
    assert promote.main() == 0
    out = capsys.readouterr().out
    assert "1 of 2 clean bundles not in manifest" in out
    conn = sqlite3.connect(db)
    try:
        rows = {r[0] for r in conn.execute("SELECT owner FROM repositories").fetchall()}
    finally:
        conn.close()
    assert rows == {"acme"}  # numpy dropped by the altered hash, acme/widget promoted


# --------------------------------------------------------------------------- #
# ,  (`03` §5): one snapshot names one reference time.
# --------------------------------------------------------------------------- #


def test_resolved_as_of_defaults_to_the_manifest(tmp_path):
    manifest = _freeze_real(tmp_path, as_of="2026-09-13T00:00:00+00:00")
    assert promote.resolved_as_of(None, manifest) == promote._dt(manifest["as_of"])


def test_resolved_as_of_accepts_an_agreeing_value(tmp_path):
    # "Z" and "+00:00" name the same instant; comparison is by value.
    manifest = _freeze_real(tmp_path, as_of="2026-09-13T00:00:00+00:00")
    assert promote.resolved_as_of("2026-09-13T00:00:00Z", manifest) == promote._dt(manifest["as_of"])


def test_resolved_as_of_rejects_a_disagreeing_value(tmp_path):
    manifest = _freeze_real(tmp_path, as_of="2026-09-13T00:00:00+00:00")
    with pytest.raises(SystemExit) as excinfo:
        promote.resolved_as_of("2020-01-01T00:00:00Z", manifest)
    assert "does not match manifest" in str(excinfo.value)
    assert "one snapshot, one reference time" in str(excinfo.value)


def test_main_refuses_as_of_disagreeing_with_the_manifest(tmp_path, monkeypatch):
    # The review's measured counter-example: --as-of 2020-01-01 bound to a
    # 2026 manifest used to be accepted, stamping rows with one time while
    # snapshot_meta silently recorded another, six years apart.
    _real_clean_bundle(tmp_path)
    manifest = _freeze_real(tmp_path, as_of="2026-09-13T00:00:00+00:00")
    db = str(tmp_path / "porchlamp.db")
    monkeypatch.setattr(sys, "argv", [
        "promote.py", "--db", db, "--data-dir", str(tmp_path),
        "--snapshot-id", manifest["snapshot_id"], "--as-of", "2020-01-01T00:00:00Z"])
    with pytest.raises(SystemExit) as excinfo:
        promote.main()
    assert "does not match manifest" in str(excinfo.value)
    assert not Path(db).exists()


def test_main_defaults_as_of_to_the_manifest_when_omitted(tmp_path, monkeypatch):
    _real_clean_bundle(tmp_path)
    manifest = _freeze_real(tmp_path, as_of="2026-09-13T00:00:00+00:00")
    db = str(tmp_path / "porchlamp.db")
    monkeypatch.setattr(sys, "argv", [
        "promote.py", "--db", db, "--data-dir", str(tmp_path),
        "--snapshot-id", manifest["snapshot_id"]])
    assert promote.main() == 0
    conn = sqlite3.connect(db)
    try:
        last_indexed_at = conn.execute(
            "SELECT last_indexed_at FROM repositories").fetchone()[0]
        promoted_at = conn.execute("SELECT promoted_at FROM snapshot_meta").fetchone()[0]
    finally:
        conn.close()
    assert last_indexed_at == manifest["as_of"]
    assert promoted_at == manifest["as_of"]


# --------------------------------------------------------------------------- #
# , item 4 (W-001, `03` §5): promotion is atomic -- it never rewrites
# the served file in place, so a failure leaves it byte-identical.
# --------------------------------------------------------------------------- #


def test_promote_atomically_leaves_the_served_db_untouched_on_failure(tmp_path, monkeypatch):
    # 's in-place `DELETE FROM repositories` (subtracted this round)
    # made a promotion PURE but not ATOMIC: a failure between the truncate
    # and the finished write left the live database rewritten and
    # unservable, with no rollback (W-001). promote_atomically builds into
    # a temp file and only os.replace()s it once every check -- including
    # this self-check, the LAST gate before the swap -- passes. Forcing the
    # self-check to fail proves every write up to that point (rows,
    # snapshot_meta) still leaves the SERVED file untouched.
    bundle = _real_clean_bundle(tmp_path)
    manifest = _freeze_real(tmp_path)
    db = tmp_path / "porchlamp.db"
    n = promote.promote_atomically([bundle], str(db), NOW, tmp_path, manifest)
    assert n == 1
    before = hashlib.sha256(db.read_bytes()).hexdigest()

    def _boom(store, db_path):
        return ["forced failure for the atomicity test"]

    monkeypatch.setattr(promote, "_serve_refusal_reasons", _boom)
    with pytest.raises(RuntimeError, match="forced failure"):
        promote.promote_atomically([bundle], str(db), NOW, tmp_path, manifest)

    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert after == before, "a failed promotion rewrote the served database"
    tmp_file = tmp_path / f"porchlamp.db.tmp-{manifest['snapshot_id']}"
    assert not tmp_file.exists(), "the failed attempt's temp file was not cleaned up"


def test_promote_atomically_cleans_up_a_stale_temp_file_from_a_prior_failure(tmp_path):
    # A temp file left behind by an earlier crash (before Python's own
    # finally block could run, or a killed process) must not be mistaken
    # for a completed promotion or block the next attempt.
    bundle = _real_clean_bundle(tmp_path)
    manifest = _freeze_real(tmp_path)
    db = tmp_path / "porchlamp.db"
    stale = tmp_path / f"porchlamp.db.tmp-{manifest['snapshot_id']}"
    stale.write_text("not a real database", encoding="utf-8")

    n = promote.promote_atomically([bundle], str(db), NOW, tmp_path, manifest)
    assert n == 1
    assert not stale.exists()  # replaced by a fresh build, then moved onto db

    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT owner, name FROM repositories").fetchall()
    finally:
        conn.close()
    assert rows == [(SAMPLE_OWNER, SAMPLE_NAME)]
