"""Acceptance tests: ``python -m porchlamp.snapshot store <snapshot_dir> --db
<path>`` -- a command that scores a snapshot directory and writes it into a
store, end to end.

Two fixture shapes feed these tests:

* ``tests/fixtures/snapshot_50`` --
  proves the CLI on real data: counts, ``list_ranked``/``list_not_ranked``,
  ``snapshot_summary``, the served-DB guard, and (real-data bonus) that
  ``build_detectors`` genuinely fires on unmodified data (12 of 50 repos
  trip ``closed_unread``).
* A small, hand-built synthetic directory (:func:`_write_snapshot_dir`) --
  proves the ``previous``/``param_jump`` wiring and the ``expected.json``
  verify/``--no-verify`` contract without depending on real fixture
  content.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import pytest

from porchlamp.core import constants
from porchlamp.core.gates import GateEvidence
from porchlamp.core.models import FeatureBundle, Unmeasured, canonical_json, sha256_hex
from porchlamp.derive.bundle import dump_bundle, write_bundle
from porchlamp.snapshot import cli
from porchlamp.snapshot.populations import PopulationSet, write_populations
from porchlamp.store import queries, schema
from porchlamp.store.migrations import apply_migrations

from tests.store.helpers import make_bundle, make_measured

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "snapshot_50"

#: ``tests/fixtures/snapshot_50/expected.json``'s own pinned ``records_hash``
#: (regenerated 2026-09-16 from the sample corpus, so the committed bundles
#: and manifest are again what ``scripts/build_fixture_snapshot.py`` builds --
#: ``test_fixture_rebuild_is_byte_identical``): 12 of the 50 real repos
#: genuinely trip ``closed_unread``, 0 trip anything else on a first-ever
#: snapshot with no ``previous``. Restated here as a named constant so a
#: silent, unnoticed drift in that shared fixture file shows up as a
#: mismatch against ITS OWN expectation (this suite's own store calls
#: verify against ``expected.json`` directly -- see the module docstring
#: -- so this constant is redundant with that check by design, a second,
#: independent assertion of the same fact).
#:
#: Provenance must be relative to the data root; ``porchlamp.derive.bundle``
#: ensures relative sources for all bundles.
#:
#: Regenerated 2026-09-23 (was b364f71c...) by the CVI -> Porchlamp rename:
#: bundles carry the scoring code's module paths in Unmeasured details and the
#: populations document tag, so every bundle's raw_hash moved; no score did.
#:
#: Regenerated 2026-10-06 (was b52ccd66...) with D-GATE-5 reading the 1.4
#: gr14_ghost_rate input (owner ruling 2026-10-06): see the counts note below.
#:
#: Regenerated 2026-10-13 (was de07ceb8...) when stage 8 stopped scoring a
#: repository routed off-platform under the persona lenses: torvalds/linux
#: lost its persona composites and gates; no other record and no count moved.
#: Regenerated 2026-10-14 (was 507887cd...) for RFC-0008 (GATE5_WILSON_Z
#: added to porchlamp.core.constants, moving ScoreRecord.constants_hash).
_SNAPSHOT_50_RECORDS_HASH_WITH_DETECTORS = (
    "debe68b8836d004e484cb1123258262137188d46ad748795732290808e91e9c3"
)
# Gated/unrated reflect porchlamp.core.pipeline.not_ranked_class display classification.
#
# Regenerated 2026-09-16 (was rated 0 / gated 31 / unrated 19) after three
# gate corrections, each of which moves real repos in this fixture:
#   * D-GATE-5 now counts a merge inside the 14-day deadline as a substantive
#     response. Five repos that merge most external PRs on sight and leave no
#     review artefact -- boyter/scc merges 25 of its 26 external PRs, often
#     within the hour -- were being disqualified as `black_hole`. They are
#     now eligible, moving gated 31 -> 26.
#   * D-GATE-6 now applies its own 365-day horizon instead of inheriting the
#     PR window's. chillzhuang/SpringBlade's only decided external PR closed
#     in 2020 and no longer satisfies a "trailing 365 d" gate.
#   * torvalds/linux is now `unrated: off_platform_routed` rather than
#     `unrated: insufficient_evidence`: it is routed off-platform, which is
#     the accurate reason, not a shortage of evidence.
#
# Regenerated 2026-10-06 (was rated 0 / gated 26 / unrated 24): D-GATE-5 now
# reads the 1.4 gr14_ghost_rate input (pr_window envelope, bots excluded,
# D-IN-2 responses; owner ruling 2026-10-06) instead of the 30-PR v1
# pr_events sample. These frozen bundles predate that input (gr14 is
# Unmeasured in every one), so D-GATE-5 is UNDECIDED for all 50, and the
# eight repos the old rule gated `black_hole` (a-h/templ, abel533/Mapper,
# alexellis/k3sup, alibaba/canal, brunodev85/winlator,
# bytecodealliance/rustix, charmbracelet/lipgloss, dtolnay/anyhow) are now
# `unrated: insufficient_evidence`.
_SNAPSHOT_50_EXPECTED_COUNTS = {"rated": 0, "gated": 18, "unrated": 32}

#: A curated-anchor, empty-PR-window ``GateEvidence`` that clears eligibility
#: regardless of the repo's other facts.
_ELIGIBLE_EVIDENCE = GateEvidence(
    manifest_found=None, is_curated_anchor=True, license_file_present=None,
    fork_parent_in_universe=None, vitality_commits_180d=5, off_platform_marker=None,
)
#: The manifest.json JSON form of :data:`_ELIGIBLE_EVIDENCE`
_GATE_EVIDENCE_JSON = asdict(_ELIGIBLE_EVIDENCE)


def _all_unmeasured_inputs() -> dict:
    """Every registered parameter input id, each ``Unmeasured``."""
    return {
        input_id: Unmeasured(reason="collector_failure", detail="synthetic fixture")
        for input_id in constants.PARAM_INPUT_IDS
    }


def _make_synthetic_bundle(name: str, dr90_value: Optional[float], dr90_n: int = 20) -> FeatureBundle:
    """One repo, eligible, coverage-poor except (optionally) a real
    ``dr90_decision_rate`` measurement -- the one parameter this file uses
    to trip ``param_jump`` (``porchlamp.core.params.responsiveness.dr90``:
    ``min(100, 125*rate)``, ``min_n=10``; ``porchlamp.verify.detectors.
    PARAM_JUMP_MIN_N`` is also 10, ``PARAM_JUMP_THRESHOLD_POINTS`` 40)."""
    inputs = _all_unmeasured_inputs()
    if dr90_value is not None:
        inputs["dr90_decision_rate"] = make_measured(dr90_value, n=dr90_n)
    return make_bundle("synth", name, inputs=inputs)


def _bundle_raw_hash(bundle: FeatureBundle) -> str:
    return sha256_hex(canonical_json(dump_bundle(bundle)))


def _write_snapshot_dir(
    directory: Path,
    *,
    snapshot_id: str,
    as_of: str,
    bundles: list,
    expected: Optional[dict] = None,
) -> None:
    """Write a snapshot directory in exactly the shape ``porchlamp.snapshot.
    fixture_io.load_frozen_fixture`` reads (the same one ``scripts/
    build_fixture_snapshot.py::write_fixture`` produces): ``bundles/
    <owner>__<name>.json``, ``populations.json`` (empty -- none of this
    file's synthetic bundles use an ECDF-kind input), ``manifest.json``,
    and an optional ``expected.json``."""
    bundles_dir = directory / "bundles"
    bundles_dir.mkdir(parents=True, exist_ok=True)
    repos_manifest = []
    for bundle in bundles:
        basename = f"{bundle.repo.owner}__{bundle.repo.name}.json"
        write_bundle(bundle, bundles_dir / basename)
        repos_manifest.append({
            "owner": bundle.repo.owner,
            "name": bundle.repo.name,
            "bundle_file": f"bundles/{basename}",
            "raw_hash": _bundle_raw_hash(bundle),
            "gate_evidence": dict(_GATE_EVIDENCE_JSON),
        })
    write_populations(
        PopulationSet(
            snapshot_id=snapshot_id, benchmark_version=constants.BENCHMARK_VERSION,
            constants_hash=constants.CONSTANTS_HASH, populations=(),
        ),
        directory / "populations.json",
    )
    (directory / "manifest.json").write_text(
        json.dumps({"snapshot_id": snapshot_id, "as_of": as_of, "repos": repos_manifest}, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    if expected is not None:
        (directory / "expected.json").write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# =========================================================================== #
# tests/fixtures/snapshot_50 -- real data, end to end
# =========================================================================== #

def test_store_snapshot_50_counts_and_hashes(tmp_path, capsys) -> None:
    db_path = tmp_path / "porchlamp.db"
    code = cli.main(["store", str(FIXTURE_DIR), "--db", str(db_path)])
    out = capsys.readouterr().out
    assert code == 0, out

    assert "snapshot snapshot_50" in out
    _c = _SNAPSHOT_50_EXPECTED_COUNTS
    assert f"rated {_c['rated']} gated {_c['gated']} unrated {_c['unrated']}" in out
    assert f"records_hash {_SNAPSHOT_50_RECORDS_HASH_WITH_DETECTORS}" in out
    assert "constants_hash " in out
    assert "snapshot_dump_hash " in out

    conn = schema.connect(db_path)
    summary = queries.snapshot_summary(conn, "snapshot_50")
    assert summary is not None
    assert summary["counts"] == {**_SNAPSHOT_50_EXPECTED_COUNTS, "total": 50}
    assert summary["canonical_dump_hash"] is not None  # finalized
    # no wall clock: as_of is the manifest's own frozen value, verbatim.
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert summary["as_of"] == manifest["as_of"]
    assert summary["benchmark_version"] == manifest["benchmark_version"]
    assert summary["constants_hash"] == manifest["constants_hash"]
    # build_detectors is really wired: this is real data, not injected.
    assert summary["detector_summary"] == {"closed_unread": 12}


def test_store_snapshot_50_list_ranked_and_not_ranked(tmp_path) -> None:
    db_path = tmp_path / "porchlamp.db"
    assert cli.main(["store", str(FIXTURE_DIR), "--db", str(db_path)]) == 0
    conn = schema.connect(db_path)

    rows, total = queries.list_ranked(conn, "snapshot_50")
    assert rows == []
    assert total == 0

    rows, counts, total = queries.list_not_ranked(conn, "snapshot_50")
    assert total == 50
    assert sum(counts.values()) == 50
    assert all(row["reason"] for row in rows)


def test_store_twice_is_immutability_error_and_db_unchanged(tmp_path, capsys) -> None:
    db_path = tmp_path / "porchlamp.db"
    assert cli.main(["store", str(FIXTURE_DIR), "--db", str(db_path)]) == 0
    hash_after_first = _file_sha256(db_path)

    code = cli.main(["store", str(FIXTURE_DIR), "--db", str(db_path)])
    out = capsys.readouterr().out
    assert code == 1
    assert "already has repositories rows" in out or "immutable" in out

    assert _file_sha256(db_path) == hash_after_first


def test_served_db_guard_accepts_this_db(tmp_path) -> None:
    """`03` section 5 / the packet dispatch: the served-DB guard must not
    refuse this command's real output.

    ``porchlamp/serve/guard.py`` verification --
    this test calls its real ``v2_refusal_reasons`` against the database
    this command just wrote and finalised (``store_cmd.py`` calls
    ``finalize_snapshot_hash`` itself, ``promoted_at=as_of_text`` -- no
    wall clock), and asserts it accepts a clean, real, freshly-written
    store: no fixture-clock stamp (this command never writes one), no
    hash tamper (nothing has touched the file since ``finalize_snapshot_
    hash`` ran)."""
    from porchlamp.serve import guard

    db_path = tmp_path / "porchlamp.db"
    assert cli.main(["store", str(FIXTURE_DIR), "--db", str(db_path)]) == 0

    assert guard.v2_refusal_reasons(str(db_path)) == []
    assert guard.run_guard(str(db_path)) is None  # no v1 shape in a store-CLI-written db


def test_create_app_serves_snapshot_and_not_ranked(tmp_path) -> None:
    """Tests that create_app serves /v1/porchlamp/snapshot and /v1/porchlamp/not-ranked."""
    try:
        from porchlamp.serve.app import create_app
    except ImportError:
        pytest.skip("porchlamp/serve/app.py not available")
        return

    db_path = tmp_path / "porchlamp.db"
    assert cli.main(["store", str(FIXTURE_DIR), "--db", str(db_path)]) == 0

    from starlette.testclient import TestClient

    app = create_app(str(db_path), web_dir=tmp_path)  # no v2 web build needed for these two endpoints
    with TestClient(app) as client:
        snapshot_response = client.get("/v1/porchlamp/snapshot")
        assert snapshot_response.status_code == 200, snapshot_response.text
        assert snapshot_response.json()["snapshot_id"] == "snapshot_50"

        not_ranked_response = client.get("/v1/porchlamp/not-ranked")
        assert not_ranked_response.status_code == 200, not_ranked_response.text
        body = not_ranked_response.json()
        assert len(body["gated"]) + len(body["unrated"]) == 50


# =========================================================================== #
# synthetic snapshot directories -- previous/param_jump, verify/--no-verify
# =========================================================================== #

def test_previous_wires_param_jump_suspicion(tmp_path) -> None:
    """A 30-repo synthetic snapshot, built the way ``tests/store/
    helpers.py`` builds records (here: bundles, since this exercises the
    CLI's own directory-loading + scoring path, not a hand-built
    ``ScoreRecord``) -- one repo's ``dr90_decision_rate`` swings from a
    measured 0.05 (score ~6.25) to 0.95 (score 100, clamped), n=20 both
    sides: > PARAM_JUMP_THRESHOLD_POINTS (40) with both sides >=
    PARAM_JUMP_MIN_N (10). Every other repo (and every other parameter on
    the target repo) is untouched between the two directories, so this is
    the one and only ``suspicion`` entry anywhere in the result."""
    previous_dir = tmp_path / "previous"
    current_dir = tmp_path / "current"

    previous_bundles = [_make_synthetic_bundle(f"repo{i}", dr90_value=None) for i in range(30)]
    previous_bundles[0] = _make_synthetic_bundle("repo0", dr90_value=0.05, dr90_n=20)
    current_bundles = [_make_synthetic_bundle(f"repo{i}", dr90_value=None) for i in range(30)]
    current_bundles[0] = _make_synthetic_bundle("repo0", dr90_value=0.95, dr90_n=20)

    _write_snapshot_dir(previous_dir, snapshot_id="synthetic-previous", as_of="2026-01-01T00:00:00+00:00",
                         bundles=previous_bundles)
    _write_snapshot_dir(current_dir, snapshot_id="synthetic-current", as_of="2026-02-01T00:00:00+00:00",
                         bundles=current_bundles)

    db_path = tmp_path / "porchlamp.db"
    code = cli.main(["store", str(current_dir), "--db", str(db_path), "--previous", str(previous_dir)])
    assert code == 0

    conn = schema.connect(db_path)
    rows = conn.execute(
        "SELECT name, suspicion FROM repositories WHERE snapshot_id = ? ORDER BY name",
        ("synthetic-current",),
    ).fetchall()
    assert len(rows) == 30
    suspicion_by_name = {name: json.loads(raw) for name, raw in rows}
    assert suspicion_by_name["repo0"] == ["param_jump"]
    for i in range(1, 30):
        assert suspicion_by_name[f"repo{i}"] == [], f"repo{i} should carry no suspicion"


def test_expected_json_mismatch_exits_1_without_no_verify(tmp_path) -> None:
    snapshot_dir = tmp_path / "snap"
    bundles = [_make_synthetic_bundle("only", dr90_value=0.5, dr90_n=20)]
    _write_snapshot_dir(
        snapshot_dir, snapshot_id="verify-test", as_of="2026-01-01T00:00:00+00:00", bundles=bundles,
        expected={"records_hash": "0" * 64},  # a corrupted/stale hash, guaranteed to mismatch
    )
    db_path = tmp_path / "porchlamp.db"

    code = cli.main(["store", str(snapshot_dir), "--db", str(db_path)])
    assert code == 1
    assert not db_path.exists()  # verification runs before any DB is opened/created


def test_expected_json_mismatch_bypassed_by_no_verify(tmp_path) -> None:
    snapshot_dir = tmp_path / "snap"
    bundles = [_make_synthetic_bundle("only", dr90_value=0.5, dr90_n=20)]
    _write_snapshot_dir(
        snapshot_dir, snapshot_id="verify-test-2", as_of="2026-01-01T00:00:00+00:00", bundles=bundles,
        expected={"records_hash": "0" * 64},
    )
    db_path = tmp_path / "porchlamp.db"

    code = cli.main(["store", str(snapshot_dir), "--db", str(db_path), "--no-verify"])
    assert code == 0
    conn = schema.connect(db_path)
    assert queries.snapshot_summary(conn, "verify-test-2") is not None


def test_expected_json_match_exits_0(tmp_path) -> None:
    """The positive control for the two tests above: a correct
    ``expected.json`` (the real ``records_hash`` this directory scores to,
    computed independently via ``porchlamp.snapshot.store_cmd.score_snapshot_dir``)
    stores cleanly with no ``--no-verify`` needed."""
    from porchlamp.snapshot.store_cmd import score_snapshot_dir

    snapshot_dir = tmp_path / "snap"
    bundles = [_make_synthetic_bundle("only", dr90_value=0.5, dr90_n=20)]
    _write_snapshot_dir(snapshot_dir, snapshot_id="verify-test-3", as_of="2026-01-01T00:00:00+00:00", bundles=bundles)
    scored, _frozen = score_snapshot_dir(snapshot_dir)
    (snapshot_dir / "expected.json").write_text(
        json.dumps({"records_hash": scored.records_hash}) + "\n", encoding="utf-8"
    )

    db_path = tmp_path / "porchlamp.db"
    code = cli.main(["store", str(snapshot_dir), "--db", str(db_path)])
    assert code == 0


# =========================================================================== #
# write_scored_snapshot -- one transaction spanning write_snapshot + write_records
# =========================================================================== #

def test_write_scored_snapshot_is_one_transaction(tmp_path, monkeypatch) -> None:
    """Atomicity guarantee: writes in one transaction with write_snapshot +
    write_records. If ``write_records`` fails for ANY reason after
    ``write_snapshot`` has already run inside the same call, nothing for
    that snapshot id exists afterwards -- not even a lone ``snapshots``
    row.

    This is the test the "write outside the transaction" mutant (remove
    this function's own ``conn.execute("BEGIN")``/``rollback()`` wrapping
    and call ``write_snapshot``/``write_records`` back to back, each
    managing its own separate transaction) turns red: without the
    wrapping, ``write_snapshot``'s insert is already committed in its own
    transaction by the time the induced ``write_records`` failure occurs,
    so the orphaned ``snapshots`` row survives and the assertion below
    fails."""
    from porchlamp.snapshot import store_cmd

    db_path = tmp_path / "porchlamp.db"
    conn = schema.connect(db_path)
    apply_migrations(conn, applied_at="2026-01-01T00:00:00Z")

    snapshot_dir = tmp_path / "snap"
    bundles = [_make_synthetic_bundle("only", dr90_value=0.5, dr90_n=20)]
    _write_snapshot_dir(snapshot_dir, snapshot_id="atomic-test", as_of="2026-01-01T00:00:00+00:00", bundles=bundles)
    scored, frozen = store_cmd.score_snapshot_dir(snapshot_dir)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("induced write_records failure")

    monkeypatch.setattr(store_cmd, "write_records", _boom)

    with pytest.raises(RuntimeError, match="induced write_records failure"):
        store_cmd.write_scored_snapshot(conn, db_path, scored, frozen, code_commit="deadbeef")

    row = conn.execute("SELECT COUNT(*) FROM snapshots WHERE id = ?", (scored.snapshot_id,)).fetchone()
    assert row[0] == 0, "write_snapshot's insert must not survive write_records's failure"


def test_manifest_root_of_includes_populations_hash_and_matches_manifest() -> None:
    """E-01: manifest_root_of(frozen) must include populations.json's sha256 so
    it matches manifest.json['merkle_root']."""
    from dataclasses import replace

    from porchlamp.snapshot.fixture_io import load_frozen_fixture
    from porchlamp.snapshot.store_cmd import manifest_root_of

    frozen = load_frozen_fixture(FIXTURE_DIR)
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert frozen.populations_hash is not None
    assert manifest_root_of(frozen) == manifest["merkle_root"]
    assert manifest_root_of(replace(frozen, populations_hash="0" * 64)) != manifest["merkle_root"]

